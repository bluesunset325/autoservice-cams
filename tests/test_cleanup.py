from datetime import datetime, timedelta

from app.archive import RecordingRepo, StateRepo, drive_path
from app.cleanup import Cleanup
from app.config import AppConfig, CameraConfig
from app.db import Database
from app.uploader import RcloneError

MON = datetime(2026, 10, 5, 9, 5)
GB = 1024 ** 3


class FakeRclone:
    def __init__(self, fail=False, exists=True):
        self.deleted, self.rmdirs_calls, self.fail, self.exists = [], [], fail, exists

    def deletefile(self, path):
        if self.fail:
            raise RcloneError("boom")
        self.deleted.append(path)

    def file_id(self, path):
        return "X" if self.exists else None

    def rmdirs(self, path):
        self.rmdirs_calls.append(path)


def setup(tmp_path, uploaded=(True, True, False)):
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True)], base_dir=tmp_path)
    d = cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    db = Database(":memory:")
    repo, state = RecordingRepo(db), StateRepo(db)
    for i, up in enumerate(uploaded):
        start = datetime(2026, 10, 1, 9, 0) + timedelta(minutes=15 * i)
        name = start.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
        (d / name).write_bytes(b"x")
        repo.add("box", start, name, 1)
        if up:
            repo.mark_uploaded(repo.pending_upload()[0].id, start, f"ID{i}", drive_path("Автосервис", "Бокс", start))
    return cfg, repo, state, d


def test_weekly_deletes_uploaded_once_on_monday(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON, disk_free=lambda p: 100 * GB)
    assert c.weekly_local() == 2
    assert sorted(p.name for p in d.iterdir()) == ["2026-10-01_09-30-00.mp4"]   # незагруженный остался
    assert repo.uploaded_on_disk() == [] and len(repo.pending_upload()) == 1
    assert state.get("last_local_cleanup") == "2026-10-05"
    assert c.weekly_local() == 0


def test_weekly_skips_other_days(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON + timedelta(days=1), disk_free=lambda p: 100 * GB)
    assert c.weekly_local() == 0 and len(list(d.iterdir())) == 3


def test_emergency_deletes_oldest_uploaded_until_enough(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    free = {"v": 15 * GB}

    def disk_free(p):
        return free["v"]
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON, disk_free=disk_free)
    orig_delete = c._delete_local

    def delete_and_free(r, now):
        n = orig_delete(r, now)
        free["v"] += 6 * GB
        return n
    c._delete_local = delete_and_free
    assert c.emergency_local() == 1
    assert [r.file for r in repo.uploaded_on_disk()] == ["2026-10-01_09-15-00.mp4"]
    assert c.low_space is False


def test_emergency_never_deletes_unuploaded(tmp_path):
    cfg, repo, state, d = setup(tmp_path, uploaded=(False, False, False))
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON, disk_free=lambda p: 1 * GB)
    assert c.emergency_local() == 0
    assert len(list(d.iterdir())) == 3 and c.low_space is True


def test_drive_retention(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    rc = FakeRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 11, 15, 12), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 2
    assert rc.deleted == ["Автосервис/Бокс/2026-10-01/09-00-00.mp4", "Автосервис/Бокс/2026-10-01/09-15-00.mp4"]
    assert rc.rmdirs_calls == ["Автосервис"]
    assert c.drive_retention() == 0


