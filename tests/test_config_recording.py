import math
from datetime import time
from pathlib import Path

import pytest

from app.config import (WEEKDAYS, RecordingConfig, load_config, resolve_executable, save_config,
                        validate_recording)


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


BASE = "secret_key: x\ncameras:\n  - {id: box, name: Бокс, source: rtsp://a, recognize: true, record: true}\n" \
       "  - {id: office, name: Офис, source: xiaomi://b, record: true, record_source: 'rtsp://cam/sub'}\n"


def test_defaults_when_section_missing(tmp_path):
    cfg = load_config(write(tmp_path, "secret_key: x\ncameras: []\n"))
    rec = cfg.recording
    assert (rec.start, rec.end, rec.segment_minutes) == ("09:00", "18:00", 15)
    assert rec.days == list(WEEKDAYS)
    assert (rec.upload_bwlimit_mbit, rec.drive_retention_days, rec.local_cleanup_weekday, rec.low_space_gb) == (10, 45, "mon", 20)
    assert (rec.rclone_remote, rec.rclone_config, rec.drive_folder) == ("gdrive:", "rclone.conf", "Автосервис")
    assert rec.start_time == time(9, 0) and rec.end_time == time(18, 0)
    assert cfg.recordings_dir == tmp_path.resolve() / "recordings"


def test_camera_record_fields(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    box, office = cfg.cameras
    assert box.record is True and box.record_source is None
    assert office.record is True and office.record_source == "rtsp://cam/sub"


def test_custom_recording_roundtrip(tmp_path):
    p = write(tmp_path, BASE + "recording:\n  start: '08:30'\n  end: '19:00'\n  days: [mon, tue]\n  upload_bwlimit_mbit: 5\n")
    cfg = load_config(p)
    assert cfg.recording.days == ["mon", "tue"] and cfg.recording.upload_bwlimit_mbit == 5
    save_config(cfg, p)
    again = load_config(p)
    assert again.recording.start == "08:30" and again.recording.end == "19:00"
    assert again.cameras[1].record_source == "rtsp://cam/sub"


@pytest.mark.parametrize("field, value", [
    ("start", "9:61"), ("start", "abc"), ("end", "08:00"),          # end <= start (start 09:00)
    ("days", []), ("days", ["mon", "xxx"]), ("local_cleanup_weekday", "funday"),
    ("segment_minutes", 0), ("segment_minutes", 61), ("segment_minutes", 1.5),
    ("drive_retention_days", 0), ("upload_bwlimit_mbit", math.nan), ("upload_bwlimit_mbit", math.inf),
    ("upload_bwlimit_mbit", 0), ("upload_bwlimit_mbit", 0.09), ("low_space_gb", -1), ("drive_folder", ""), ("drive_folder", "/abs"),
])
def test_validate_recording_rejects(field, value):
    rec = RecordingConfig(**{field: value})
    with pytest.raises(ValueError):
        validate_recording(rec)


def test_validate_recording_accepts_defaults():
    validate_recording(RecordingConfig())


def test_load_config_rejects_bad_recording(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, BASE + "recording:\n  end: '07:00'\n"))
    with pytest.raises(ValueError):
        load_config(write(tmp_path, BASE + "recording:\n  bogus: 1\n"))


def test_resolve_executable(tmp_path):
    assert resolve_executable(tmp_path, "ffmpeg") == "ffmpeg"
    (tmp_path / "ffmpeg.exe").write_text("x")
    assert resolve_executable(tmp_path, "ffmpeg.exe") == str(tmp_path / "ffmpeg.exe")


def test_example_config_office_records_same_stream(tmp_path):
    import shutil
    from pathlib import Path
    example = tmp_path / "config.yaml"      # load_config дописывает secret_key — грузим копию
    shutil.copy(Path(__file__).resolve().parent.parent / "config.example.yaml", example)
    cfg = load_config(example)
    office = next(c for c in cfg.cameras if c.id == "office")
    assert office.record and office.record_source is None


def test_validate_recording_accepts_min_bwlimit():
    validate_recording(RecordingConfig(upload_bwlimit_mbit=0.1))
