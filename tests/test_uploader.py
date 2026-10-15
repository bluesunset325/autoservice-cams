import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest

from app.archive import RecordingRepo
from app.config import AppConfig, CameraConfig
from app.db import Database
from app.uploader import Rclone, RcloneError, Uploader, bwlimit_arg

T = datetime(2026, 10, 3, 12, 0)


def fake_popen(result=lambda cmd: (0, "", ""), seen=None):
    """Подмена subprocess.Popen для rclone: result(cmd) -> (код, stdout, stderr)."""
    class P:
        def __init__(self, cmd, **kw):
            self.args, self.kw, self.returncode, self.timeout = cmd, kw, None, None
            self.killed = self.terminated = False
            if seen is not None:
                seen.append(self)

        def communicate(self, timeout=None):
            self.timeout = timeout
            self.returncode, out, err = result(self.args)
            return out, err

        def poll(self):
            return self.returncode
    return P


def cfg(tmp_path):
    return AppConfig(cameras=[CameraConfig("box", "Бокс", "rtsp://a", record=True)], base_dir=tmp_path)


def test_bwlimit_arg():
    assert bwlimit_arg(10) == "1220K"
    assert bwlimit_arg(0.5) == "61K"


def test_rclone_commands(tmp_path):
    seen = []

    def result(cmd):
        return 0, json.dumps([{"Path": "09-00-00.mp4", "ID": "FILEID"}]) if "lsjson" in cmd else "", ""
    r = Rclone(cfg(tmp_path), popen=fake_popen(result, seen))
    r.copyto(tmp_path / "a.mp4", "Автосервис/Бокс/2026-10-03/09-00-00.mp4")
    assert r.file_id("Автосервис/Бокс/2026-10-03/09-00-00.mp4") == "FILEID"
    conf = str(tmp_path / "rclone.conf")
    calls = [p.args for p in seen]
    assert calls[0] == ["rclone", "--config", conf, "copyto", str(tmp_path / "a.mp4"),
                        "gdrive:Автосервис/Бокс/2026-10-03/09-00-00.mp4", "--bwlimit", "1220K", "--retries", "3"]
    assert calls[1][:4] == ["rclone", "--config", conf, "lsjson"]


def test_rclone_errors(tmp_path):
    r = Rclone(cfg(tmp_path), popen=fake_popen(lambda cmd: (1, "", "no network")))
    with pytest.raises(RcloneError, match="no network"):
        r.about()

    def missing(cmd, **kw):
        raise FileNotFoundError("rclone")
    with pytest.raises(RcloneError):
        Rclone(cfg(tmp_path), popen=missing).about()
    empty = Rclone(cfg(tmp_path), popen=fake_popen(lambda cmd: (0, "[]", "")))
    assert empty.file_id("x") is None


class FakeRclone:
    def __init__(self, fail_on=None, ids=True):
        self.copied, self.fail_on, self.ids = [], fail_on, ids

    def copyto(self, local, path):
        if self.fail_on is not None and len(self.copied) == self.fail_on:
            raise RcloneError("нет интернета")
        self.copied.append((local.name, path))

    def file_id(self, path):
        return f"ID:{path}" if self.ids else None

    def terminate(self):
        self.terminated = True


def setup(tmp_path, n=3):
    c = cfg(tmp_path)
    d = c.recordings_dir / "box"
    d.mkdir(parents=True)
    repo = RecordingRepo(Database(":memory:"))
    for i in range(n):
        name = f"2026-10-03_09-{i * 15:02d}-00.mp4"
        (d / name).write_bytes(b"x" * 10)
        repo.add("box", datetime(2026, 10, 3, 9, i * 15), name, 10)
    return c, repo


def test_uploads_in_order_and_marks(tmp_path):
    c, repo = setup(tmp_path)
    rc = FakeRclone()
    up = Uploader(c, repo, rc, clock=lambda: T)
    assert up.run_once() is True
    assert [p for _, p in rc.copied] == [f"Автосервис/Бокс/2026-10-03/09-{m}-00.mp4" for m in ("00", "15", "30")]
    assert repo.pending_upload() == []
    assert repo.uploaded_on_disk()[0].drive_id == "ID:Автосервис/Бокс/2026-10-03/09-00-00.mp4"
    assert [r.drive_path for r in repo.uploaded_on_disk()] == [p for _, p in rc.copied]
    st = up.status()
    assert (st.queue_count, st.queue_bytes, st.last_ok_at, st.last_error) == (0, 0, T, None)


def test_stops_on_error_and_reports(tmp_path):
    c, repo = setup(tmp_path)
    up = Uploader(c, repo, FakeRclone(fail_on=1), clock=lambda: T)
    assert up.run_once() is False
    assert len(repo.pending_upload()) == 2
    st = up.status()
    assert st.last_error == "нет интернета" and st.last_error_at == T and st.queue_count == 2


def test_no_id_means_not_uploaded(tmp_path):
    c, repo = setup(tmp_path, n=1)
    up = Uploader(c, repo, FakeRclone(ids=False), clock=lambda: T)
    assert up.run_once() is False
    assert len(repo.pending_upload()) == 1
    assert "id" in up.status().last_error