def test_drive_retention_not_yet(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: datetime(2026, 11, 14, 12), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0


def test_drive_delete_error_keeps_record_unless_missing(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    late = lambda: datetime(2026, 12, 1)
    c = Cleanup(cfg, repo, state, FakeRclone(fail=True, exists=True), clock=late, disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0 and c.last_error == "boom"
    c2 = Cleanup(cfg, repo, state, FakeRclone(fail=True, exists=False), clock=late, disk_free=lambda p: 100 * GB)
    assert c2.drive_retention() == 2      # файла уже нет на Диске — считаем удалённым


def test_weekly_retries_after_failed_unlink(tmp_path, monkeypatch):
    from pathlib import Path
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON, disk_free=lambda p: 100 * GB)
    bad = d / "2026-10-01_09-00-00.mp4"
    orig = Path.unlink

    def flaky(self, *a, **kw):
        if self == bad:
            raise PermissionError("locked")
        return orig(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", flaky)
    assert c.weekly_local() == 1
    assert state.get("last_local_cleanup") is None
    assert c.last_error == "Не удалось удалить с компьютера 1 файлов"
    monkeypatch.setattr(Path, "unlink", orig)
    assert c.weekly_local() == 1
    assert state.get("last_local_cleanup") == "2026-10-05"
    assert c.last_error is None
    assert sorted(p.name for p in d.iterdir()) == ["2026-10-01_09-30-00.mp4"]


class DirMissingRclone(FakeRclone):
    def deletefile(self, path):
        raise RcloneError("boom")

    def file_id(self, path):
        raise RcloneError("directory not found", returncode=3)


def test_drive_missing_folder_counts_as_deleted(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, DirMissingRclone(), clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 2


class OtherErrRclone(FakeRclone):
    def deletefile(self, path):
        raise RcloneError("boom")

    def file_id(self, path):
        raise RcloneError("network down")


def test_drive_other_lsjson_error_keeps_record(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, OtherErrRclone(), clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0 and c.last_error == "boom"


def test_last_error_cleared_after_success(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    late = lambda: datetime(2026, 12, 1)
    c = Cleanup(cfg, repo, state, FakeRclone(fail=True, exists=True), clock=late, disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0 and c.last_error == "boom"
    c._rclone = FakeRclone()
    assert c.drive_retention() == 2 and c.last_error is None


class SecondFailsRclone(FakeRclone):
    def deletefile(self, path):
        self.attempts = getattr(self, "attempts", []) + [path]
        if len(self.attempts) == 2:
            raise RcloneError("boom")
        self.deleted.append(path)


def test_drive_partial_progress(tmp_path):
    cfg, repo, state, d = setup(tmp_path, uploaded=(True, True, True))
    rc = SecondFailsRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 1
    assert len(rc.attempts) == 2
    assert rc.rmdirs_calls == ["Автосервис"]
    assert c.last_error == "boom"


def test_run_once_stages_independent(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    rc = FakeRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)

    def boom():
        raise RuntimeError("x")
    c.weekly_local = boom
    c.emergency_local = boom
    c.run_once()
    assert len(rc.deleted) == 2


def test_drive_retention_uses_stored_path_after_rename(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    cfg.cameras[0].name = "Бокс 2"
    rc = FakeRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 2
    assert rc.deleted == ["Автосервис/Бокс/2026-10-01/09-00-00.mp4", "Автосервис/Бокс/2026-10-01/09-15-00.mp4"]


class PathRecordingRclone(FakeRclone):
    def __init__(self):
        super().__init__(fail=True)
        self.looked_up = []

    def file_id(self, path):
        self.looked_up.append(path)
        return None


def test_drive_retention_file_id_fallback_uses_stored_path(tmp_path):
    cfg, repo, state, d = setup(tmp_path, uploaded=(True, False, False))
    cfg.cameras[0].name = "Бокс 2"
    rc = PathRecordingRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 1
    assert rc.looked_up == ["Автосервис/Бокс/2026-10-01/09-00-00.mp4"]


def test_drive_retention_recomputes_path_when_not_stored(tmp_path):
    cfg, repo, state, d = setup(tmp_path, uploaded=(False, False, False))
    r = repo.pending_upload()[0]
    repo.mark_uploaded(r.id, r.started_at, "OLD", None)
    rc = FakeRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: datetime(2026, 12, 1), disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 1
    assert rc.deleted == ["Автосервис/Бокс/2026-10-01/09-00-00.mp4"]


class CodeRclone(FakeRclone):
    def __init__(self, message, code):
        super().__init__()
        self.message, self.code = message, code

    def deletefile(self, path):
        raise RcloneError("boom")

    def file_id(self, path):
        raise RcloneError(self.message, returncode=self.code)


def test_drive_file_not_found_code_counts_as_deleted(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, CodeRclone("object not found", 4), clock=lambda: datetime(2026, 12, 1),
                disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 2


def test_drive_not_found_text_with_other_code_keeps_record(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, CodeRclone("Config file not found", 1), clock=lambda: datetime(2026, 12, 1),
                disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0
    assert len(repo.drive_expired(datetime(2026, 12, 1))) == 2 and c.last_error == "boom"


def test_drive_delete_error_without_code_is_not_missing(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, CodeRclone("file not found", None), clock=lambda: datetime(2026, 12, 1),
                disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 0


class DeleteNotFoundRclone(FakeRclone):
    def deletefile(self, path):
        raise RcloneError("object not found", returncode=4)

    def file_id(self, path):
        raise AssertionError("lsjson не нужен")


def test_deletefile_not_found_code_counts_as_deleted(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, DeleteNotFoundRclone(), clock=lambda: datetime(2026, 12, 1),
                disk_free=lambda p: 100 * GB)
    assert c.drive_retention() == 2 and c.last_error is None


def test_weekly_error_survives_successful_drive_pass(tmp_path, monkeypatch):
    from pathlib import Path
    cfg, repo, state, d = setup(tmp_path)
    cfg.recording.drive_retention_days = 1
    rc = FakeRclone()
    c = Cleanup(cfg, repo, state, rc, clock=lambda: MON, disk_free=lambda p: 100 * GB)

    def locked(self, *a, **kw):
        raise PermissionError("locked")
    monkeypatch.setattr(Path, "unlink", locked)
    c.run_once()
    assert len(rc.deleted) == 2                      # Диск почищен успешно
    assert c.weekly_error == "Не удалось удалить с компьютера 2 файлов" and c.drive_error is None
    assert c.last_error == "Не удалось удалить с компьютера 2 файлов"


def test_last_error_joins_both(tmp_path):
    cfg, repo, state, d = setup(tmp_path)
    c = Cleanup(cfg, repo, state, FakeRclone(), clock=lambda: MON, disk_free=lambda p: 100 * GB)
    assert c.last_error is None
    c.weekly_error, c.drive_error = "a", "b"
    assert c.last_error == "a; b"
