"""Окно записи: дни недели и часы."""
from datetime import datetime

from app.config import WEEKDAYS, RecordingConfig


def is_recording_time(now: datetime, rec: RecordingConfig) -> bool:
    if WEEKDAYS[now.weekday()] not in rec.days:
        return False
    return rec.start_time <= now.time() < rec.end_time
