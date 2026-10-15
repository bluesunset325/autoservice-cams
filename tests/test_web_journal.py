from fastapi.testclient import TestClient

DAY = "2026-10-02"


def test_journal_requires_login(env):
    assert TestClient(env.app).get("/journal", follow_redirects=False).status_code == 303


def test_journal_groups_by_plate(login, seeded):
    html = login("viewer").get(f"/journal?day={DAY}").text
    assert html.count('<span class="plate">А123ВС77</span>') == 1
    assert "2 ч 30 мин" in html
    assert "заездов: 2" in html
    assert "в боксе" in html          # В456ОР99
    assert "с прошлого дня" in html   # Е789КХ50
    assert "/screenshots/screenshot_2026-10-02_10-00-00.jpg" in html


def test_journal_defaults_to_today(login, seeded):
    assert "А123ВС77" in login("viewer").get("/journal").text


def test_open_visit_not_shown_on_future_days(login, seeded):
    # В456ОР99 is open since 2026-10-02 12:00; today is 2026-10-02 17:00
    # Should appear today but not tomorrow
    html_today = login("viewer").get(f"/journal?day={DAY}").text
    assert "В456ОР99" in html_today
    html_tomorrow = login("viewer").get("/journal?day=2026-10-03").text
    assert "В456ОР99" not in html_tomorrow


def test_journal_other_day_and_nav_links(login, seeded):
    html = login("viewer").get("/journal?day=2026-10-05").text
    assert "заездов нет" in html
    assert "day=2026-10-04" in html and "day=2026-10-06" in html


def test_journal_bad_day_falls_back_to_today(login, seeded):
    assert "А123ВС77" in login("viewer").get("/journal?day=garbage").text


def test_journal_default_sort_by_entry(login, seeded):
    html = login("viewer").get(f"/journal?day={DAY}").text
    assert html.index("Е789КХ50") < html.index("А123ВС77") < html.index("В456ОР99")


def test_journal_sort_by_duration_desc(login, seeded):
    # В456ОР99: 12:00→17:00 = 5 ч; А123ВС77: 2 ч 30 мин; Е789КХ50: 2 ч
    html = login("viewer").get(f"/journal?day={DAY}&sort=duration&desc=1").text
    assert html.index("В456ОР99") < html.index("А123ВС77") < html.index("Е789КХ50")


def test_viewer_sees_no_edit_controls(login, seeded):
    html = login("viewer").get(f"/journal?day={DAY}").text
    assert "/plate" not in html and "/delete" not in html


def test_owner_edits_plate(env, login, seeded):
    r = login("owner").post(f"/visits/{seeded.a1}/plate", data={"plate": "a 123 bc 777"},
                            follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/journal?day={DAY}"
    v = env.visits.get(seeded.a1)
    assert (v.plate, v.plate_raw, v.edited_by) == ("А123ВС777", "A123BC77", "owner")


def test_edit_to_existing_plate_merges_group(login, seeded):
    client = login("owner")
    client.post(f"/visits/{seeded.b}/plate", data={"plate": "А123ВС77"})
    html = client.get(f"/journal?day={DAY}").text
    assert html.count('<span class="plate">А123ВС77</span>') == 1
    assert "заездов: 3" in html


def test_edit_rejects_empty_plate(login, seeded):
    assert login("owner").post(f"/visits/{seeded.a1}/plate", data={"plate": "  "}).status_code == 400


def test_viewer_cannot_edit(login, seeded):
    assert login("viewer").post(f"/visits/{seeded.a1}/plate", data={"plate": "А1"}).status_code == 403


def test_edit_missing_visit_404(login):
    assert login("owner").post("/visits/999/plate", data={"plate": "А1"}).status_code == 404


def test_delete_admin_only(env, login, seeded):
    assert login("owner").post(f"/visits/{seeded.a1}/delete").status_code == 403
    r = login("admin").post(f"/visits/{seeded.a1}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert env.visits.get(seeded.a1) is None


def test_screenshot_served_only_to_logged_in(env, login):
    name = "screenshot_2026-10-02_10-00-00.jpg"
    (env.cfg.screenshots_dir / name).write_bytes(b"\xff\xd8fakejpeg")
    assert TestClient(env.app).get(f"/screenshots/{name}", follow_redirects=False).status_code == 303
    client = login("viewer")
    r = client.get(f"/screenshots/{name}")
    assert r.status_code == 200 and r.content == b"\xff\xd8fakejpeg"
    assert client.get("/screenshots/config.yaml").status_code == 404
    assert client.get("/screenshots/screenshot_2026-10-02_11-00-00.jpg").status_code == 404
