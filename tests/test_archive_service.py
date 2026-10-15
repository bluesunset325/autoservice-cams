import os
from datetime import datetime

from app.archive_service import ArchiveService
from app.config import AppConfig, CameraConfig
from app.db import Database

T = datetime(2026, 10, 3, 12, 40)


class FakeRclonePopen:
    def __init__(self, cmd, **kw):
        self.args, self.returncode = cmd, None

    def communicate(self, timeout=None):
        self.returncode = 0
        return "[]", ""

    def poll(self):
        return self.returncode


def make(tmp_path):
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True),
                             CameraConfig("office", "Офис", "rtsp://b")], base_dir=tmp_path)
    svc = ArchiveService(cfg, Database(":memory:"), popen=None, rclone_popen=FakeRclonePopen,
                         clock=lambda: T, disk_free=lambda p: 50 * 1024 ** 3)
    return cfg, svc


def test_index_once_uses_recorder_state(tmp_path):
    cfg, svc = make(tmp_path)
    d = cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    for n in ("2026-10-03_09-00-00.mp4", "2026-10-03_09-15-00.mp4"):
        (d / n).write_bytes(b"x" * 8192)
        os.utime(d / n, (0, 0))
    assert svc.index_once() == 2           # запись не идёт, новейший давно не менялся
    assert svc.repo.pending_stats() == (2, 16384)


def test_status_shape(tmp_path):
    cfg, svc = make(tmp_path)
    st = svc.status()
    assert st["cameras"] == {"box": {"name": "Бокс", "state": "idle", "since": None, "error": None}}
    assert (st["queue_count"], st["queue_gb"], st["last_upload"], st["upload_error"], st["low_space"]) == (0, 0.0, None, None, False)
    assert st["cleanup_error"] is None
    assert st["free_gb"] == 50.0


def test_stop_terminates_rclone_and_recorder_even_if_one_fails(tmp_path):
    cfg, svc = make(tmp_path)
    calls = []

    def boom():
        calls.append("uploader")
        raise RuntimeError("x")
    svc.uploader.stop = boom
    svc.rclone.terminate = lambda: calls.append("rclone")
    svc.recorder.stop = lambda: calls.append("recorder")
    svc.cleanup.stop = lambda: calls.append("cleanup")
    svc.stop()
    assert set(calls) == {"uploader", "cleanup", "rclone", "recorder"}


def _old_segments(cfg):
    d = cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    for n in ("2026-10-03_09-00-00.mp4", "2026-10-03_09-15-00.mp4"):
        (d / n).write_bytes(b"x" * 8192)
        os.utime(d / n, (0, 0))


def test_indexer_runs_in_own_thread_without_uploader(tmp_path):
    import time
    cfg, svc = make(tmp_path)
    _old_segments(cfg)
    svc._index_s = 0.01
    for part in (svc.recorder, svc.uploader, svc.cleanup):    # загрузчик занят и раунд не начинает
        part.start = lambda: None
    svc.start()
    for _ in range(300):
        if svc.repo.pending_stats()[0] == 2:
            break
        time.sleep(0.01)
    svc.stop()
    assert svc.repo.pending_stats() == (2, 16384)
    assert not svc._index_thread.is_alive()


def test_index_loop_survives_errors(tmp_path):
    cfg, svc = make(tmp_path)
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("boom")
        svc._index_stop.set()
        return 0
    svc.index_once = flaky
    svc._index_s = 0
    svc._index_loop()
    assert n["c"] == 2


def test_status_cleanup_error_uses_both(tmp_path):
    cfg, svc = make(tmp_path)
    svc.cleanup.weekly_error, svc.cleanup.drive_error = "w", "d"
    assert svc.status()["cleanup_error"] == "w; d"
