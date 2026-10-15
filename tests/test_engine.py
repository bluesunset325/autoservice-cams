from datetime import datetime, timedelta

import numpy as np

from app.capture import CameraStatus
from app.config import AppConfig, CameraConfig
from app.db import Database
from app.recognizer import PlateRead
from app.engine import Engine
from app.visits import VisitRepo

T0 = datetime(2026, 10, 2, 10, 0, 0)
FRAME = np.zeros((40, 60, 3), np.uint8)


def t(s):
    return T0 + timedelta(seconds=s)


class FakeRecognizer:
    def __init__(self):
        self.reads = []
        self.fail = False

    def read(self, frame):
        if self.fail:
            raise RuntimeError("boom")
        return self.reads


class FakeStream:
    def __init__(self, camera_id, url):
        self.camera_id, self.url = camera_id, url

    def start(self): pass
    def stop(self): pass
    def latest(self): return FRAME, T0
    def status(self): return CameraStatus(True, None)


def make(tmp_path, repo=None, clock=lambda: T0):
    cfg = AppConfig(
        cameras=[CameraConfig("box", "Бокс", "rtsp://cam1", True),
                 CameraConfig("office", "Офис", "rtsp://cam2")],
        base_dir=tmp_path,
    )
    repo = repo or VisitRepo(Database(":memory:"))
    rec = FakeRecognizer()
    return cfg, repo, rec, Engine(cfg, repo, rec, stream_factory=FakeStream, clock=clock)


def test_streams_use_go2rtc_restream(tmp_path):
    _, _, _, engine = make(tmp_path)
    assert engine._streams["box"].url == "rtsp://127.0.0.1:8554/box"
    assert set(engine.statuses()) == {"box", "office"}


def test_entry_creates_visit_with_screenshot_then_exit_closes_it(tmp_path):
    cfg, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 10, 10))]
    for s in (0, 1, 2):
        engine.process("box", FRAME, t(s), online=True)
    [v] = repo.open_visits("box")
    assert (v.plate, v.plate_raw, v.entered_at) == ("А123ВС77", "A123BC77", t(0))
    assert v.screenshot == "screenshot_2026-10-02_10-00-00.jpg"
    assert (cfg.screenshots_dir / v.screenshot).is_file()

    rec.reads = []
    engine.process("box", FRAME, t(182), online=True)  # последний раз видели в t(2), +180 с
    assert repo.open_visits("box") == []
    assert repo.get(v.id).exited_at == t(2)


def test_invalid_plates_are_ignored(tmp_path):
    _, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("ZZZ1", 0.9, (0, 0, 1, 1))]
    for s in range(5):
        engine.process("box", FRAME, t(s), online=True)
    assert repo.open_visits("box") == []


def test_offline_camera_is_not_recognized_and_does_not_exit(tmp_path):
    _, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 1, 1))]
    for s in (0, 1, 2):
        engine.process("box", FRAME, t(s), online=True)
    rec.fail = True  # если бы распознавание вызвалось при offline — тест бы упал на логе, но не на assert
    engine.process("box", None, t(500), online=False)
    assert len(repo.open_visits("box")) == 1


def test_resumes_open_visits_after_restart(tmp_path):
    repo = VisitRepo(Database(":memory:"))
    vid = repo.create("А123ВС77", "A123BC77", "box", t(0), "s.jpg")
    repo.touch(vid, t(5))
    _, _, rec, engine = make(tmp_path, repo=repo, clock=lambda: t(10))
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 1, 1))]
    engine.process("box", FRAME, t(16), online=True)
    [v] = repo.open_visits("box")
    assert v.id == vid and v.last_seen_at == t(16)


def test_recognizer_error_does_not_crash(tmp_path):
    _, repo, rec, engine = make(tmp_path)
    rec.fail = True
    engine.process("box", FRAME, t(0), online=True)
    assert repo.open_visits("box") == []


def test_screenshot_failure_creates_visit_with_empty_screenshot(tmp_path, monkeypatch):
    cfg, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 10, 10))]

    # Make save_screenshot fail
    def failing_save_screenshot(*args, **kwargs):
        raise OSError("no permission")
    monkeypatch.setattr("app.engine.save_screenshot", failing_save_screenshot)

    for s in (0, 1, 2):
        engine.process("box", FRAME, t(s), online=True)

    [v] = repo.open_visits("box")
    assert v.screenshot == ""
    assert (v.plate, v.plate_raw, v.entered_at) == ("А123ВС77", "A123BC77", t(0))

    # Exit should still work
    rec.reads = []
    monkeypatch.undo()
    engine.process("box", FRAME, t(182), online=True)
    assert repo.open_visits("box") == []


