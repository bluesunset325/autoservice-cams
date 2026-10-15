import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock

from app.backup import BackupScheduler, backup_db
from app.db import Database
from app.visits import VisitRepo


def make_db(tmp_path):
    db = Database(tmp_path / "data.db")
    VisitRepo(db).create("А123ВС77", "x", "box", datetime(2026, 10, 2, 10), "s.jpg")
    return db


def test_backup_copies_data(tmp_path):
    db = make_db(tmp_path)
    path = backup_db(db, tmp_path / "backups", date(2026, 10, 2))
    assert path.name == "data_2026-10-02.db"
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT plate FROM visits").fetchall()
        assert rows == [("А123ВС77",)]
    finally:
        conn.close()


def test_backup_keeps_last_14(tmp_path):
    db = make_db(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in range(1, 21):
        (backups / f"data_2026-09-{day:02d}.db").write_bytes(b"old")
    backup_db(db, backups, date(2026, 10, 2))
    names = sorted(p.name for p in backups.glob("data_*.db"))
    assert len(names) == 14
    assert names[-1] == "data_2026-10-02.db"
    assert names[0] == "data_2026-09-08.db"


def test_scheduler_backs_up_once_per_day(tmp_path):
    db = make_db(tmp_path)
    backups = tmp_path / "backups"
    sched = BackupScheduler(db, backups, clock=lambda: datetime(2026, 10, 2, 9))
    sched.run_once()
    first = (backups / "data_2026-10-02.db").stat().st_mtime_ns
    sched.run_once()
    assert (backups / "data_2026-10-02.db").stat().st_mtime_ns == first


def test_failed_backup_leaves_no_files(tmp_path):
    db = make_db(tmp_path)
    backups = tmp_path / "backups"

    # Mock the backup() method to raise an error
    broken_db = MagicMock()
    broken_db.conn = MagicMock()
    broken_db.conn.backup.side_effect = sqlite3.OperationalError("disk I/O error")
    broken_db.lock = db.lock

    try:
        backup_db(broken_db, backups, date(2026, 10, 2))
        assert False, "Should have raised"
    except sqlite3.OperationalError:
        pass

    # Verify no final file and no tmp file left
    assert not (backups / "data_2026-10-02.db").exists()
    assert not (backups / "data_2026-10-02.db.tmp").exists()


def test_scheduler_retries_after_failed_backup(tmp_path):
    db = make_db(tmp_path)
    backups = tmp_path / "backups"
    sched = BackupScheduler(db, backups, clock=lambda: datetime(2026, 10, 2, 9))

    # First attempt: mock to fail
    broken_db = MagicMock()
    broken_db.conn = MagicMock()
    broken_db.conn.backup.side_effect = sqlite3.OperationalError("disk I/O error")
    broken_db.lock = db.lock

    sched._db = broken_db
    try:
        sched.run_once()
    except sqlite3.OperationalError:
        pass

    # Verify no file created
    assert not (backups / "data_2026-10-02.db").exists()

    # Second attempt: use working db
    sched._db = db
    sched.run_once()

    # Verify file created on retry
    assert (backups / "data_2026-10-02.db").exists()
