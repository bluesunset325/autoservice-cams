import os
from datetime import datetime

from app.archive import RecordingRepo
from app.config import AppConfig, CameraConfig
from app.db import Database
from app.indexer import find_ready_segments, index_segments

NAMES = ["2026-10-03_09-00-00.mp4", "2026-10-03_09-15-00.mp4", "2026-10-03_09-30-00.mp4"]


def make_files(d, now_ts, newest_age):
    d.mkdir(parents=True)
    for i, n in enumerate(NAMES):
        p = d / n
        p.write_bytes(b"x" * (i + 1))
        age = newest_age if i == len(NAMES) - 1 else 1000
        os.utime(p, (now_ts - age, now_ts - age))
    (d / "junk.txt").write_text("x")


def test_newest_skipped_while_recording(tmp_path):
    make_files(tmp_path / "box", 10_000, newest_age=500)
    ready = find_ready_segments(tmp_path / "box", recording=True, now_ts=10_000)
    assert [p.name for p in ready] == NAMES[:2]


def test_newest_ready_when_stopped_and_settled(tmp_path):
    make_files(tmp_path / "box", 10_000, newest_age=61)
    assert len(find_ready_segments(tmp_path / "box", recording=False, now_ts=10_000)) == 3


def test_newest_not_ready_when_stopped_but_fresh(tmp_path):
    make_files(tmp_path / "box", 10_000, newest_age=10)
    assert len(find_ready_segments(tmp_path / "box", recording=False, now_ts=10_000)) == 2


def test_missing_dir(tmp_path):
    assert find_ready_segments(tmp_path / "nope", recording=False, now_ts=0) == []


def test_index_segments_adds_once(tmp_path):
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True),
                             CameraConfig("office", "Офис", "rtsp://b", record=False)], base_dir=tmp_path)
    make_files(cfg.recordings_dir / "box", 10_000, newest_age=500)
    make_files(cfg.recordings_dir / "office", 10_000, newest_age=500)   # record=False — игнорируется
    repo = RecordingRepo(Database(":memory:"))
    assert index_segments(cfg, repo, {"box": True}, now_ts=10_000, min_bytes=0) == 2
    assert index_segments(cfg, repo, {"box": True}, now_ts=10_000, min_bytes=0) == 0
    rows = repo.pending_upload()
    assert [(r.camera, r.started_at, r.size) for r in rows] == [
        ("box", datetime(2026, 10, 3, 9, 0), 1), ("box", datetime(2026, 10, 3, 9, 15), 2)]


def test_empty_segment_not_indexed_and_removed(tmp_path):
    # при остановке ffmpeg ровно на границе куска остаётся файл из одного заголовка (28 байт)
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True)], base_dir=tmp_path)
    d = cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    (d / "2026-10-03_09-00-00.mp4").write_bytes(b"v" * 50_000)
    (d / "2026-10-03_09-15-00.mp4").write_bytes(b"f" * 28)
    (d / "2026-10-03_09-15-05.mp4").write_bytes(b"v" * 50_000)   # пишется сейчас
    repo = RecordingRepo(Database(":memory:"))
    assert index_segments(cfg, repo, {"box": True}, now_ts=10_000) == 1
    assert [r.file for r in repo.pending_upload()] == ["2026-10-03_09-00-00.mp4"]
    assert not (d / "2026-10-03_09-15-00.mp4").exists()
    assert (d / "2026-10-03_09-15-05.mp4").exists()


def test_small_newest_while_recording_is_kept(tmp_path):
    # только что начатый кусок ещё мал — его не трогаем
    cfg = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True)], base_dir=tmp_path)
    d = cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    (d / "2026-10-03_09-00-00.mp4").write_bytes(b"f" * 28)
    repo = RecordingRepo(Database(":memory:"))
    assert index_segments(cfg, repo, {"box": True}, now_ts=10_000) == 0
    assert (d / "2026-10-03_09-00-00.mp4").exists()
