import pytest

from app.config import load_config, save_config

SAMPLE = """
cameras:
  - id: box
    name: Бокс
    source: rtsp://user:pass@192.168.1.10:554/stream1
    recognize: true
  - id: office
    name: Офис
    source: rtsp://192.168.1.11:554/stream1
tracker:
  exit_after_s: 120
"""


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_reads_cameras_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, SAMPLE))
    assert [c.id for c in cfg.cameras] == ["box", "office"]
    assert cfg.cameras[0].recognize is True
    assert cfg.cameras[1].recognize is False
    assert cfg.tracker.exit_after_s == 120
    assert cfg.tracker.confirm_reads == 3
    assert cfg.port == 8000
    assert cfg.camera("office").name == "Офис"
    assert cfg.camera("nope") is None


def test_paths_are_relative_to_config_dir(tmp_path):
    cfg = load_config(write(tmp_path, SAMPLE))
    base = tmp_path.resolve()
    assert cfg.db_path == base / "data.db"
    assert cfg.screenshots_dir == base / "screenshots"
    assert cfg.backups_dir == base / "backups"
    assert cfg.logs_dir == base / "logs"


def test_secret_key_generated_once(tmp_path):
    path = write(tmp_path, SAMPLE)
    first = load_config(path)
    assert len(first.secret_key) >= 32
    assert load_config(path).secret_key == first.secret_key


def test_invalid_camera_id_rejected(tmp_path):
    bad = "cameras:\n  - id: 'Бокс 1'\n    name: x\n    source: rtsp://x\n"
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))


def test_duplicate_camera_id_rejected(tmp_path):
    dup = (
        "cameras:\n"
        "  - {id: box, name: a, source: rtsp://a}\n"
        "  - {id: box, name: b, source: rtsp://b}\n"
    )
    with pytest.raises(ValueError):
        load_config(write(tmp_path, dup))


def test_save_roundtrip(tmp_path):
    path = write(tmp_path, SAMPLE)
    cfg = load_config(path)
    cfg.tracker.exit_after_s = 300
    cfg.cameras[0].name = "Бокс 1"
    save_config(cfg, path)
    again = load_config(path)
    assert again.tracker.exit_after_s == 300
    assert again.cameras[0].name == "Бокс 1"
    assert "Бокс 1" in path.read_text(encoding="utf-8")  # без \u-экранирования


def test_non_string_camera_id_rejected(tmp_path):
    """Camera id must be a string, not a number."""
    bad = "cameras:\n  - id: 123\n    name: x\n    source: rtsp://x\n"
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))


def test_unknown_camera_key_rejected(tmp_path):
    """Unknown keys in camera dict should raise ValueError."""
    bad = "cameras:\n  - id: box\n    name: x\n    source: rtsp://x\n    unknown_key: value\n"
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))


def test_unknown_tracker_key_rejected(tmp_path):
    """Unknown keys in tracker dict should raise ValueError."""
    bad = "tracker:\n  unknown_key: value\n"
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))


def test_save_config_is_atomic(tmp_path):
    """save_config should not leave .tmp files behind and content should reload."""
    path = write(tmp_path, SAMPLE)
    cfg = load_config(path)
    cfg.tracker.exit_after_s = 300
    save_config(cfg, path)
    # Verify no .tmp file is left
    tmp_file = path.with_suffix(path.suffix + ".tmp")
    assert not tmp_file.exists(), "Temporary file should be cleaned up"
    # Verify content can be reloaded
    again = load_config(path)
    assert again.tracker.exit_after_s == 300


@pytest.mark.parametrize("line", [
    "tracker: {confirm_reads: 0}", "tracker: {confirm_reads: 101}",
    "tracker: {confirm_window_s: .nan}", "tracker: {confirm_window_s: .inf}",
    "tracker: {confirm_window_s: 601}", "tracker: {exit_after_s: .inf}",
    "tracker: {exit_after_s: 86401}", "tracker: {exit_after_s: -1}",
    "tracker: {seen_write_interval_s: .nan}", "tracker: {seen_write_interval_s: 3601}",
    "analyze_fps: .nan", "analyze_fps: .inf", "analyze_fps: 11", "analyze_fps: 0",
])
def test_load_rejects_non_finite_or_huge_thresholds(tmp_path, line):
    with pytest.raises(ValueError, match="config.yaml"):
        load_config(write(tmp_path, "cameras: []\n" + line + "\n"))