def test_persistent_recognizer_errors_do_not_close_visits(tmp_path, caplog):
    _, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 10, 10))]

    # Create a visit (requires 3 reads)
    for s in (0, 1, 2):
        engine.process("box", FRAME, t(s), online=True)
    [v] = repo.open_visits("box")

    # Now make recognizer fail for all future calls
    rec.fail = True
    for s in range(3, 400, 10):
        engine.process("box", FRAME, t(s), online=True)

    # Visit should still be open
    assert len(repo.open_visits("box")) == 1
    # Only one error should be logged for this error streak
    error_logs = [r for r in caplog.records if "Ошибка распознавания" in r.message]
    assert len(error_logs) == 1


def test_stale_frames_skipped(tmp_path):
    """Same frame is not processed multiple times."""
    cfg, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 1, 1))]

    class SameFrameStream:
        def __init__(self, camera_id, url):
            self.camera_id, self.url = camera_id, url
        def start(self): pass
        def stop(self): pass
        def latest(self): return FRAME, T0  # Same (frame, time) every call
        def status(self): return CameraStatus(True, None)

    engine._streams["box"] = SameFrameStream("box", "rtsp://127.0.0.1:8554/box")

    # Call _tick three times with same frame — should not create visit
    for _ in range(3):
        engine._tick("box")

    assert repo.open_visits("box") == []


def test_advancing_frames_create_visit(tmp_path):
    """Different frames create visit on tracker threshold."""
    cfg, repo, rec, engine = make(tmp_path)
    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 1, 1))]

    frames_and_times = [(FRAME, t(0)), (FRAME, t(1)), (FRAME, t(2))]
    call_count = [0]

    class AdvancingFrameStream:
        def __init__(self, camera_id, url):
            self.camera_id, self.url = camera_id, url
        def start(self): pass
        def stop(self): pass
        def latest(self):
            idx = call_count[0]
            if idx < len(frames_and_times):
                return frames_and_times[idx]
            return FRAME, t(100)
        def status(self): return CameraStatus(True, None)

    engine._streams["box"] = AdvancingFrameStream("box", "rtsp://127.0.0.1:8554/box")

    # Call _tick three times with advancing frame times
    for _ in range(3):
        engine._tick("box")
        call_count[0] += 1

    # After 3 different frames, visit should exist
    assert len(repo.open_visits("box")) == 1


class ControlStream:
    def __init__(self, camera_id, url):
        self.camera_id, self.url = camera_id, url
        self.frame_at = None
        self.online = True

    def start(self): pass
    def stop(self): pass
    def latest(self): return FRAME, self.frame_at
    def status(self): return CameraStatus(self.online, None)


def test_offline_pauses_exit_and_restarts_countdown(tmp_path):
    now = {"t": t(0)}
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://cam1", True)], base_dir=tmp_path)
    repo = VisitRepo(Database(":memory:"))
    rec = FakeRecognizer()
    engine = Engine(cfg, repo, rec, stream_factory=ControlStream, clock=lambda: now["t"])
    stream = engine._streams["box"]

    rec.reads = [PlateRead("A123BC77", 0.9, (0, 0, 1, 1))]
    for s in (0, 1, 2):
        now["t"], stream.frame_at = t(s), t(s)
        engine._tick("box")
    [visit] = repo.open_visits("box")

    rec.reads = []
    stream.online = False          # frame_at stays frozen at t(2)
    for s in (100, 500):
        now["t"] = t(s)
        engine._tick("box")
    assert len(repo.open_visits("box")) == 1

    stream.online = True
    now["t"], stream.frame_at = t(600), t(600)
    engine._tick("box")
    assert len(repo.open_visits("box")) == 1   # countdown restarted at t(600)

    now["t"], stream.frame_at = t(781), t(781)
    engine._tick("box")
    assert repo.open_visits("box") == []
    assert repo.get(visit.id).exited_at == t(2)  # exit time = last time the plate was seen


def test_duplicate_open_plates_closed_except_newest(tmp_path, caplog):
    repo = VisitRepo(Database(":memory:"))
    old = repo.create("А123ВС77", "A123BC77", "box", t(0), "s.jpg")
    repo.touch(old, t(50))
    new = repo.create("В456ОР99", "B456OP99", "box", t(100), "s.jpg")
    repo.touch(new, t(120))
    repo.edit_plate(new, "А123ВС77", "admin", t(130))  # ручная правка -> дубль номера
    with caplog.at_level("WARNING"):
        _, _, rec, engine = make(tmp_path, repo=repo, clock=lambda: t(140))
    assert repo.get(old).exited_at == t(50)
    assert repo.get(new).exited_at is None
    assert [v.id for v in repo.open_visits("box")] == [new]
    assert any("А123ВС77" in r.message for r in caplog.records)
    # выезд закрывает именно новейший заезд
    engine.process("box", FRAME, t(145), online=True)  # номер не виден, ждём выезда
    engine.process("box", FRAME, t(140 + 181), online=True)
    assert repo.get(new).exited_at is not None
