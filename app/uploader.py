"""Загрузка готовых кусков на Google Диск через rclone."""
import json
import logging
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.archive import RecordingRepo, drive_path
from app.config import resolve_executable

log = logging.getLogger(__name__)


class RcloneError(Exception):
    """returncode — код выхода rclone; None, если rclone не запустился или завис (таймаут)."""

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def bwlimit_arg(mbit: float) -> str:
    """rclone --bwlimit в байтах; настройка в мегабитах."""
    return f"{int(mbit * 1_000_000 / 8 / 1024)}K"


class Rclone:
    def __init__(self, cfg, popen=subprocess.Popen):
        rec = cfg.recording
        self._exe = resolve_executable(cfg.base_dir, rec.rclone_path)
        self._conf = str(Path(cfg.base_dir) / rec.rclone_config)
        self._remote = rec.rclone_remote
        self._bw = rec.upload_bwlimit_mbit
        self._popen = popen
        # один Rclone на загрузчик и очистку: запущенных процессов может быть несколько
        self._running: set = set()
        self._lock = threading.Lock()
        self._closed = False

    def _call(self, *args: str, timeout: float | None = None) -> str:
        cmd = [self._exe, "--config", self._conf, *args]
        with self._lock:
            if self._closed:
                raise RcloneError("rclone: программа останавливается")
            try:
                proc = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace")
            except OSError as e:
                raise RcloneError(f"rclone: {e}") from e
            self._running.add(proc)
        try:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as e:
                proc.kill()
                proc.communicate()
                raise RcloneError(f"rclone: {e}") from e
        finally:
            with self._lock:
                self._running.discard(proc)
        if proc.returncode != 0:
            raise RcloneError((err or out or "").strip()[-500:] or f"rclone: код {proc.returncode}",
                              returncode=proc.returncode)
        return out

    def terminate(self, wait_s: float = 5.0) -> None:
        """Остановить запущенные rclone (при выключении программы), новые не запускать."""
        with self._lock:
            self._closed = True
            procs = list(self._running)
        for proc in procs:
            try:
                if proc.poll() is not None:
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=wait_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError:
                log.exception("Не удалось остановить rclone")

    def _r(self, path: str) -> str:
        return f"{self._remote}{path}"

    def copyto(self, local: Path, path: str) -> None:
        self._call("copyto", str(local), self._r(path), "--bwlimit", bwlimit_arg(self._bw), "--retries", "3",
                   timeout=3 * 3600)

    def file_id(self, path: str) -> str | None:
        out = self._call("lsjson", "--files-only", "--original", self._r(path), timeout=120)
        try:
            items = json.loads(out or "[]")
            return items[0].get("ID") if items else None
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
            raise RcloneError(f"rclone lsjson: неожиданный ответ: {e}") from e

    def deletefile(self, path: str) -> None:
        self._call("deletefile", self._r(path), timeout=120)

    def rmdirs(self, path: str) -> None:
        self._call("rmdirs", "--leave-root", self._r(path), timeout=600)

    def about(self) -> str:
        return self._call("about", self._remote, timeout=60)


@dataclass(frozen=True)
class UploaderStatus:
    queue_count: int
    queue_bytes: int
    last_ok_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None


class Uploader:
    def __init__(self, cfg, repo: RecordingRepo, rclone, clock=datetime.now, retry_s: float = 300.0,
                 idle_s: float = 30.0, before_round=None):
        self._cfg, self._repo, self._rclone, self._clock = cfg, repo, rclone, clock
        self._retry_s, self._idle_s, self._before_round = retry_s, idle_s, before_round
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        self._stop = threading.Event()

    def run_once(self) -> bool:
        for r in self._repo.pending_upload():
            if self._stop.is_set():
                return True
            cam = self._cfg.camera(r.camera)
            path = drive_path(self._cfg.recording.drive_folder, cam.name if cam else r.camera, r.started_at)
            local = self._cfg.recordings_dir / r.camera / r.file
            if not local.is_file():
                log.warning("Файл записи пропал до загрузки: %s", local)
                self._repo.mark_local_deleted(r.id, self._clock())
                continue
            try:
                self._rclone.copyto(local, path)
                file_id = self._rclone.file_id(path)
            except RcloneError as e:
                return self._fail(str(e))
            if not file_id:
                return self._fail("Google Диск не вернул id файла")
            now = self._clock()
            self._repo.mark_uploaded(r.id, now, file_id, path)
            self._last_ok = now
        self._last_error, self._last_error_at = None, None
        return True

    def _fail(self, message: str) -> bool:
        log.warning("Загрузка на Google Диск: %s", message)
        self._last_error, self._last_error_at = message, self._clock()
        return False

    def status(self) -> UploaderStatus:
        count, size = self._repo.pending_stats()
        return UploaderStatus(count, size, self._last_ok or self._repo.last_uploaded_at(),
                              self._last_error, self._last_error_at)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="uploader", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._rclone.terminate()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok = False
            try:
                if self._before_round is not None:
                    self._before_round()
                ok = self.run_once()
            except Exception:
                log.exception("Ошибка загрузчика")
            self._stop.wait(self._idle_s if ok else self._retry_s)
