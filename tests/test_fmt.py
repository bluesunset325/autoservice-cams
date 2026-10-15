from datetime import date, datetime, timedelta

from app.web.fmt import fmt_date, fmt_duration, fmt_time


def test_fmt_duration():
    assert fmt_duration(timedelta(minutes=45, seconds=59)) == "45 мин"
    assert fmt_duration(timedelta(hours=2, minutes=30)) == "2 ч 30 мин"
    assert fmt_duration(timedelta(hours=1)) == "1 ч 0 мин"
    assert fmt_duration(timedelta(seconds=-5)) == "0 мин"


def test_fmt_time_and_date():
    assert fmt_time(datetime(2026, 10, 2, 9, 5)) == "09:05"
    assert fmt_time(None) == "—"
    assert fmt_date(date(2026, 10, 2)) == "02.10.2026"
