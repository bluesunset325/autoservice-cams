"""Находит готовые куски записи и заносит их в таблицу recordings."""
import logging
import time
from pathlib import Path

from app.archive import RecordingRepo, parse_segment_name

log = logging.getLogger(__name__)
SETTLE_S = 60
MIN_SEGMENT_BYTES = 4096   # меньше — ни одного кадра (только заголовок MP4): ffmpeg остановили на границе куска


def find_ready_segments(cam_dir: Path, recording: bool, now_ts: float, settle_s: float = SETTLE_S) -> list[Path]:
    if not cam_dir.is_dir():
        return []
    files = sorted(p for p in cam_dir.glob("*.mp4") if parse_segment_name(p.name))
    if not files:
        return []
    *older, newest = files
    ready = list(older)
    try:
        if not recording and now_ts - newest.stat().st_mtime >= settle_s:
            ready.append(newest)
    except FileNotFoundError:
        pass
    return ready


def index_segments(cfg, repo: RecordingRepo, recording_now: dict[str, bool], now_ts: float | None = None,
                   min_bytes: int = MIN_SEGMENT_BYTES) -> int:
    now_ts = time.time() if now_ts is None else now_ts
    added = 0
    for cam in cfg.cameras:
        if not cam.record:
            continue
        for path in find_ready_segments(cfg.recordings_dir / cam.id, recording_now.get(cam.id, False), now_ts):
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size < min_bytes:
                log.info("Пустой кусок записи удалён: %s (%s байт)", path, size)
                path.unlink(missing_ok=True)
                continue
            if repo.add(cam.id, parse_segment_name(path.name), path.name, size):
                added += 1
    if added:
        log.info("В архив добавлено кусков: %s", added)
    return added
