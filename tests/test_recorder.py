import os
import subprocess
from datetime import datetime, timedelta

from app.config import AppConfig, CameraConfig
from app.recorder import CameraRecorder, Recorder, ffmpeg_command

T0 = datetime(2026, 10, 3, 9, 0)


class FakeStdin:
    def __init__(self):
        self.data, self.closed = b"", False

    def write(self, b):
        self.data += b

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProc:
    instances = []

    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        self.returncode, self.stdin, self.terminated, self.killed = None, FakeStdin(), False, False
        FakeProc.instances.append(self)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            if self.stdin.data == b"q" or self.terminated:
                self.returncode = 0
            else:
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed, self.returncode = True, -9


def cfg(tmp_path):
    return AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", True, record=True),
                              CameraConfig("office", "Офис", "xiaomi://b", record=True, record_source="rtsp://sub"),
                              CameraConfig("yard", "Двор", "rtsp://c")], base_dir=tmp_path)


def test_ffmpeg_command(tmp_path):
    c = cfg(tmp_path)
    cmd = ffmpeg_command(c, c.cameras[1])
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == "rtsp://127.0.0.1:8554/office_rec"
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-an" in cmd and "0:a?" not in cmd and "-c" not in cmd   # G.711 не влезает в MP4 — без звука
    assert cmd[cmd.index("-map") + 1] == "0:v"
    assert cmd[cmd.index("-timeout") + 1] == "10000000" and cmd.index("-timeout") < cmd.index("-i")
    assert cmd[cmd.index("-segment_time") + 1] == "900"
    assert cmd[cmd.index("-segment_atclocktime") + 1] == "1"
    assert cmd[cmd.index("-segment_format_options") + 1] == "movflags=+frag_keyframe+empty_moov+default_base_moof"
    assert cmd[-1] == str(c.recordings_dir / "office" / "%Y-%m-%d_%H-%M-%S.mp4")
    assert ffmpeg_command(c, c.cameras[0])[cmd.index("-i") + 1] == "rtsp://127.0.0.1:8554/box"


def test_start_once_and_stop_gracefully(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: T0)
    r.ensure(True)
    r.ensure(True)
    assert len(FakeProc.instances) == 1 and r.is_recording
    assert r.status().state == "recording" and r.status().since == T0
    assert (c.recordings_dir / "box").is_dir()
    assert FakeProc.instances[0].kw["stdin"] == subprocess.PIPE
    r.ensure(False)
    p = FakeProc.instances[0]
    assert p.stdin.data == b"q" and p.returncode == 0 and not p.killed
    assert r.status().state == "idle" and not r.is_recording


def test_restart_after_crash_with_delay(tmp_path):
    FakeProc.instances = []
    now = {"t": T0}
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: now["t"], restart_delay=10)
    r.ensure(True)
    FakeProc.instances[0].returncode = 1
    now["t"] = T0 + timedelta(seconds=3)
    r.ensure(True)
    st = r.status()
    assert st.state == "error" and "1" in st.error and st.since == T0 + timedelta(seconds=3)
    now["t"] = T0 + timedelta(seconds=12)
    r.ensure(True)
    assert len(FakeProc.instances) == 1
    now["t"] = T0 + timedelta(seconds=13)
    r.ensure(True)
    assert len(FakeProc.instances) == 2 and r.status().state == "recording"


def test_kill_when_ffmpeg_ignores_q(tmp_path):
    class Stubborn(FakeProc):
        def wait(self, timeout=None):
            if self.killed:
                return self.returncode
            raise subprocess.TimeoutExpired(self.args, timeout)
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=Stubborn, clock=lambda: T0)
    r.ensure(True)
    r.stop()
    assert Stubborn.instances[-1].killed


def test_popen_failure_is_error(tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=boom, clock=lambda: T0)
    r.ensure(True)
    assert r.status().state == "error" and "ffmpeg" in r.status().error


def test_recorder_follows_schedule(tmp_path):
    FakeProc.instances = []
    now = {"t": T0 - timedelta(minutes=1)}
    c = cfg(tmp_path)
    rec = Recorder(c, popen=FakeProc, clock=lambda: now["t"])
    assert set(rec.cameras) == {"box", "office"}          # yard без record
    rec.tick()
    assert FakeProc.instances == [] and rec.recording_now() == {"box": False, "office": False}
    now["t"] = T0
    rec.tick()
    assert len(FakeProc.instances) == 2 and rec.recording_now() == {"box": True, "office": True}
    now["t"] = T0.replace(hour=18)
    rec.tick()
    assert all(p.returncode == 0 for p in FakeProc.instances)
    assert {k: v.state for k, v in rec.statuses().items()} == {"box": "idle", "office": "idle"}


