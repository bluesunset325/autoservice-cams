import pytest

from app.config import load_config

FORM = {
    "cam_box_name": "Бокс 1", "cam_box_source": "rtsp://new/1", "cam_box_recognize": "on",
    "cam_office_name": "Офис", "cam_office_source": "rtsp://1.2.3.5/s",
    "confirm_reads": "4", "confirm_window_s": "6", "exit_after_s": "240", "analyze_fps": "3",
}


@pytest.mark.parametrize("role", ["owner", "viewer"])
def test_settings_admin_only(login, role):
    client = login(role)
    assert client.get("/settings").status_code == 403
    assert client.post("/settings", data=FORM).status_code == 403
    assert client.post("/settings/check/box").status_code == 403
    assert client.post("/settings/restart").status_code == 403


def test_settings_page_shows_cameras_and_disk(login):
    html = login("admin").get("/settings").text
    assert "rtsp://u:p@1.2.3.4/s" in html
    assert "Свободно на диске" in html


def test_save_settings_writes_config(env, login):
    r = login("admin").post("/settings", data=FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1"
    saved = load_config(env.cfg_path)
    assert saved.cameras[0].name == "Бокс 1"
    assert saved.cameras[0].source == "rtsp://new/1"
    assert saved.cameras[0].recognize is True
    assert saved.cameras[1].recognize is False
    assert saved.tracker.confirm_reads == 4
    assert saved.tracker.confirm_window_s == 6
    assert saved.tracker.exit_after_s == 240
    assert saved.analyze_fps == 3
    assert saved.secret_key == env.cfg.secret_key
    assert "после перезапуска" in login("admin").get("/settings?saved=1").text


@pytest.mark.parametrize("field, value", [
    ("confirm_reads", "0"), ("exit_after_s", "-1"), ("analyze_fps", "abc"), ("cam_box_source", ""),
    ("confirm_window_s", "nan"), ("confirm_window_s", "inf"), ("exit_after_s", "nan"),
    ("exit_after_s", "inf"), ("analyze_fps", "nan"), ("analyze_fps", "inf"),
    ("confirm_reads", "101"), ("confirm_window_s", "601"), ("exit_after_s", "86401"),
    ("analyze_fps", "11"), ("exit_after_s", "1e999"),
])
def test_save_settings_rejects_bad_values(env, login, field, value):
    before = env.cfg_path.read_text(encoding="utf-8")
    r = login("admin").post("/settings", data={**FORM, field: value})
    assert r.status_code == 400
    assert env.cfg_path.read_text(encoding="utf-8") == before


def test_check_camera_uses_probe(env, login):
    env.app.state.probe = lambda url: (url == "rtsp://127.0.0.1:8554/box", "Работает, 1920×1080")
    client = login("admin")
    assert client.post("/settings/check/box").json() == {"ok": True, "message": "Работает, 1920×1080"}
    assert client.post("/settings/check/nope").status_code == 404


def test_restart_calls_restart(env, login):
    called = []
    env.app.state.restart = lambda: called.append(True)
    assert login("admin").post("/settings/restart").json() == {"ok": True}
    assert called == [True]
