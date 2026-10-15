from datetime import datetime

import pytest

from app.config import RecordingConfig
from app.schedule import is_recording_time

SAT = datetime(2026, 10, 3)   # суббота


@pytest.mark.parametrize("hh, mm, expected", [
    (8, 59, False), (9, 0, True), (12, 30, True), (17, 59, True), (18, 0, False), (23, 0, False),
])
def test_window(hh, mm, expected):
    assert is_recording_time(SAT.replace(hour=hh, minute=mm), RecordingConfig()) is expected


def test_day_off():
    rec = RecordingConfig(days=["mon", "tue", "wed", "thu", "fri"])
    assert is_recording_time(SAT.replace(hour=12), rec) is False
    assert is_recording_time(datetime(2026, 10, 5, 12), rec) is True   # понедельник


def test_custom_hours():
    rec = RecordingConfig(start="08:30", end="08:45")
    assert is_recording_time(SAT.replace(hour=8, minute=30), rec)
    assert not is_recording_time(SAT.replace(hour=8, minute=45), rec)
