"""Настройки программы: читаются из config.yaml."""
import math
import os
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path

import yaml

CAMERA_ID_RE = re.compile(r"^[a-z0-9_]+$")
_TOP_LEVEL_KEYS = ("host", "port", "analyze_fps", "data_dir", "go2rtc_path", "secret_key")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass
class CameraConfig:
    id: str
    name: str
    source: str
    recognize: bool = False
    record: bool = False
    record_source: str | None = None


@dataclass
class TrackerConfig:
    confirm_reads: int = 3
    confirm_window_s: float = 5.0
    exit_after_s: float = 180.0
    seen_write_interval_s: float = 10.0


@dataclass
class RecordingConfig:
    start: str = "09:00"
    end: str = "18:00"
    days: list[str] = field(default_factory=lambda: list(WEEKDAYS))
    segment_minutes: int = 15
    ffmpeg_path: str = "ffmpeg"
    rclone_path: str = "rclone"
    rclone_remote: str = "gdrive:"
    rclone_config: str = "rclone.conf"
    drive_folder: str = "Автосервис"
    upload_bwlimit_mbit: float = 10
    drive_retention_days: int = 45
    local_cleanup_weekday: str = "mon"
    low_space_gb: float = 20

    @property
    def start_time(self) -> time:
        return datetime.strptime(self.start, "%H:%M").time()

    @property
    def end_time(self) -> time:
        return datetime.strptime(self.end, "%H:%M").time()


@dataclass
class AppConfig:
    cameras: list[CameraConfig]
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    host: str = "0.0.0.0"
    port: int = 8000
    analyze_fps: float = 2.0
    data_dir: str = "."
    go2rtc_path: str = "go2rtc"
    secret_key: str = ""
    base_dir: Path = field(default=Path("."), repr=False, compare=False)

    @property
    def data_path(self) -> Path:
        return (self.base_dir / self.data_dir).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_path / "data.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_path / "screenshots"

    @property
    def backups_dir(self) -> Path:
        return self.data_path / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_path / "logs"

    @property
    def recordings_dir(self) -> Path:
        return self.data_path / "recordings"

    def camera(self, camera_id: str) -> CameraConfig | None:
        return next((c for c in self.cameras if c.id == camera_id), None)


def validate_limits(cfg: AppConfig) -> None:
    """Пороги должны быть конечными числами в разумных пределах; иначе ValueError."""
    t = cfg.tracker
    checks = [
        ("tracker.confirm_reads", t.confirm_reads, 100),
        ("tracker.confirm_window_s", t.confirm_window_s, 600),
        ("tracker.exit_after_s", t.exit_after_s, 86400),
        ("tracker.seen_write_interval_s", t.seen_write_interval_s, 3600),
        ("analyze_fps", cfg.analyze_fps, 10),
    ]
    for name, value, upper in checks:
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or not 0 < value <= upper:
            raise ValueError(f"Ошибка в config.yaml ({name}): число должно быть больше 0 и не больше {upper}, "
                             f"получено {value!r}")


def _parse_hhmm(value, name: str) -> time:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError:
        raise ValueError(f"Ошибка в config.yaml (recording.{name}): время в формате ЧЧ:ММ, "
                         f"получено {value!r}") from None


def validate_recording(rec: RecordingConfig) -> None:
    start, end = _parse_hhmm(rec.start, "start"), _parse_hhmm(rec.end, "end")
    if end <= start:
        raise ValueError("Ошибка в config.yaml (recording): конец записи должен быть позже начала")
    if not isinstance(rec.days, list) or not rec.days or any(d not in WEEKDAYS for d in rec.days):
        raise ValueError(f"Ошибка в config.yaml (recording.days): укажите дни из {', '.join(WEEKDAYS)}")
    if rec.local_cleanup_weekday not in WEEKDAYS:
        raise ValueError(f"Ошибка в config.yaml (recording.local_cleanup_weekday): один из {', '.join(WEEKDAYS)}")
    checks = [
        ("segment_minutes", rec.segment_minutes, 60, True),
        ("drive_retention_days", rec.drive_retention_days, 3650, True),
        ("upload_bwlimit_mbit", rec.upload_bwlimit_mbit, 1000, False),
        ("low_space_gb", rec.low_space_gb, 10000, False),
    ]
    for name, value, upper, integer in checks:
        if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) \
                or not math.isfinite(value) or not 0 < value <= upper:
            kind = "целое число" if integer else "число"
            raise ValueError(f"Ошибка в config.yaml (recording.{name}): {kind} больше 0 и не больше {upper}, "
                             f"получено {value!r}")
    if rec.upload_bwlimit_mbit < 0.1:
        raise ValueError("Ошибка в config.yaml (recording.upload_bwlimit_mbit): не меньше 0.1 Мбит/с, "
                         f"получено {rec.upload_bwlimit_mbit!r}")
    if not isinstance(rec.drive_folder, str) or not rec.drive_folder.strip() or rec.drive_folder.startswith("/"):
        raise ValueError("Ошибка в config.yaml (recording.drive_folder): непустое имя папки без «/» в начале")


def resolve_executable(base_dir: Path, name: str) -> str:
    local = Path(base_dir) / name
    return str(local) if local.is_file() else name


def load_config(path) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Load cameras with error handling
    try:
        cameras = [CameraConfig(**c) for c in raw.get("cameras") or []]
    except (TypeError, AttributeError) as e:
        raise ValueError(f"Ошибка в config.yaml (cameras): {e}") from e

    seen: set[str] = set()
    for cam in cameras:
        # Robust id check: must be string and match pattern
        if not isinstance(cam.id, str) or not CAMERA_ID_RE.match(cam.id):
            raise ValueError(f"Неверный id камеры {cam.id!r}: допустимы только a-z, 0-9, _")
        if cam.id in seen:
            raise ValueError(f"Повторяется id камеры {cam.id!r}")
        seen.add(cam.id)

    # Load tracker with error handling
    try:
        tracker = TrackerConfig(**(raw.get("tracker") or {}))
    except (TypeError, AttributeError) as e:
        raise ValueError(f"Ошибка в config.yaml (tracker): {e}") from e

    # Load recording with error handling
    try:
        recording = RecordingConfig(**(raw.get("recording") or {}))
    except (TypeError, AttributeError) as e:
        raise ValueError(f"Ошибка в config.yaml (recording): {e}") from e

    cfg = AppConfig(
        cameras=cameras,
        tracker=tracker,
        recording=recording,
        base_dir=path.parent.resolve(),
        **{k: raw[k] for k in _TOP_LEVEL_KEYS if k in raw},
    )
    validate_limits(cfg)
    validate_recording(cfg.recording)
    if not cfg.secret_key:
        cfg.secret_key = secrets.token_hex(32)
        save_config(cfg, path)
    return cfg


def save_config(cfg: AppConfig, path) -> None:
    data = asdict(cfg)
    data.pop("base_dir")
    yaml_content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    # Atomic write: write to temp file, then rename
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(yaml_content, encoding="utf-8")
    os.replace(str(tmp_path), str(path))