def test_vanished_file_is_marked_gone(tmp_path):
    c, repo = setup(tmp_path, n=1)
    (c.recordings_dir / "box" / "2026-10-03_09-00-00.mp4").unlink()
    up = Uploader(c, repo, FakeRclone(), clock=lambda: T)
    assert up.run_once() is True
    assert repo.pending_upload() == [] and repo.uploaded_on_disk() == []


def test_rclone_run_uses_utf8_and_copyto_timeout(tmp_path):
    seen = []
    r = Rclone(cfg(tmp_path), popen=fake_popen(seen=seen))
    r.copyto(tmp_path / "a.mp4", "x/a.mp4")
    r.about()
    for p in seen:
        assert p.kw == {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
                        "encoding": "utf-8", "errors": "replace"}
    assert seen[0].timeout == 10800 and seen[1].timeout == 60


@pytest.mark.parametrize("out", ["not json", '{"a":1}', "[1]", '[{"a":1}]x', '["s"]'])
def test_file_id_bad_output_is_rclone_error(tmp_path, out):
    r = Rclone(cfg(tmp_path), popen=fake_popen(lambda cmd: (0, out, "")))
    with pytest.raises(RcloneError, match="lsjson"):
        r.file_id("x")


def test_file_id_error_sets_last_error(tmp_path):
    class Bad(FakeRclone):
        def file_id(self, path):
            raise RcloneError("rclone lsjson: неожиданный ответ: x")
    c, repo = setup(tmp_path, n=1)
    up = Uploader(c, repo, Bad(), clock=lambda: T)
    assert up.run_once() is False
    assert "lsjson" in up.status().last_error


def test_successful_round_clears_error(tmp_path):
    c, repo = setup(tmp_path, n=1)
    up = Uploader(c, repo, FakeRclone(fail_on=0), clock=lambda: T)
    assert up.run_once() is False
    (c.recordings_dir / "box" / "2026-10-03_09-00-00.mp4").unlink()
    assert up.run_once() is True
    st = up.status()
    assert st.last_error is None and st.last_error_at is None
    up._fail("again")
    assert up.run_once() is True  # empty queue
    assert up.status().last_error is None


def test_rclone_error_carries_returncode(tmp_path):
    r = Rclone(cfg(tmp_path), popen=fake_popen(lambda cmd: (3, "", "dir not found")))
    with pytest.raises(RcloneError) as e:
        r.about()
    assert e.value.returncode == 3

    def missing(cmd, **kw):
        raise FileNotFoundError("rclone")
    with pytest.raises(RcloneError) as e:
        Rclone(cfg(tmp_path), popen=missing).about()
    assert e.value.returncode is None
    assert RcloneError("x").returncode is None


class HangingProc:
    """rclone, который висит, пока его не остановят."""
    instances = []

    def __init__(self, cmd, **kw):
        self.args, self.returncode = cmd, None
        self.terminated = self.killed = False
        self.ignore_terminate = False
        self._done = threading.Event()
        self.waits = []
        HangingProc.instances.append(self)

    def communicate(self, timeout=None):
        if not self._done.wait(timeout if timeout is not None else 5):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return "", "killed"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.returncode = 1
            self._done.set()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if not self._done.wait(timeout if timeout is not None else 5):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9
        self._done.set()


def _start_call(r):
    errors = []

    def call():
        try:
            r.copyto(Path("a.mp4"), "x/a.mp4")
        except RcloneError as e:
            errors.append(e)
    t = threading.Thread(target=call)
    t.start()
    for _ in range(200):
        if HangingProc.instances and r._running:
            break
        threading.Event().wait(0.01)
    return t, errors


def test_terminate_kills_in_flight_rclone(tmp_path):
    HangingProc.instances = []
    r = Rclone(cfg(tmp_path), popen=HangingProc)
    t, errors = _start_call(r)
    r.terminate()
    t.join(5)
    p = HangingProc.instances[0]
    assert not t.is_alive() and p.terminated and not p.killed
    assert len(errors) == 1 and r._running == set()


def test_terminate_kills_when_terminate_ignored(tmp_path):
    HangingProc.instances = []
    r = Rclone(cfg(tmp_path), popen=HangingProc)
    orig = HangingProc.__init__

    def stubborn(self, cmd, **kw):
        orig(self, cmd, **kw)
        self.ignore_terminate = True
    HangingProc.__init__ = stubborn
    try:
        t, errors = _start_call(r)
    finally:
        HangingProc.__init__ = orig
    r.terminate(wait_s=0.05)
    t.join(5)
    p = HangingProc.instances[0]
    assert p.terminated and p.killed and 0.05 in p.waits and len(errors) == 1


def test_no_new_rclone_after_terminate(tmp_path):
    seen = []
    r = Rclone(cfg(tmp_path), popen=fake_popen(seen=seen))
    r.terminate()
    with pytest.raises(RcloneError):
        r.about()
    assert seen == []


def test_rclone_timeout_kills_and_raises(tmp_path):
    HangingProc.instances = []
    r = Rclone(cfg(tmp_path), popen=HangingProc)
    with pytest.raises(RcloneError) as e:
        r._call("about", "gdrive:", timeout=0.01)
    assert HangingProc.instances[0].killed and e.value.returncode is None and r._running == set()


def test_uploader_stop_terminates_rclone(tmp_path):
    c, repo = setup(tmp_path, n=0)
    rc = FakeRclone()
    Uploader(c, repo, rc, clock=lambda: T).stop()
    assert rc.terminated
