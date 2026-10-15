from datetime import date, datetime, time, timedelta

import pytest

from app.db import Database
from app.visits import VisitRepo, group_by_plate, sort_groups

D = date(2026, 10, 2)


def dt(h: int, m: int = 0, day: date = D) -> datetime:
    return datetime.combine(day, time(h, m))


@pytest.fixture
def repo():
    return VisitRepo(Database(":memory:"))


def test_create_touch_close(repo):
    vid = repo.create("А123ВС77", "A123BC77", "box", dt(10), "s.jpg")
    v = repo.get(vid)
    assert (v.plate, v.plate_raw, v.camera, v.screenshot) == ("А123ВС77", "A123BC77", "box", "s.jpg")
    assert v.entered_at == dt(10) and v.last_seen_at == dt(10) and v.exited_at is None
    repo.touch(vid, dt(10, 30))
    assert repo.get(vid).last_seen_at == dt(10, 30)
    repo.close(vid, dt(11))
    v = repo.get(vid)
    assert v.exited_at == dt(11) and v.last_seen_at == dt(11)
    assert v.duration(dt(15)) == timedelta(hours=1)


def test_open_visit_duration_uses_now(repo):
    vid = repo.create("А123ВС77", "x", "box", dt(10), "s.jpg")
    assert repo.get(vid).duration(dt(12)) == timedelta(hours=2)


def test_get_missing_returns_none(repo):
    assert repo.get(999) is None


def test_open_visits_by_camera(repo):
    a = repo.create("А123ВС77", "x", "box", dt(10), "a.jpg")
    b = repo.create("В456ОР99", "x", "box", dt(11), "b.jpg")
    repo.create("Е789КХ50", "x", "other", dt(11), "c.jpg")
    repo.close(a, dt(12))
    assert [v.id for v in repo.open_visits("box")] == [b]


def test_for_day_includes_overnight_and_open_visits(repo):
    prev, nxt = D - timedelta(days=1), D + timedelta(days=1)
    overnight = repo.create("А111АА11", "x", "box", dt(23, day=prev), "1.jpg")
    repo.close(overnight, dt(1))
    yesterday = repo.create("А222АА22", "x", "box", dt(20, day=prev), "2.jpg")
    repo.close(yesterday, dt(21, day=prev))
    today = repo.create("А333АА33", "x", "box", dt(10), "3.jpg")
    repo.create("А444АА44", "x", "box", dt(9, day=nxt), "4.jpg")
    still_open = repo.create("А555АА55", "x", "box", dt(22, day=D - timedelta(days=2)), "5.jpg")
    assert {v.id for v in repo.for_day(D)} == {overnight, today, still_open}


def test_group_by_plate(repo):
    a1 = repo.create("А123ВС77", "x", "box", dt(10), "a1.jpg")
    repo.close(a1, dt(11))
    a2 = repo.create("А123ВС77", "x", "box", dt(15), "a2.jpg")
    repo.close(a2, dt(16, 30))
    repo.create("В456ОР99", "x", "box", dt(12), "b.jpg")
    groups = {g.plate: g for g in group_by_plate(repo.for_day(D), D, now=dt(17))}

    a = groups["А123ВС77"]
    assert a.count == 2
    assert a.first_entry == dt(10)
    assert a.last_exit == dt(16, 30)
    assert a.total == timedelta(hours=2, minutes=30)
    assert not a.in_box and not a.from_prev_day
    assert [v.screenshot for v in a.visits] == ["a1.jpg", "a2.jpg"]

    b = groups["В456ОР99"]
    assert b.in_box and b.last_exit is None
    assert b.total == timedelta(hours=5)


def test_group_from_prev_day_flag(repo):
    v = repo.create("А123ВС77", "x", "box", dt(23, day=D - timedelta(days=1)), "a.jpg")
    repo.close(v, dt(1))
    [g] = group_by_plate(repo.for_day(D), D, now=dt(17))
    assert g.from_prev_day


def test_sort_groups(repo):
    a = repo.create("А123ВС77", "x", "box", dt(10), "a.jpg")
    repo.close(a, dt(10, 30))
    b = repo.create("В456ОР99", "x", "box", dt(9), "b.jpg")
    repo.close(b, dt(12))
    repo.create("Е789КХ50", "x", "box", dt(11), "c.jpg")  # в боксе
    groups = group_by_plate(repo.for_day(D), D, now=dt(11, 10))
    plates = lambda gs: [g.plate for g in gs]
    assert plates(sort_groups(groups, "entry")) == ["В456ОР99", "А123ВС77", "Е789КХ50"]
    assert plates(sort_groups(groups, "exit")) == ["А123ВС77", "В456ОР99", "Е789КХ50"]
    assert plates(sort_groups(groups, "duration", desc=True)) == ["В456ОР99", "А123ВС77", "Е789КХ50"]
    assert plates(sort_groups(groups, "bogus")) == plates(sort_groups(groups, "entry"))


def test_search_by_substring_and_period(repo):
    repo.create("А123ВС77", "x", "box", dt(10), "a.jpg")
    repo.create("А123ВС77", "x", "box", dt(10, day=D - timedelta(days=5)), "b.jpg")
    repo.create("В456ОР99", "x", "box", dt(11), "c.jpg")
    assert [v.screenshot for v in repo.search("ВС77")] == ["a.jpg", "b.jpg"]  # новые сверху
    assert [v.screenshot for v in repo.search("123", date_from=D)] == ["a.jpg"]
    assert [v.screenshot for v in repo.search("123", date_to=D - timedelta(days=1))] == ["b.jpg"]
    assert len(repo.search("")) == 3


def test_edit_plate_keeps_raw_and_records_editor(repo):
    vid = repo.create("А123ВС71", "A123BC71", "box", dt(10), "a.jpg")
    repo.edit_plate(vid, "А123ВС77", "owner", dt(12))
    v = repo.get(vid)
    assert v.plate == "А123ВС77" and v.plate_raw == "A123BC71" and v.edited_by == "owner"


def test_delete(repo):
    vid = repo.create("А123ВС77", "x", "box", dt(10), "a.jpg")
    repo.delete(vid)
    assert repo.get(vid) is None
