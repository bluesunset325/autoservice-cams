"""Скриншоты заездов: screenshot_ГГГГ-ММ-ДД_ЧЧ-ММ-СС.jpg"""
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

JPEG_QUALITY = 85


def screenshot_name(at: datetime, exists: Callable[[str], bool]) -> str:
    base = f"screenshot_{at:%Y-%m-%d_%H-%M-%S}"
    name, n = f"{base}.jpg", 2
    while exists(name):
        name, n = f"{base}_{n}.jpg", n + 1
    return name


def save_screenshot(directory: Path, at: datetime, frame: np.ndarray) -> str:
    name = screenshot_name(at, exists=lambda n: (directory / n).exists())
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("Не удалось закодировать скриншот")
    (directory / name).write_bytes(buf.tobytes())  # imwrite не дружит с не-ASCII путями на Windows
    return name