def test_stop_during_tick_leaves_no_live_ffmpeg(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    calls = {"n": 0}
    holder = {}

    def clock():
        calls["n"] += 1
        if calls["n"] == 1:
            holder["rec"].stop()
        return T0

    rec = Recorder(c, popen=FakeProc, clock=clock)
    holder["rec"] = rec
    rec.tick()
    assert all(p.returncode is not None for p in FakeProc.instances)
    assert FakeProc.instances == []


def test_one_camera_stop_failure_does_not_skip_others(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    rec = Recorder(c, popen=FakeProc, clock=lambda: T0)
    rec.tick()

    def bad():
        raise RuntimeError("boom")
    rec.cameras["box"].stop = bad
    rec.stop()
    assert FakeProc.instances[1].returncode == 0


def test_non_oserror_from_popen_is_error_with_retry_delay(tmp_path):
    n = {"c": 0}

    def boom(*a, **k):
        n["c"] += 1
        raise RuntimeError("bad")
    now = {"t": T0}
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=boom, clock=lambda: now["t"], restart_delay=10)
    r.ensure(True)
    assert r.status().state == "error" and r._log_file is None
    now["t"] = T0 + timedelta(seconds=5)
    r.ensure(True)
    assert n["c"] == 1
    now["t"] = T0 + timedelta(seconds=10)
    r.ensure(True)
    assert n["c"] == 2


def test_tick_isolates_camera_failures(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    rec = Recorder(c, popen=FakeProc, clock=lambda: T0)

    def bad(should):
        raise RuntimeError("boom")
    rec.cameras["box"].ensure = bad
    rec.tick()
    assert rec.recording_now() == {"box": False, "office": True}


def test_error_since_does_not_drift(tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")
    now = {"t": T0}
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=boom, clock=lambda: now["t"], restart_delay=10)
    r.ensure(True)
    now["t"] = T0 + timedelta(seconds=10)
    r.ensure(True)
    assert r.status().state == "error" and r.status().since == T0


def _touch(path, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (when.timestamp(), when.timestamp()))


def _stall_setup(tmp_path):
    FakeProc.instances = []
    now = {"t": T0}
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: now["t"], restart_delay=10)
    r.ensure(True)
    return c, r, now, c.recordings_dir / "box"


def test_stalled_ffmpeg_is_restarted(tmp_path):
    c, r, now, d = _stall_setup(tmp_path)
    _touch(d / "2026-10-03_09-00-00.mp4", T0)
    now["t"] = T0 + timedelta(seconds=110)          # ffmpeg запущен недавно — не трогаем
    r.ensure(True)
    assert r.status().state == "recording" and FakeProc.instances[0].stdin.data == b""
    now["t"] = T0 + timedelta(seconds=121)
    r.ensure(True)
    p = FakeProc.instances[0]
    assert p.stdin.data == b"q" and p.returncode == 0
    st = r.status()
    assert st.state == "error" and st.error == "ffmpeg не пишет файлы — перезапуск"
    now["t"] = T0 + timedelta(seconds=131)
    r.ensure(True)
    assert len(FakeProc.instances) == 2 and r.status().state == "recording"


def test_fresh_files_keep_recording(tmp_path):
    c, r, now, d = _stall_setup(tmp_path)
    _touch(d / "2026-10-03_09-00-00.mp4", T0)
    _touch(d / "2026-10-03_09-15-00.mp4", T0 + timedelta(seconds=200))
    now["t"] = T0 + timedelta(seconds=300)
    r.ensure(True)
    assert r.status().state == "recording" and len(FakeProc.instances) == 1
    assert FakeProc.instances[0].stdin.data == b""


def test_no_files_after_two_minutes_is_stalled(tmp_path):
    c, r, now, d = _stall_setup(tmp_path)
    now["t"] = T0 + timedelta(seconds=121)
    r.ensure(True)
    assert r.status().error == "ffmpeg не пишет файлы — перезапуск"


def test_injected_mtime_of(tmp_path):
    FakeProc.instances = []
    now = {"t": T0}
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: now["t"],
                       mtime_of=lambda p: (T0 + timedelta(seconds=150)).timestamp())
    r.ensure(True)
    _touch(c.recordings_dir / "box" / "2026-10-03_09-00-00.mp4", T0)
    now["t"] = T0 + timedelta(seconds=200)
    r.ensure(True)
    assert r.status().state == "recording"


def test_ffmpeg_log_rotated_when_big(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    c.logs_dir.mkdir(parents=True)
    log = c.logs_dir / "ffmpeg-box.log"
    log.write_bytes(b"a" * (10 * 1024 * 1024 + 1))
    (c.logs_dir / "ffmpeg-box.log.1").write_bytes(b"old")
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: T0)
    r.ensure(True)
    assert (c.logs_dir / "ffmpeg-box.log.1").stat().st_size == 10 * 1024 * 1024 + 1
    assert log.stat().st_size == 0
    r.stop()


