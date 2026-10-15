from types import SimpleNamespace

import pytest

from app.config import load_config
from app.uploader import RcloneError

STATUS = {"cameras": {"box": {"state": "error", "since": "11:40", "error": "ffmpeg завершился с кодом 1"}},
          "queue_count": 2, "queue_gb": 0.5, "last_upload": "11:00", "upload_error": "нет интернета",
          "upload_error_at": "11:40", "low_space": True, "free_gb": 12.0, "cleanup_error": "x"}

REC_FORM = {"rec_start": "08:30", "rec_end": "19:00", "rec_day_mon": "on", "rec_day_sat": "on",
            "segment_minutes": "10", "upload_bwlimit_mbit": "5", "drive_retention_days": "30",
            "local_cleanup_weekday": "sun", "low_space_gb": "25",
            "cam_box_record": "on", "cam_box_record_source": "",
            "cam_office_record": "on", "cam_office_record_source": "rtsp://sub"}


def test_status_includes_archive_for_owner_only(env, login):
    env.app.state.archive = SimpleNamespace(status=lambda: STATUS)
    assert login("owner").get("/api/status").json()["archive"] == STATUS
    assert login("viewer").get("/api/status").json()["archive"] is None


def test_status_archive_none_when_disabled(login):
    assert login("admin").get("/api/status").json()["archive"] is None


def test_cameras_page_has_archive_alert_container(login):
    assert 'id="archive-alert"' in login("owner").get("/").text


def test_save_recording_settings(env, login):
    r = login("admin").post("/settings/recording", data=REC_FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1"
    cfg = load_config(env.cfg_path)
    rec = cfg.recording
    assert (rec.start, rec.end, rec.days, rec.segment_minutes) == ("08:30", "19:00", ["mon", "sat"], 10)
    assert (rec.upload_bwlimit_mbit, rec.drive_retention_days, rec.local_cleanup_weekday, rec.low_space_gb) == (5, 30, "sun", 25)
    assert cfg.cameras[0].record and cfg.cameras[0].record_source is None
    assert cfg.cameras[1].record_source == "rtsp://sub"


@pytest.mark.parametrize("field, value", [("rec_end", "08:00"), ("segment_minutes", "0"),
                                          ("upload_bwlimit_mbit", "nan"), ("upload_bwlimit_mbit", "0.05"),
                                          ("local_cleanup_weekday", "xx")])
def test_save_recording_rejects_bad(env, login, field, value):
    before = env.cfg_path.read_text(encoding="utf-8")
    assert login("admin").post("/settings/recording", data={**REC_FORM, field: value}).status_code == 400
    assert env.cfg_path.read_text(encoding="utf-8") == before


def test_save_recording_requires_a_day(login):
    data = {k: v for k, v in REC_FORM.items() if not k.startswith("rec_day_")}
    assert login("admin").post("/settings/recording", data=data).status_code == 400


@pytest.mark.parametrize("role", ["owner", "viewer"])
def test_recording_settings_admin_only(login, role):
    assert login(role).post("/settings/recording", data=REC_FORM).status_code == 403
    assert login(role).post("/settings/check-drive").status_code == 403


def test_check_drive(env, login):
    env.app.state.rclone = SimpleNamespace(about=lambda: "Total: 5 TiB")
    assert login("admin").post("/settings/check-drive").json() == {"ok": True, "message": "Google Диск подключён"}

    def fail():
        raise RcloneError("didn't find section in config file")
    env.app.state.rclone = SimpleNamespace(about=fail)
    data = login("admin").post("/settings/check-drive").json()
    assert data["ok"] is False and "didn't find section" in data["message"]


def test_settings_page_shows_recording_section(login):
    html = login("admin").get("/settings").text
    assert 'action="/settings/recording"' in html and 'name="rec_day_mon"' in html and "check-drive" in html
    assert '<p class="muted small">Изменения записи вступят в силу после перезапуска программы.</p>' in html
    assert 'name="upload_bwlimit_mbit" min="0.1"' in html
