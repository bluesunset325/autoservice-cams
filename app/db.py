"""Подключение к SQLite и схема. Один connection на процесс, доступ под lock."""
import sqlite3
import threading
from datetime import datetime

TS_FMT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'owner', 'viewer')),
    created_at TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    plate TEXT NOT NULL,
    plate_raw TEXT NOT NULL,
    camera TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    exited_at TEXT,
    screenshot TEXT NOT NULL,
    edited_by TEXT,
    edited_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_visits_plate ON visits (plate);
CREATE INDEX IF NOT EXISTS ix_visits_entered ON visits (entered_at);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera TEXT NOT NULL,
    started_at TEXT NOT NULL,
    file TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_at TEXT,
    drive_id TEXT,
    drive_path TEXT,
    local_deleted_at TEXT,
    drive_deleted_at TEXT,
    UNIQUE (camera, file)
);
CREATE INDEX IF NOT EXISTS ix_recordings_cam_start ON recordings (camera, started_at);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def to_ts(dt: datetime | None) -> str | None:
    return dt.strftime(TS_FMT) if dt else None


def from_ts(s: str | None) -> datetime | None:
    return datetime.strptime(s, TS_FMT) if s else None


class Database:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        # recordings появилась раньше drive_path: у уже созданных data.db колонку добавляем
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(recordings)")}
        if "drive_path" not in cols:
            self.conn.execute("ALTER TABLE recordings ADD COLUMN drive_path TEXT")

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params=()) -> int:
        with self.lock:
            return self.conn.execute(sql, params).rowcount

    def insert(self, sql: str, params=()) -> int:
        with self.lock:
            return self.conn.execute(sql, params).lastrowid

    def close(self) -> None:
        with self.lock:
            self.conn.close()
