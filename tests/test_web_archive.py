from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.web.pages_archive import _pick, day_cells
from app.archive import Recording
from app.config import RecordingConfig


@pytest.fixture
def archive(env):
    for cam in env.cfg.cameras:
        cam.record = True
    d = env.cfg.recordings_dir / "box"
    d.mkdir(parents=True)
    r = env.recordings
    files = {}
    for hh, mm, state in [(9, 0, "both"), (9, 15, "local"), (9, 30, "drive")]:
        name = f"2026-10-02_{hh:02d}-{mm:02d}-00.mp4"
        (d / name).write_bytes(b"0123456789")
        r.add("box", datetime(2026, 10, 2, hh, mm), name, 10)
        rec = r.for_day("box", date(2026, 10, 2))[-1]
        if state in ("both", "drive"):
            r.mark_uploaded(rec.id, datetime(2026, 10, 2, 12), f"DRV{hh}{mm}", f"Автосервис/Бокс/2026-10-02/{hh:02d}-{mm:02d}-00.mp4")
        if state == "drive":
            r.mark_local_deleted(rec.id, datetime(2026, 10, 2, 13))
            (d / name).unlink()
        files[state] = rec.id
    return files


def test_day_cells_fill_gaps():
    recs = []
    cells = day_cells(date(2026, 10, 2), RecordingConfig(start="09:00", end="10:00"), recs)
    assert [c.start.strftime("%H:%M") for c in cells] == ["09:00", "09:15", "09:30", "09:45"]
    assert all(c.status == "none" for c in cells)


@pytest.mark.parametrize("role, code", [("viewer", 403), ("owner", 200), ("admin", 200)])
def test_archive_access(login, archive, role, code):
    assert login(role).get("/archive?camera=box&day=2026-10-02").status_code == code


def test_archive_requires_login(env):
    assert TestClient(env.app).get("/archive", follow_redirects=False).status_code == 303


def test_nav_link_only_for_archive_roles(login, archive):
    assert 'href="/archive"' in login("owner").get("/").text
    assert 'href="/archive"' not in login("viewer").get("/").text


def test_archive_page_shows_cells_legend_and_links(login, archive):
    html = login("owner").get("/archive?camera=box&day=2026-10-02").text
    for text in ("на компьютере и на Диске", "ждёт загрузки", "только на Диске", "записи нет"):
        assert text in html                                   # легенда
    assert 'class="cell both"' in html and 'class="cell local"' in html and 'class="cell drive"' in html
    assert "https://drive.google.com/file/d/DRV930/view" in html
    assert 'class="cell none"' in html                         # 09:45 и далее — пусто


def test_archive_selects_by_at_and_rec(login, archive):
    client = login("owner")
    html = client.get("/archive?camera=box&day=2026-10-02&at=09:20").text
    assert f'src="/archive/video/{archive["local"]}"' in html
    html = client.get(f"/archive?camera=box&day=2026-10-02&rec={archive['both']}").text
    assert f'src="/archive/video/{archive["both"]}"' in html


