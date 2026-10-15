"""Запись камер ffmpeg'ом по расписанию: куски MP4 без перекодирования."""
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import resolve_executable
from app.go2rtc import recording_stream
from app.schedule import is_recording_time

log = logging.getLogger(__name__)
MOVFLAGS = "movflags=+frag_keyframe+empty_moov+default_base_moof"
RTSP_TIMEOUT_US = "10000000"        # 10 с без данных от go2rtc — ffmpeg завершается сам
STALL = timedelta(seconds=120)      # ffmpeg жив, но файлы не растут столько — перезапуск
LOG_MAX_BYTES = 10 * 1024 * 1024
STALL_MESSAGE = "ffmpeg не пишет файлы — перезапуск"
PROBE_TIMEOUT_S = 20
_VIDEO_CODEC = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+)")


def parse_video_codec(ffmpeg_output: str) -> str | None:
    m = _VIDEO_CODEC.search(ffmpeg_output or "")
    return m.group(1) if m else None


def probe_video_codec(cfg, cam, run=subprocess.run) -> str | None:
    """Кодек видео в потоке записи (hevc, h264…) по выводу «ffmpeg -i»; None — не удалось узнать."""
    cmd = [resolve_executable(cfg.base_dir, cfg.recording.ffmpeg_path), "-hide_banner",
           "-rtsp_transport", "tcp", "-timeout", RTSP_TIMEOUT_US, "-i", recording_stream(cam)]
    try:
        out = run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                  timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Камера %s: не удалось узнать кодек: %s", cam.id, e)
        return None
    return parse_video_codec(out.stderr)


def ffmpeg_command(cfg, cam, codec: str | None = None) -> list[str]:
    rec = cfg.recording
    out = cfg.recordings_dir / cam.id / "%Y-%m-%d_%H-%M-%S.mp4"
    return [
        resolve_executable(cfg.base_dir, rec.ffmpeg_path), "-hide_banner", "-loglevel", "warning",
        "-rtsp_transport", "tcp", "-timeout", RTSP_TIMEOUT_US, "-i", recording_stream(cam),
        "-map", "0:v", "-an", "-c:v", "copy",   # без звука: G.711 iCSee/XMEye нельзя копировать в MP4
        # H.265 с меткой hev1 (по умолчанию) Safari/iPhone не играют; для H.264 hvc1 ломает запись
        *(["-tag:v", "hvc1"] if codec == "hevc" else []),
        "-f", "segment", "-segment_time", str(rec.segment_minutes * 60), "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1",
        "-segment_format", "mp4", "-segment_format_options", MOVFLAGS,
        str(out),
    ]


@dataclass(frozen=True)
class RecorderStatus:
    state: str               # recording | idle | error
    since: datetime | None
    error: str | None


