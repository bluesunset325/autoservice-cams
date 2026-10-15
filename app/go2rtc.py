"""go2rtc: раздаёт видео камер в браузер и RTSP-рестрим для анализа."""
import logging
import os
import subprocess
import threading
from pathlib import Path

import yaml

from app.config import AppConfig

log = logging.getLogger(__name__)

API_LISTEN = "127.0.0.1:1984"
BASE_PATH = "/go2rtc"
RTSP_LISTEN = "127.0.0.1:8554"
WEBRTC_LISTEN = "127.0.0.1:8555"


def go2rtc_rtsp_url(camera_id: str) -> str:
    return f"rtsp://{RTSP_LISTEN}/{camera_id}"


def recording_stream(cam) -> str:
    """Поток, который пишет ffmpeg: облегчённый <id>_rec, если задан record_source."""
    return go2rtc_rtsp_url(f"{cam.id}_rec" if cam.record_source else cam.id)


def go2rtc_config(cfg: AppConfig) -> dict:
    return {
        "api": {"listen": API_LISTEN, "base_path": BASE_PATH},
        "log": {"level": "warn"},
        "rtsp": {"listen": RTSP_LISTEN},
        "webrtc": {"listen": WEBRTC_LISTEN},
        "streams": {
            **{c.id: c.source for c in cfg.cameras},
            **{f"{c.id}_rec": c.record_source for c in cfg.cameras if c.record and c.record_source},
        },
    }


def write_go2rtc_config(cfg: AppConfig) -> Path:
    """Перезаписывает только наши разделы; то, что дописал сам go2rtc
    (например, токены аккаунта Xiaomi), сохраняется."""
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    path = cfg.data_path / "go2rtc.yaml"
    existing = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise yaml.YAMLError("верхний уровень не словарь")
            existing = loaded
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            bad = path.with_suffix(path.suffix + ".bad")
            log.warning("go2rtc.yaml повреждён (%s): сохранён как %s, создан заново", e, bad.name)
            os.replace(str(path), str(bad))
    merged = {**existing, **go2rtc_config(cfg)}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(str(tmp_path), str(path))
    return path


class Go2rtcProcess:
    """Запускает go2rtc и перезапускает его, если он упал."""

    def __init__(self, cfg: AppConfig, popen=subprocess.Popen, restart_delay: float = 5.0):
        self._cfg, self._popen, self._restart_delay = cfg, popen, restart_delay
        self._stop = threading.Event()
        self._proc = None

    def _executable(self) -> str:
        local = self._cfg.base_dir / self._cfg.go2rtc_path
        return str(local) if local.is_file() else self._cfg.go2rtc_path

    def start(self) -> None:
        path = write_go2rtc_config(self._cfg)
        threading.Thread(target=self._run, args=(path,), name="go2rtc", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate()

    def _terminate(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def _run(self, config_path: Path) -> None:
        self._cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(self._cfg.logs_dir / "go2rtc.log", "ab")
        try:
            while not self._stop.is_set():
                try:
                    self._proc = self._popen(
                        [self._executable(), "-config", str(config_path)],
                        stdout=log_file, stderr=subprocess.STDOUT,
                    )
                except OSError:
                    log.exception("Не удалось запустить go2rtc (%s)", self._executable())
                    self._stop.wait(self._restart_delay)
                    continue
                if self._stop.is_set():
                    self._terminate()
                    break
                code = self._proc.wait()
                if not self._stop.is_set():
                    log.warning("go2rtc завершился с кодом %s, перезапуск через %s с", code, self._restart_delay)
                    self._stop.wait(self._restart_delay)
        finally:
            log_file.close()
