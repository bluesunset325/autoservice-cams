"""Форматирование для страниц АП."""
from datetime import date, datetime, timedelta


def fmt_duration(td: timedelta) -> str:
    minutes = max(0, int(td.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def fmt_time(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "—"


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")
