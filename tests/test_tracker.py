from datetime import datetime, timedelta

from app.config import TrackerConfig
from app.tracker import Entered, Exited, Seen, Tracker

T0 = datetime(2026, 10, 2, 10, 0, 0)
P = "А123ВС77"
Q = "В456ОР99"
CFG = TrackerConfig(confirm_reads=3, confirm_window_s=5, exit_after_s=180, seen_write_interval_s=10)


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def entered(tr: Tracker, plate: str = P, start: float = 0) -> None:
    for s in (start, start + 1, start + 2):
        tr.update([plate], t(s))


def test_three_reads_within_window_create_entry_at_first_read():
    tr = Tracker(CFG, now=T0)
    assert tr.update([P], t(0)) == []
    assert tr.update([P], t(1)) == []
    assert tr.update([P], t(2)) == [Entered(P, t(0))]
    assert tr.in_box == {P}


def test_sparse_reads_do_not_create_entry():
    tr = Tracker(CFG, now=T0)
    events = tr.update([P], t(0)) + tr.update([P], t(6)) + tr.update([P], t(12))
    assert events == []
    assert tr.in_box == set()


def test_duplicate_plate_in_one_frame_counts_once():
    tr = Tracker(CFG, now=T0)
    assert tr.update([P, P, P], t(0)) == []


def test_seen_emitted_at_most_every_interval():
    tr = Tracker(CFG, now=T0)
    entered(tr)
    events = [e for s in range(3, 26) for e in tr.update([P], t(s))]
    assert events == [Seen(P, t(12)), Seen(P, t(22))]


def test_exit_after_absence_uses_last_seen_time():
    tr = Tracker(CFG, now=T0)
    entered(tr)
    tr.update([P], t(50))
    assert tr.update([], t(229)) == []
    assert tr.update([], t(230)) == [Exited(P, t(50))]
    assert tr.in_box == set()


def test_short_absence_keeps_same_visit():
    tr = Tracker(CFG, now=T0)
    entered(tr)
    assert tr.update([], t(100)) == []
    assert tr.update([P], t(150)) == [Seen(P, t(150))]
    assert tr.update([], t(329)) == []
    assert tr.update([], t(330)) == [Exited(P, t(150))]


def test_plates_tracked_independently():
    tr = Tracker(CFG, now=T0)
    for s in (0, 1, 2):
        tr.update([P, Q], t(s))
    assert tr.in_box == {P, Q}
    events = []
    for s in range(3, 200):
        events += [e for e in tr.update([Q], t(s)) if isinstance(e, Exited)]
    assert events == [Exited(P, t(2))]
    assert tr.in_box == {Q}


def test_plate_can_enter_again_after_exit():
    tr = Tracker(CFG, now=T0)
    entered(tr)
    tr.update([], t(182))
    for s in (300, 301):
        assert tr.update([P], t(s)) == []
    assert tr.update([P], t(302)) == [Entered(P, t(300))]


def test_offline_pauses_exit_and_restarts_timer():
    tr = Tracker(CFG, now=T0)
    entered(tr)
    assert tr.update([], t(100), online=False) == []
    assert tr.update([], t(500), online=False) == []
    assert tr.update([], t(600), online=True) == []
    assert tr.update([], t(779), online=True) == []
    assert tr.update([], t(780), online=True) == [Exited(P, t(2))]


def test_restart_loads_open_visits_and_closes_after_timeout():
    tr = Tracker(CFG, now=t(1000), open_visits={P: t(900)})
    assert tr.in_box == {P}
    assert tr.update([], t(1179)) == []
    assert tr.update([], t(1180)) == [Exited(P, t(900))]


def test_restart_continues_visit_when_plate_seen():
    tr = Tracker(CFG, now=t(1000), open_visits={P: t(990)})
    assert tr.update([P], t(1001)) == [Seen(P, t(1001))]
    assert tr.in_box == {P}
