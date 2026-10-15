from datetime import datetime

import cv2
import numpy as np

from app.screenshots import save_screenshot, screenshot_name

AT = datetime(2026, 10, 2, 14, 35, 7)


def test_name_format():
    assert screenshot_name(AT, exists=lambda n: False) == "screenshot_2026-10-02_14-35-07.jpg"


def test_name_collision_gets_suffix():
    taken = {"screenshot_2026-10-02_14-35-07.jpg", "screenshot_2026-10-02_14-35-07_2.jpg"}
    assert screenshot_name(AT, exists=taken.__contains__) == "screenshot_2026-10-02_14-35-07_3.jpg"


def test_save_writes_readable_jpeg(tmp_path):
    frame = np.full((60, 80, 3), 200, np.uint8)
    name = save_screenshot(tmp_path, AT, frame)
    assert name == "screenshot_2026-10-02_14-35-07.jpg"
    img = cv2.imread(str(tmp_path / name))
    assert img.shape == (60, 80, 3)
    assert save_screenshot(tmp_path, AT, frame) == "screenshot_2026-10-02_14-35-07_2.jpg"
