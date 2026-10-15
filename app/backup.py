"""Ежедневная копия базы в backups/, хранятся последние 14."""
import logging
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from app.db import Database

log = logging.getLogger(__name__)
KEEP = 14


def _backup_path(backups_dir: Path, today: date) -> Path:
    return backups_dir / f"data_{today:%Y-%m-%d}.db"


def backup_db(db: Database, backups_dir: Path, today: date, keep: int = KEEP) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _backup_path(backups_dir, today)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")
    dest = sqlite3.connect(str(tmp_path))
    try:
        with db.lock:
            db.conn.backup(dest)
        dest.close()
        os.replace(str(tmp_path), str(dest_path))
    except Exception:
        dest.close()
        tmp_path.unlink(missing_ok=True)
        raise
    for old in sorted(backups_dir.glob("data_*.db"))[:-keep]:
        old.unlink()
    log.info("Бэкап базы: %s", dest_path)
    return dest_path


class BackupScheduler:
    def __init__(self, db: Database, backups_dir: Path, clock=datetime.now, check_every_s: float = 3600.0):
        self._db, self._dir, self._clock, self._every = db, backups_dir, clock, check_every_s
        self._stop = threading.Event()

    def run_once(self) -> None:
        today = self._clock().date()
        if not _backup_path(self._dir, today).exists():
            backup_db(self._db, self._dir, today)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="backup", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("Бэкап не удался")
            self._stop.wait(self._every)
