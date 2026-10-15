from datetime import datetime, timedelta

from app.capture import CameraStatus, CameraStream, compute_status

T0 = datetime(2026, 10, 2, 10, 0, 0)


def test_status_online_with_fresh_frame():
    assert compute_status(T0, T0, T0 + timedelta(seconds=9)) == CameraStatus(True, None)


def test_status_offline_since_last_frame():
    assert compute_status(T0, T0 - timedelta(hours=1), T0 + timedelta(seconds=10)) == CameraStatus(False, T0)


def test_status_offline_since_start_when_no_frames():
    started = T0 - timedelta(minutes=5)
    assert compute_status(None, started, T0) == CameraStatus(False, started)


class FakeCap:
    def __init__(self, frames, on_empty):
        self.frames, self.on_empty = list(frames), on_empty

    def isOpened(self):
        return True

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        self.on_empty()
        return False, None

    def release(self):
        pass


def test_stream_keeps_latest_frame():
    stream = CameraStream(
        "box", "rtsp://x", reconnect_s=0,
        opener=lambda url: FakeCap(["f1", "f2"], on_empty=lambda: stream.stop()),
        clock=lambda: T0,
    )
    stream._run()  # синхронно: читает кадры, пока FakeCap не остановит поток
    assert stream.latest() == ("f2", T0)
    assert stream.status() == CameraStatus(True, None)


def test_stream_recovers_from_opener_error():
    """Stream recovers from opener exception and successfully reads frames."""
    call_count = [0]

    def failing_opener(url):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("cv2 connection error")
        return FakeCap(["f1"], on_empty=lambda: stream.stop())

    stream = CameraStream(
        "box", "rtsp://x", reconnect_s=0,
        opener=failing_opener,
        clock=lambda: T0,
    )
    stream._run()
    frame, frame_at = stream.latest()
    assert frame == "f1"
    assert frame_at == T0
