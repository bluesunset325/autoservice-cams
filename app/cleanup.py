"""Очистка: недельная и аварийная на компьютере, 45 дней на Google Диске."""
import logging
import shutil
import threading
import time
from datetime import datetime, timedelta

from app.archive import Recording, RecordingRepo, StateRepo, drive_path
from app.config import WEEKDAYS
from app.uploader import RcloneError

log = logging.getLogger(__name__)
GB = 1024 ** 3
NOT_FOUND_CODES = (3, 4)   # коды выхода rclone: папка не найдена, файл не найден


def _not_found(e: RcloneError) -> bool:
    return e.returncode in NOT_FOUND_CODES


class Cleanup:
    def __init__(self, cfg, repo: RecordingRepo, state: StateRepo, rclone, clock=datetime.now,
                 disk_free=None, every_s: float = 600.0, drive_every_s: float = 3600.0):
        self._cfg, self._repo, self._state, self._rclone, self._clock = cfg, repo, state, rclone, clock
        self._disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        self._every_s, self._drive_every_s = every_s, drive_every_s
        self._last_drive_run: float | None = None
        self.low_space = False
        self.weekly_error: str | None = None   # недельная очистка компьютера
        self.drive_error: str | None = None    # удаление старого с Google Диска
        self._stop = threading.Event()

    @property
    def last_error(self) -> str | None:
        return "; ".join(e for e in (self.weekly_error, self.drive_error) if e) or None

    def _local_path(self, r: Recording):
        return self._cfg.recordings_dir / r.camera / r.file

    def _delete_local(self, r: Recording, now: datetime) -> int:
        try:
            self._local_path(r).unlink(missing_ok=True)
        except OSError:
            log.exception("Не удалось удалить %s", self._local_path(r))
            return 0
        self._repo.mark_local_deleted(r.id, now)
        return 1

    def weekly_local(self) -> int:
        now = self._clock()
        if WEEKDAYS[now.weekday()] != self._cfg.recording.local_cleanup_weekday:
            return 0
        today = now.date().isoformat()
        if self._state.get("last_local_cleanup") == today:
            return 0
        recs = self._repo.uploaded_on_disk()
        n = sum(self._delete_local(r, now) for r in recs)
        failed = len(recs) - n
        if failed:
            self.weekly_error = f"Не удалось удалить с компьютера {failed} файлов"
            log.warning("Недельная очистка: не удалось удалить %s файлов, повтор в следующем цикле", failed)
        else:
            self._state.set("last_local_cleanup", today)
            self.weekly_error = None
        log.info("Недельная очистка: удалено с компьютера %s загруженных кусков", n)
        return n

    def emergency_local(self) -> int:
        now = self._clock()
        threshold = self._cfg.recording.low_space_gb * GB
        n = 0
        for r in self._repo.uploaded_on_disk():
            if self._disk_free(self._cfg.recordings_dir) >= threshold:
                break
            n += self._delete_local(r, now)
        self.low_space = self._disk_free(self._cfg.recordings_dir) < threshold
        if n:
            log.warning("Мало места: удалено раньше срока %s загруженных кусков", n)
        if self.low_space:
            log.warning("Мало места на диске, удалять больше нечего (незагруженные не удаляются)")
        return n

    def _fallback_path(self, r: Recording) -> str:
        """Для записей, загруженных до появления колонки drive_path."""
        cam = self._cfg.camera(r.camera)
        return drive_path(self._cfg.recording.drive_folder, cam.name if cam else r.camera, r.started_at)

    def drive_retention(self) -> int:
        now = self._clock()
        rec = self._cfg.recording
        n = 0
        failed = False
        for r in self._repo.drive_expired(now - timedelta(days=rec.drive_retention_days)):
            path = r.drive_path or self._fallback_path(r)
            try:
                self._rclone.deletefile(path)
            except RcloneError as e:
                missing = _not_found(e)
                if not missing:
                    try:
                        missing = self._rclone.file_id(path) is None
                    except RcloneError as e2:
                        missing = _not_found(e2)
                if not missing:
                    failed = True
                    self.drive_error = str(e)
                    log.warning("Не удалось удалить с Google Диска %s: %s", path, e)
                    break
            self._repo.mark_drive_deleted(r.id, now)
            n += 1
        if n:
            try:
                self._rclone.rmdirs(rec.drive_folder)
            except RcloneError as e:
                log.warning("rclone rmdirs: %s", e)
            log.info("Удалено с Google Диска старых кусков: %s", n)
        if not failed:
            self.drive_error = None
        return n

    def run_once(self) -> None:
        for stage in (self.weekly_local, self.emergency_local):
            try:
                stage()
            except Exception:
                log.exception("Ошибка этапа очистки %s", getattr(stage, "__name__", stage))
        mono = time.monotonic()
        if self._last_drive_run is None or mono - self._last_drive_run >= self._drive_every_s:
            self._last_drive_run = mono
            try:
                self.drive_retention()
            except Exception:
                log.exception("Ошибка удаления с Google Диска")

    def start(self) -> None:
        threading.Thread(target=self._loop, name="cleanup", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("Ошибка очистки архива")
            self._stop.wait(self._every_s)
