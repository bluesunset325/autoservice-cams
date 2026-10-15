"""Чтение кадров с камер в фоновом потоке и статус «в сети / нет сигнала»."""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import cv2

log = logging.getLogger(__name__)
STALE_AFTER = timedelta(seconds=10)
TIMEOUT_MS = 5000


@dataclass(frozen=True)
class CameraStatus:
    online: bool
    offline_since: datetime | None


def compute_status(last_frame_at: datetime | None, started_at: datetime, now: datetime) -> CameraStatus:
    if last_frame_at is not None and now - last_frame_at < STALE_AFTER:
        return CameraStatus(True, None)
    return CameraStatus(False, last_frame_at or started_at)


def open_rtsp(url: str):
    return cv2.VideoCapture(
        url, cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, TIMEOUT_MS],
    )


def probe_rtsp(url: str) -> tuple[bool, str]:
    """Разовая проверка камеры для страницы настроек."""
    cap = open_rtsp(url)
    try:
        if not cap.isOpened():
            return False, "Не удалось подключиться"
        ok, frame = cap.read()
        if not ok:
            return False, "Подключились, но кадр не получен"
        h, w = frame.shape[:2]
        return True, f"Работает, {w}×{h}"
    finally:
        cap.release()


class CameraStream:
    def __init__(self, camera_id: str, url: str, reconnect_s: float = 10.0,
                 opener=open_rtsp, clock=datetime.now):
        self.camera_id, self.url = camera_id, url
        self._reconnect_s, self._opener, self._clock = reconnect_s, opener, clock
        self._lock = threading.Lock()
        self._frame = None
        self._frame_at: datetime | None = None
        self._started_at = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self):
        with self._lock:
            return self._frame, self._frame_at

    def status(self) -> CameraStatus:
        with self._lock:
            last = self._frame_at
        return compute_status(last, self._started_at, self._clock())

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = None
            try:
                cap = self._opener(self.url)
                if not cap.isOpened():
                    log.warning("Камера %s: не удалось подключиться", self.camera_id)
                    cap.release()
                    self._stop.wait(self._reconnect_s)
                    continue
                log.info("Камера %s: подключена", self.camera_id)
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        log.warning("Камера %s: поток прервался, переподключение", self.camera_id)
                        break
                    with self._lock:
                        self._frame, self._frame_at = frame, self._clock()
                cap.release()
            except Exception:
                log.exception("Ошибка при чтении камеры %s", self.camera_id)
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            self._stop.wait(self._reconnect_s)
