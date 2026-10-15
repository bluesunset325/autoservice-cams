"""Связывает запись, индексатор, загрузку и очистку архива."""
import logging
import shutil
import subprocess
import threading
from datetime import datetime

from app.archive import RecordingRepo, StateRepo
from app.cleanup import Cleanup
from app.indexer import index_segments
from app.recorder import Recorder, probe_video_codec
from app.uploader import Rclone, Uploader

log = logging.getLogger(__name__)
GB = 1024 ** 3


def _hm(dt: datetime | None) -> str | None:
    return dt.strftime("%H:%M") if dt else None


class ArchiveService:
    def __init__(self, cfg, db, popen=subprocess.Popen, rclone_popen=subprocess.Popen, clock=datetime.now,
                 disk_free=None, index_s: float = 60.0, probe=None):
        self._cfg = cfg
        self._disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        self.repo, self.state = RecordingRepo(db), StateRepo(db)
        self.rclone = Rclone(cfg, popen=rclone_popen)
        probe = probe or (lambda cam: probe_video_codec(cfg, cam))   # cam -> кодек, для метки hvc1
        self.recorder = Recorder(cfg, popen=popen, clock=clock, probe=probe)
        self.uploader = Uploader(cfg, self.repo, self.rclone, clock=clock, before_round=self.index_once)
        self.cleanup = Cleanup(cfg, self.repo, self.state, self.rclone, clock=clock, disk_free=self._disk_free)
        # индексатор в своём потоке: долгая загрузка (часы) не должна задерживать появление кусков в архиве
        self._index_s = index_s
        self._index_stop = threading.Event()
        self._index_thread: threading.Thread | None = None

    def index_once(self) -> int:
        return index_segments(self._cfg, self.repo, self.recorder.recording_now())

    def _index_loop(self) -> None:
        while not self._index_stop.is_set():
            try:
                self.index_once()
            except Exception:
                log.exception("Ошибка индексатора архива")
            self._index_stop.wait(self._index_s)

    def _stop_indexer(self) -> None:
        self._index_stop.set()
        t = self._index_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)

    def start(self) -> None:
        self._cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.recorder.start()
        self._index_thread = threading.Thread(target=self._index_loop, name="archive-indexer", daemon=True)
        self._index_thread.start()
        self.uploader.start()
        self.cleanup.start()
        log.info("Архив запущен: камеры %s", ", ".join(self.recorder.cameras) or "нет")

    def stop(self) -> None:
        # каждая остановка отдельно: ffmpeg и rclone не должны остаться, даже если что-то упало
        for name, step in (("индексатор", self._stop_indexer), ("загрузчик", self.uploader.stop),
                           ("очистка", self.cleanup.stop),
                           ("rclone", self.rclone.terminate), ("запись", self.recorder.stop)):
            try:
                step()
            except Exception:
                log.exception("Не удалось остановить: %s", name)

    def _camera_name(self, cid: str) -> str:
        cam = self._cfg.camera(cid)
        return cam.name if cam else cid

    def status(self) -> dict:
        up = self.uploader.status()
        try:
            free = self._disk_free(self._cfg.recordings_dir if self._cfg.recordings_dir.exists() else self._cfg.data_path)
        except OSError:
            free = 0
        return {
            "cameras": {cid: {"name": self._camera_name(cid), "state": s.state, "since": _hm(s.since),
                              "error": s.error}
                        for cid, s in self.recorder.statuses().items()},
            "queue_count": up.queue_count,
            "queue_gb": round(up.queue_bytes / GB, 1),
            "last_upload": _hm(up.last_ok_at),
            "upload_error": up.last_error,
            "upload_error_at": _hm(up.last_error_at),
            "cleanup_error": self.cleanup.last_error,
            "low_space": self.cleanup.low_space,
            "free_gb": round(free / GB, 1),
        }