class CameraRecorder:
    def __init__(self, cfg, cam, popen=subprocess.Popen, clock=datetime.now, restart_delay: float = 10.0,
                 mtime_of=lambda p: p.stat().st_mtime, probe=None):
        self._cfg, self._cam, self._popen, self._clock = cfg, cam, popen, clock
        self._probe = probe          # cam -> кодек или None; без probe — запись без метки кодека
        self._codec: str | None = None
        self._mtime_of = mtime_of
        self._spawned_at: datetime | None = None
        self._delay = timedelta(seconds=restart_delay)
        self._proc = None
        self._log_file = None
        self._retry_at: datetime | None = None
        self._status = RecorderStatus("idle", None, None)
        self._lock = threading.Lock()
        self._closed = False

    @property
    def is_recording(self) -> bool:
        return self._status.state == "recording" and self._proc is not None and self._proc.poll() is None

    def status(self) -> RecorderStatus:
        return self._status

    def ensure(self, should_record: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._ensure(should_record)

    def _ensure(self, should_record: bool) -> None:
        now = self._clock()
        alive = self._proc is not None and self._proc.poll() is None
        if not should_record:
            if self._proc is not None:
                self._shutdown()
            if self._status.state != "idle":
                self._status = RecorderStatus("idle", now, None)
            self._retry_at = None
            return
        if alive:
            if self._stalled(now):
                self._shutdown()
                self._fail(STALL_MESSAGE, now)
            return
        if self._proc is not None:   # ffmpeg упал сам
            code = self._proc.poll()
            self._close_log()
            self._proc = None
            self._codec = None       # вдруг камеру переключили на другой кодек — узнать заново
            self._fail(f"ffmpeg завершился с кодом {code}", now)
        if self._retry_at is not None and now < self._retry_at:
            return
        self._spawn(now)

    def _stalled(self, now: datetime) -> bool:
        if self._spawned_at is None or now - self._spawned_at <= STALL:
            return False
        newest = None
        for p in (self._cfg.recordings_dir / self._cam.id).glob("*.mp4"):
            try:
                m = self._mtime_of(p)
            except OSError:
                continue
            newest = m if newest is None else max(newest, m)
        return newest is None or now.timestamp() - newest > STALL.total_seconds()

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            if self._proc is not None:
                self._shutdown()
            if self._status.state != "idle":
                self._status = RecorderStatus("idle", self._clock(), None)

    def _spawn(self, now: datetime) -> None:
        try:
            (self._cfg.recordings_dir / self._cam.id).mkdir(parents=True, exist_ok=True)
            self._cfg.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._cfg.logs_dir / f"ffmpeg-{self._cam.id}.log"
            if log_path.is_file() and log_path.stat().st_size > LOG_MAX_BYTES:
                os.replace(log_path, log_path.with_name(log_path.name + ".1"))
            if self._codec is None and self._probe is not None:
                self._codec = self._probe(self._cam)
            self._log_file = open(log_path, "ab")
            self._proc = self._popen(ffmpeg_command(self._cfg, self._cam, self._codec), stdin=subprocess.PIPE,
                                     stdout=self._log_file, stderr=subprocess.STDOUT)
        except Exception as e:
            self._close_log()
            self._proc = None
            self._fail(f"не удалось запустить ffmpeg: {e}", now)
            return
        self._retry_at = None
        self._spawned_at = now
        self._status = RecorderStatus("recording", now, None)
        log.info("Запись камеры %s начата", self._cam.id)

    def _fail(self, message: str, now: datetime) -> None:
        log.warning("Камера %s: %s", self._cam.id, message)
        since = self._status.since if self._status.state == "error" else now
        self._status = RecorderStatus("error", since, message)
        self._retry_at = now + self._delay

    def _shutdown(self) -> None:
        proc, self._proc = self._proc, None
        if proc.poll() is None:
            try:
                proc.stdin.write(b"q")   # ffmpeg корректно закрывает текущий кусок
                proc.stdin.flush()
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self._close_log()
        log.info("Запись камеры %s остановлена", self._cam.id)

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


class Recorder:
    """Все записывающие камеры; tick() раз в tick_s включает/выключает их по расписанию."""

    def __init__(self, cfg, popen=subprocess.Popen, clock=datetime.now, tick_s: float = 5.0, probe=None):
        self._cfg, self._clock, self._tick_s = cfg, clock, tick_s
        self.cameras = {c.id: CameraRecorder(cfg, c, popen, clock, probe=probe)
                        for c in cfg.cameras if c.record}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        should = is_recording_time(self._clock(), self._cfg.recording)
        for r in self.cameras.values():
            try:
                r.ensure(should)
            except Exception:
                log.exception("Ошибка записи камеры")

    def recording_now(self) -> dict[str, bool]:
        return {cid: r.is_recording for cid, r in self.cameras.items()}

    def statuses(self) -> dict[str, RecorderStatus]:
        return {cid: r.status() for cid, r in self.cameras.items()}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=15)
        for r in self.cameras.values():
            try:
                r.stop()
            except Exception:
                log.exception("Не удалось остановить запись камеры")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Ошибка планировщика записи")
            self._stop.wait(self._tick_s)