def test_video_serving_range_and_download(login, archive):
    client = login("owner")
    r = client.get(f"/archive/video/{archive['both']}", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"0123"
    r = client.get(f"/archive/video/{archive['both']}?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/archive/video/{archive['drive']}").status_code == 404   # только на Диске
    assert client.get("/archive/video/999").status_code == 404
    assert login("viewer").get(f"/archive/video/{archive['both']}").status_code == 403


def test_journal_video_link(login, seeded, archive):
    html = login("owner").get("/journal?day=2026-10-02").text
    assert "/archive?camera=box&amp;day=2026-10-02&amp;at=10:00:00" in html
    assert "Видео заезда" not in login("viewer").get("/journal?day=2026-10-02").text


def test_at_drive_only_shows_notice(login, archive):
    html = login("owner").get("/archive?camera=box&day=2026-10-02&at=09:35").text
    assert "<video" not in html
    assert "только на Google Диске" in html and "https://drive.google.com/file/d/DRV930/view" in html


def test_at_without_recording_says_missing(login, archive):
    html = login("owner").get("/archive?camera=box&day=2026-10-02&at=12:00").text
    assert "Записи на 12:00 нет" in html and "<video" not in html


@pytest.mark.parametrize("at", ["garbage", "25:99", "9"])
def test_at_garbage_no_selection(login, archive, at):
    r = login("owner").get(f"/archive?camera=box&day=2026-10-02&at={at}")
    assert r.status_code == 200 and "<video" not in r.text


def test_day_cells_partial_last_slot():
    cells = day_cells(date(2026, 10, 2), RecordingConfig(start="09:00", end="10:00", segment_minutes=25), [])
    assert [c.start.strftime("%H:%M") for c in cells] == ["09:00", "09:25", "09:50"]


def test_video_rejects_traversal_row(login, env, archive):
    env.recordings.add("box", datetime(2026, 10, 2, 9, 45), "../../config.yaml", 10)
    rec = env.recordings.for_day("box", date(2026, 10, 2))[-1]
    assert login("owner").get(f"/archive/video/{rec.id}").status_code == 404


def test_cleanup_error_shown(login, env, archive):
    from types import SimpleNamespace
    status = {"cameras": {}, "queue_count": 0, "queue_gb": 0, "last_upload": None, "upload_error": None,
              "upload_error_at": None, "cleanup_error": "x", "low_space": False, "free_gb": 5}
    env.app.state.archive = SimpleNamespace(status=lambda: status)
    assert "очистка: x" in login("owner").get("/archive?camera=box&day=2026-10-02").text


def _recs(*times, day=date(2026, 10, 2)):
    return [Recording(i + 1, "box", datetime.combine(day, t), f"f{i}.mp4", 1, None, None, None, None)
            for i, t in enumerate(times)]


BOUNDARY = _recs(*(datetime.strptime(t, "%H:%M:%S").time() for t in ("09:00:04", "09:15:02", "09:30:01")))


@pytest.mark.parametrize("at, expected", [
    ("09:00", "09:00:04"), ("09:00:00", "09:00:04"),     # чуть раньше начала (до 60 с) — этот кусок
    ("09:15:30", "09:15:02"), ("09:15:01", "09:15:02"), ("09:14:01", "09:00:04"),
    ("09:44:00", "09:30:01"), ("09:44", "09:30:01"), ("09:46:00", "09:30:01"),
    ("10:20", None), ("09:46:02", None), ("08:59:03", None),
])
def test_pick_boundaries(at, expected):
    selected, drive_only, missing = _pick(BOUNDARY, date(2026, 10, 2), 15, at, None)
    if expected is None:
        assert selected is None and drive_only is None and missing == at
    else:
        assert selected is not None and selected.started_at.strftime("%H:%M:%S") == expected and missing is None


def test_pick_gap_after_crash_is_missing():
    recs = _recs(*(datetime.strptime(t, "%H:%M:%S").time() for t in ("09:00:00", "10:00:00")))
    assert _pick(recs, date(2026, 10, 2), 15, "09:50", None)[2] == "09:50"
    assert _pick(recs, date(2026, 10, 2), 15, "09:59:30", None)[0].id == 2


def test_pick_skips_gone():
    recs = _recs(*(datetime.strptime(t, "%H:%M:%S").time() for t in ("09:00:00", "09:15:00")))
    gone = Recording(2, "box", recs[1].started_at, "f1.mp4", 1, None, None, datetime(2026, 10, 3), None)
    assert _pick([recs[0], gone], date(2026, 10, 2), 15, "09:20", None)[2] == "09:20"


@pytest.mark.parametrize("at", ["09:15:99", "09:15:00:00", "aa:bb"])
def test_pick_bad_seconds(at):
    assert _pick(BOUNDARY, date(2026, 10, 2), 15, at, None) == (None, None, None)


def test_archive_status_uses_camera_name(login, env, archive):
    from types import SimpleNamespace
    status = {"cameras": {"box": {"name": "Бокс", "state": "error", "since": "09:00", "error": "e"}},
              "queue_count": 0, "queue_gb": 0, "last_upload": None, "upload_error": None,
              "upload_error_at": None, "cleanup_error": None, "low_space": False, "free_gb": 5}
    env.app.state.archive = SimpleNamespace(status=lambda: status)
    assert "<span>Бокс: ⚠ e" in login("owner").get("/archive?camera=box&day=2026-10-02").text
