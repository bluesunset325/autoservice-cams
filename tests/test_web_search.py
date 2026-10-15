from fastapi.testclient import TestClient


def test_search_requires_login(env):
    assert TestClient(env.app).get("/search", follow_redirects=False).status_code == 303


def test_empty_form_shows_no_results(login, seeded):
    html = login("viewer").get("/search").text
    assert "Найдено" not in html


def test_search_partial_latin_input(login, seeded):
    html = login("viewer").get("/search?q=bc77").text
    assert "Найдено: 2" in html
    assert "А123ВС77" in html and "В456ОР99" not in html


def test_search_by_period_only(login, seeded):
    html = login("viewer").get("/search?q=&date_from=2026-10-02&date_to=2026-10-02").text
    assert "Найдено: 3" in html
    assert "Е789КХ50" not in html  # заехал 01.10


def test_search_result_links_to_journal_day(login, seeded):
    html = login("viewer").get("/search?q=789").text
    assert "/journal?day=2026-10-01" in html
    assert "01.10.2026" in html


def test_search_ignores_bad_dates(login, seeded):
    assert "Найдено: 2" in login("viewer").get("/search?q=123&date_from=oops").text
