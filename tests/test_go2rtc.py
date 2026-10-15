import subprocess
import yaml

from app.config import AppConfig, CameraConfig
from app.go2rtc import Go2rtcProcess, go2rtc_config, go2rtc_rtsp_url, write_go2rtc_config


def cfg(tmp_path):
    return AppConfig(
        cameras=[CameraConfig("box", "Бокс", "rtsp://u:p@10.0.0.5/s", True),
                 CameraConfig("office", "Офис", "rtsp://10.0.0.6/s")],
        base_dir=tmp_path,
    )


def test_config_binds_to_localhost_and_lists_streams(tmp_path):
    conf = go2rtc_config(cfg(tmp_path))
    assert conf["api"] == {"listen": "127.0.0.1:1984", "base_path": "/go2rtc"}
    assert conf["log"] == {"level": "warn"}
    assert conf["rtsp"] == {"listen": "127.0.0.1:8554"}
    assert conf["webrtc"] == {"listen": "127.0.0.1:8555"}
    assert conf["streams"] == {"box": "rtsp://u:p@10.0.0.5/s", "office": "rtsp://10.0.0.6/s"}


def test_write_config(tmp_path):
    path = write_go2rtc_config(cfg(tmp_path))
    assert path == tmp_path.resolve() / "go2rtc.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["streams"]["box"] == "rtsp://u:p@10.0.0.5/s"


def test_write_config_keeps_go2rtc_sections(tmp_path):
    path = tmp_path / "go2rtc.yaml"
    path.write_text("xiaomi:\n  '123': token\nstreams:\n  old: rtsp://old\n", encoding="utf-8")
    data = yaml.safe_load(write_go2rtc_config(cfg(tmp_path)).read_text(encoding="utf-8"))
    assert data["xiaomi"] == {"123": "token"}
    assert set(data["streams"]) == {"box", "office"}


import pytest


@pytest.mark.parametrize("content", ["streams: [unclosed\n  - : :", "- just\n- a list\n", "just a string"])
def test_write_config_recovers_from_corrupt_file(tmp_path, content, caplog):
    path = tmp_path / "go2rtc.yaml"
    path.write_text(content, encoding="utf-8")
    (tmp_path / "go2rtc.yaml.bad").write_text("older", encoding="utf-8")
    with caplog.at_level("WARNING"):
        data = yaml.safe_load(write_go2rtc_config(cfg(tmp_path)).read_text(encoding="utf-8"))
    assert set(data["streams"]) == {"box", "office"}
    assert (tmp_path / "go2rtc.yaml.bad").read_text(encoding="utf-8") == content
    assert any("go2rtc.yaml" in r.message for r in caplog.records)


def test_write_config_atomic(tmp_path):
    """write_go2rtc_config creates data_path and writes atomically."""
    config_dir = tmp_path / "subdir" / "data"
    c = cfg(tmp_path)
    c.data_dir = "subdir/data"
    write_go2rtc_config(c)
    # data_path was created
    assert config_dir.exists()
    # no .tmp file left
    assert not list(config_dir.glob("*.tmp"))
    # config was written
    assert (config_dir / "go2rtc.yaml").exists()


def test_rtsp_url():
    assert go2rtc_rtsp_url("box") == "rtsp://127.0.0.1:8554/box"


def test_process_restarts_after_exit(tmp_path):
    calls = []
    proc = None

    class FakeProc:
        def __init__(self, args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                proc._stop.set()  # второй запуск — останавливаем супервизор

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return 1

    proc = Go2rtcProcess(cfg(tmp_path), popen=FakeProc, restart_delay=0)
    proc._run(write_go2rtc_config(proc._cfg))
    assert len(calls) == 2
    assert calls[0][1:] == ["-config", str(tmp_path.resolve() / "go2rtc.yaml")]


def test_stop_terminates_process(tmp_path):
    """stop() calls terminate and wait on running process."""
    terminate_calls = []
    wait_calls = []

    class FakeProc:
        def __init__(self, args, **kwargs):
            pass

        def terminate(self):
            terminate_calls.append(True)

        def wait(self, timeout=None):
            wait_calls.append(timeout)

        def poll(self):
            return None  # process is running

    proc = Go2rtcProcess(cfg(tmp_path), popen=FakeProc)
    proc._proc = FakeProc([])
    proc.stop()
    assert len(terminate_calls) == 1
    assert 5 in wait_calls  # wait(timeout=5)


def test_kill_called_on_terminate_timeout(tmp_path):
    """kill() is called if terminate doesn't stop process within timeout."""
    kill_calls = []
    wait_calls = []

    class FakeProc:
        def __init__(self, args, **kwargs):
            pass

        def terminate(self):
            pass

        def kill(self):
            kill_calls.append(True)

        def wait(self, timeout=None):
            wait_calls.append(timeout)
            if timeout == 5:
                raise subprocess.TimeoutExpired("go2rtc", 5)

        def poll(self):
            return None

    proc = Go2rtcProcess(cfg(tmp_path), popen=FakeProc)
    proc._proc = FakeProc([])
    proc.stop()
    assert len(kill_calls) == 1


def test_process_logs_output(tmp_path):
    """go2rtc output is logged to logs_dir/go2rtc.log."""
    c = cfg(tmp_path)
    calls = []
    proc_ref = None

    class FakeProc:
        def __init__(self, args, **kwargs):
            calls.append(kwargs)
            proc_ref._stop.set()  # stop after first iteration

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 1  # return 1 to indicate already terminated

        def terminate(self):
            pass

        def kill(self):
            pass

    proc = Go2rtcProcess(c, popen=FakeProc, restart_delay=0)
    proc_ref = proc
    proc._run(write_go2rtc_config(proc._cfg))
    # logs_dir was created
    assert c.logs_dir.exists()
    # log file was created
    assert (c.logs_dir / "go2rtc.log").exists()
    # stdout was passed as file handle
    assert "stdout" in calls[0]
    stdout_arg = calls[0]["stdout"]
    assert hasattr(stdout_arg, "write")


def test_recording_streams(tmp_path):
    from app.go2rtc import recording_stream
    c = AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://main", True, record=True),
                           CameraConfig("office", "Офис", "xiaomi://x", record=True, record_source="rtsp://sub")],
                  base_dir=tmp_path)
    streams = go2rtc_config(c)["streams"]
    assert streams == {"box": "rtsp://main", "office": "xiaomi://x", "office_rec": "rtsp://sub"}
    assert recording_stream(c.cameras[0]) == "rtsp://127.0.0.1:8554/box"
    assert recording_stream(c.cameras[1]) == "rtsp://127.0.0.1:8554/office_rec"
