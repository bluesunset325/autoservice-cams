from datetime import date, datetime

import pytest

from app.archive import RecordingRepo, StateRepo, drive_path, parse_segment_name
from app.db import Database

D = datetime(2026, 10, 3, 9, 0)


@pytest.fixture
def db():
    return Database(":memory:")


def test_parse_segment_name():
    assert parse_segment_name("2026-10-03_09-15-00.mp4") == datetime(2026, 10, 3, 9, 15)
    for bad in ("2026-10-03_09-15-00.mp4.tmp", "x.mp4", "2026-13-03_09-15-00.mp4", "2026-10-03_09-15-00.ts"):
        assert parse_segment_name(bad) is None


def test_drive_path():
    assert drive_path("Автосервис", "Бокс", datetime(2026, 10, 3, 9, 15, 7)) == "Автосервис/Бокс/2026-10-03/09-15-07.mp4"


def test_add_is_idempotent_and_status_flow(db):
    repo = RecordingRepo(db)
    assert repo.add("box", D, "2026-10-03_09-00-00.mp4", 100) is True
    assert repo.add("box", D, "2026-10-03_09-00-00.mp4", 100) is False
    [r] = repo.pending_upload()
    assert (r.camera, r.started_at, r.size, r.status) == ("box", D, 100, "local")
    repo.mark_uploaded(r.id, datetime(2026, 10, 3, 10), "DRIVEID", "Автосервис/Бокс/2026-10-03/09-00-00.mp4")
    r = repo.get(r.id)
    assert r.status == "both" and r.drive_id == "DRIVEID" and repo.pending_upload() == []
    assert r.drive_path == "Автосервис/Бокс/2026-10-03/09-00-00.mp4"
    assert repo.last_uploaded_at() == datetime(2026, 10, 3, 10)
    repo.mark_local_deleted(r.id, datetime(2026, 10, 5))
    assert repo.get(r.id).status == "drive"
    repo.mark_drive_deleted(r.id, datetime(2026, 11, 20))
    assert repo.get(r.id).status == "gone"


def test_queries(db):
    repo = RecordingRepo(db)
    repo.add("box", datetime(2026, 10, 2, 17, 45), "2026-10-02_17-45-00.mp4", 10)
    repo.add("box", datetime(2026, 10, 3, 9, 15), "2026-10-03_09-15-00.mp4", 20)
    repo.add("box", datetime(2026, 10, 3, 9, 0), "2026-10-03_09-00-00.mp4", 30)
    repo.add("office", datetime(2026, 10, 3, 9, 0), "2026-10-03_09-00-00.mp4", 40)
    assert [r.file for r in repo.for_day("box", date(2026, 10, 3))] == ["2026-10-03_09-00-00.mp4", "2026-10-03_09-15-00.mp4"]
    assert [r.size for r in repo.pending_upload()] == [10, 30, 40, 20]  # по времени
    assert repo.pending_stats() == (4, 100)
    first = repo.pending_upload()[0]
    repo.mark_uploaded(first.id, datetime(2026, 10, 3, 10), "A", "p")
    assert [r.id for r in repo.uploaded_on_disk()] == [first.id]
    assert [r.id for r in repo.drive_expired(datetime(2026, 10, 3))] == [first.id]
    assert repo.drive_expired(datetime(2026, 10, 2)) == []


def test_state_repo(db):
    s = StateRepo(db)
    assert s.get("k") is None
    s.set("k", "1")
    s.set("k", "2")
    assert s.get("k") == "2"


def test_migration_adds_drive_path_to_old_table(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT NOT NULL, "
        "started_at TEXT NOT NULL, file TEXT NOT NULL, size INTEGER NOT NULL, uploaded_at TEXT, drive_id TEXT, "
        "local_deleted_at TEXT, drive_deleted_at TEXT, UNIQUE (camera, file));"
        "INSERT INTO recordings (camera, started_at, file, size, uploaded_at, drive_id) "
        "VALUES ('box', '2026-10-03 09:00:00', '2026-10-03_09-00-00.mp4', 5, '2026-10-03 10:00:00', 'X');")
    conn.close()
    db = Database(path)
    repo = RecordingRepo(db)
    [r] = repo.uploaded_on_disk()
    assert r.drive_id == "X" and r.drive_path is None
    repo.mark_uploaded(r.id, datetime(2026, 10, 3, 10), "X", "a/b.mp4")
    assert repo.get(r.id).drive_path == "a/b.mp4"
    db.close()
    Database(path).close()           # повторное открытие: миграция не падает
