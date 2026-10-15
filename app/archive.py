"""Архив записей: таблица recordings и служебное состояние app_state."""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.db import Database, from_ts, to_ts

SEGMENT_FMT = "%Y-%m-%d_%H-%M-%S"
SEGMENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4$")


def parse_segment_name(name: str) -> datetime | None:
    if not SEGMENT_RE.match(name):
        return None
    try:
        return datetime.strptime(name[:-4], SEGMENT_FMT)
    except ValueError:
        return None


def drive_path(folder: str, camera_name: str, started_at: datetime) -> str:
    return f"{folder}/{camera_name}/{started_at:%Y-%m-%d}/{started_at:%H-%M-%S}.mp4"


@dataclass(frozen=True)
class Recording:
    id: int
    camera: str
    started_at: datetime
    file: str
    size: int
    uploaded_at: datetime | None
    drive_id: str | None
    local_deleted_at: datetime | None
    drive_deleted_at: datetime | None
    drive_path: str | None = None    # путь на Диске, куда кусок загружен

    @property
    def on_disk(self) -> bool:
        return self.local_deleted_at is None

    @property
    def on_drive(self) -> bool:
        return self.uploaded_at is not None and self.drive_deleted_at is None

    @property
    def status(self) -> str:
        if self.on_disk:
            return "both" if self.on_drive else "local"
        return "drive" if self.on_drive else "gone"


def _rec(row) -> Recording:
    return Recording(
        id=row["id"], camera=row["camera"], started_at=from_ts(row["started_at"]), file=row["file"],
        size=row["size"], uploaded_at=from_ts(row["uploaded_at"]), drive_id=row["drive_id"],
        local_deleted_at=from_ts(row["local_deleted_at"]), drive_deleted_at=from_ts(row["drive_deleted_at"]),
        drive_path=row["drive_path"],
    )


class RecordingRepo:
    def __init__(self, db: Database):
        self._db = db

    def add(self, camera: str, started_at: datetime, file: str, size: int) -> bool:
        return self._db.execute(
            "INSERT OR IGNORE INTO recordings (camera, started_at, file, size) VALUES (?, ?, ?, ?)",
            (camera, to_ts(started_at), file, size),
        ) == 1

    def get(self, rec_id: int) -> Recording | None:
        rows = self._db.query("SELECT * FROM recordings WHERE id = ?", (rec_id,))
        return _rec(rows[0]) if rows else None

    def for_day(self, camera: str, day: date) -> list[Recording]:
        start = datetime.combine(day, time())
        rows = self._db.query(
            "SELECT * FROM recordings WHERE camera = ? AND started_at >= ? AND started_at < ? ORDER BY started_at, id",
            (camera, to_ts(start), to_ts(start + timedelta(days=1))),
        )
        return [_rec(r) for r in rows]

    def pending_upload(self) -> list[Recording]:
        rows = self._db.query(
            "SELECT * FROM recordings WHERE uploaded_at IS NULL AND local_deleted_at IS NULL ORDER BY started_at, id")
        return [_rec(r) for r in rows]

    def pending_stats(self) -> tuple[int, int]:
        row = self._db.query(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM recordings "
            "WHERE uploaded_at IS NULL AND local_deleted_at IS NULL")[0]
        return row["n"], row["b"]

    def mark_uploaded(self, rec_id: int, at: datetime, drive_id: str, drive_path: str | None) -> None:
        self._db.execute("UPDATE recordings SET uploaded_at = ?, drive_id = ?, drive_path = ? WHERE id = ?",
                         (to_ts(at), drive_id, drive_path, rec_id))

    def uploaded_on_disk(self) -> list[Recording]:
        rows = self._db.query(
            "SELECT * FROM recordings WHERE uploaded_at IS NOT NULL AND local_deleted_at IS NULL "
            "ORDER BY started_at, id")
        return [_rec(r) for r in rows]

    def mark_local_deleted(self, rec_id: int, at: datetime) -> None:
        self._db.execute("UPDATE recordings SET local_deleted_at = ? WHERE id = ?", (to_ts(at), rec_id))

    def drive_expired(self, before: datetime) -> list[Recording]:
        rows = self._db.query(
            "SELECT * FROM recordings WHERE uploaded_at IS NOT NULL AND drive_deleted_at IS NULL "
            "AND started_at < ? ORDER BY started_at, id", (to_ts(before),))
        return [_rec(r) for r in rows]

    def mark_drive_deleted(self, rec_id: int, at: datetime) -> None:
        self._db.execute("UPDATE recordings SET drive_deleted_at = ? WHERE id = ?", (to_ts(at), rec_id))

    def last_uploaded_at(self) -> datetime | None:
        return from_ts(self._db.query("SELECT MAX(uploaded_at) AS m FROM recordings")[0]["m"])


class StateRepo:
    def __init__(self, db: Database):
        self._db = db

    def get(self, key: str) -> str | None:
        rows = self._db.query("SELECT value FROM app_state WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