def test_ffmpeg_log_small_not_rotated(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    c.logs_dir.mkdir(parents=True)
    log = c.logs_dir / "ffmpeg-box.log"
    log.write_bytes(b"a" * (10 * 1024 * 1024))
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: T0)
    r.ensure(True)
    assert log.stat().st_size == 10 * 1024 * 1024 and not (c.logs_dir / "ffmpeg-box.log.1").exists()
    r.stop()


# --- H.265: пометка hvc1, иначе Safari/iPhone не играют архив ---

FFMPEG_HEVC = """Input #0, rtsp, from 'rtsp://127.0.0.1:8554/box':
  Duration: N/A, start: 0.000000, bitrate: N/A
  Stream #0:0: Video: hevc (Main), yuvj420p(pc), 1920x1080, 12 fps, 12 tbr, 90k tbn
  Stream #0:1: Audio: pcm_alaw, 8000 Hz, mono, s16, 64 kb/s
At least one output file must be specified"""

FFMPEG_H264 = """Input #0, rtsp, from 'rtsp://127.0.0.1:8554/office':
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(progressive), 2304x1296, 20 fps
At least one output file must be specified"""


def test_parse_video_codec():
    from app.recorder import parse_video_codec
    assert parse_video_codec(FFMPEG_HEVC) == "hevc"
    assert parse_video_codec(FFMPEG_H264) == "h264"
    assert parse_video_codec("Connection refused") is None
    assert parse_video_codec("") is None


def test_ffmpeg_command_hevc_gets_hvc1_tag(tmp_path):
    c = cfg(tmp_path)
    cmd = ffmpeg_command(c, c.cameras[0], codec="hevc")
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert cmd.index("-c:v") < cmd.index("-tag:v") < cmd.index("-f")
    for codec in ("h264", None):
        assert "-tag:v" not in ffmpeg_command(c, c.cameras[0], codec=codec)


def test_probe_video_codec_runs_ffmpeg_on_recording_stream(tmp_path):
    from app.recorder import probe_video_codec
    c = cfg(tmp_path)
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=FFMPEG_HEVC)

    assert probe_video_codec(c, c.cameras[1], run=run) == "hevc"
    args, kw = calls[0]
    assert args[0] == "ffmpeg" and args[args.index("-i") + 1] == "rtsp://127.0.0.1:8554/office_rec"
    assert "-rtsp_transport" in args and kw["timeout"] > 0


def test_probe_video_codec_failure_is_none(tmp_path):
    from app.recorder import probe_video_codec
    c = cfg(tmp_path)

    def boom(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    def missing(args, **kw):
        raise FileNotFoundError("ffmpeg")

    assert probe_video_codec(c, c.cameras[0], run=boom) is None
    assert probe_video_codec(c, c.cameras[0], run=missing) is None


def test_recorder_tags_hevc_and_probes_once(tmp_path):
    FakeProc.instances = []
    now = {"t": T0}
    probes = []
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: now["t"],
                       probe=lambda cam: probes.append(cam.id) or "hevc")
    r.ensure(True)
    assert "-tag:v" in FakeProc.instances[0].args and probes == ["box"]
    r.ensure(False)
    r.ensure(True)   # следующий день / новый запуск: кодек уже известен
    assert "-tag:v" in FakeProc.instances[1].args and probes == ["box"]


def test_recorder_reprobes_after_ffmpeg_crash(tmp_path):
    FakeProc.instances = []
    now = {"t": T0}
    answers = iter(["hevc", "h264"])   # камеру переключили на H.264
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: now["t"], restart_delay=10,
                       probe=lambda cam: next(answers))
    r.ensure(True)
    FakeProc.instances[0].returncode = 183
    now["t"] = T0 + timedelta(seconds=1)
    r.ensure(True)
    now["t"] = T0 + timedelta(seconds=12)
    r.ensure(True)
    assert len(FakeProc.instances) == 2 and "-tag:v" not in FakeProc.instances[1].args


def test_recorder_unknown_codec_records_without_tag_and_retries_probe(tmp_path):
    FakeProc.instances = []
    probes = []
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: T0,
                       probe=lambda cam: probes.append(1) or None)
    r.ensure(True)
    assert r.is_recording and "-tag:v" not in FakeProc.instances[0].args
    r.ensure(False)
    r.ensure(True)
    assert len(probes) == 2   # не узнали — пробуем при следующем запуске


def test_recorder_without_probe_does_not_tag(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    r = CameraRecorder(c, c.cameras[0], popen=FakeProc, clock=lambda: T0)
    r.ensure(True)
    assert "-tag:v" not in FakeProc.instances[0].args


def test_recorder_passes_probe_to_cameras(tmp_path):
    FakeProc.instances = []
    c = cfg(tmp_path)
    rec = Recorder(c, popen=FakeProc, clock=lambda: T0, probe=lambda cam: "hevc")
    rec.cameras["box"].ensure(True)
    assert "-tag:v" in FakeProc.instances[0].args
