from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.archive import Recording, parse_segment_name
from app.users import User, can_view_archive
from app.web.auth import render, require
from app.web.pages_journal import parse_date

router = APIRouter()
archive_only = require(can_view_archive)
STATUS_TEXT = {
    "both": "на компьютере и на Диске",
    "local": "только на компьютере, ждёт загрузки",
    "drive": "только на Диске",
    "none": "записи нет",
    "gone": "записи нет",
}


@dataclass(frozen=True)
class Cell:
    start: datetime
    rec: Recording | None

    @property
    def status(self) -> str:
        if self.rec is None or self.rec.status == "gone":
            return "none"
        return self.rec.status


def day_cells(day: date, rec_cfg, recordings: list[Recording]) -> list[Cell]:
    step = timedelta(minutes=rec_cfg.segment_minutes)
    start = datetime.combine(day, rec_cfg.start_time)
    end = datetime.combine(day, rec_cfg.end_time)
    covered = {int((r.started_at - start) / step) for r in recordings if start <= r.started_at < end}
    cells = [Cell(start + i * step, None) for i in range(-(-(end - start) // step)) if i not in covered]
    cells += [Cell(r.started_at, r) for r in recordings]
    return sorted(cells, key=lambda c: c.start)


LEEWAY = timedelta(seconds=60)


def _containing(recordings: list[Recording], target: datetime, segment_minutes: int) -> Recording | None:
    """Последний кусок, начавшийся не позже target (с запасом 60 с: ffmpeg начинает кусок на
    несколько секунд позже ЧЧ:ММ:00), если target раньше начала следующего куска и не дальше
    конца этого куска + 60 с (иначе — дыра в записи)."""
    alive = sorted((r for r in recordings if r.status != "gone"), key=lambda r: r.started_at)
    idx = next((i for i in range(len(alive) - 1, -1, -1) if alive[i].started_at - LEEWAY <= target), None)
    if idx is None:
        return None
    r = alive[idx]
    end = r.started_at + timedelta(minutes=segment_minutes) + LEEWAY
    if idx + 1 < len(alive):
        end = min(end, alive[idx + 1].started_at)
    return r if target < end else None


def _pick(recordings: list[Recording], day: date, segment_minutes: int, at: str | None,
          rec_id: int | None) -> tuple[Recording | None, Recording | None, str | None]:
    """-> (выбранная запись на диске, запись только на Диске, время без записи)."""
    if rec_id is not None:
        return next((r for r in recordings if r.id == rec_id), None), None, None
    if not at:
        return None, None, None
    try:
        parts = [int(x) for x in at.split(":")]
        if len(parts) not in (2, 3):
            raise ValueError(at)
        target = datetime.combine(day, time(*parts))
    except ValueError:
        return None, None, None
    found = _containing(recordings, target, segment_minutes)
    if found is None:
        return None, None, at
    if found.on_disk:
        return found, None, None
    return None, found, None


@router.get("/archive")
def archive_page(request: Request, camera: str | None = None, day: str | None = None, at: str | None = None,
                 rec: int | None = None, user: User = Depends(archive_only)):
    state = request.app.state
    cams = [c for c in state.cfg.cameras if c.record]
    if not cams:
        return render(request, "archive.html", user, nav="archive", cams=[], cam=None)
    cam = next((c for c in cams if c.id == camera), cams[0])
    now = state.clock()
    d = parse_date(day) or now.date()
    recordings = state.recordings.for_day(cam.id, d)
    selected, drive_only, missing_at = _pick(recordings, d, state.cfg.recording.segment_minutes, at, rec)
    playable = [r for r in recordings if r.on_disk]
    idx = next((i for i, r in enumerate(playable) if selected and r.id == selected.id), None)
    return render(
        request, "archive.html", user, nav="archive", cams=cams, cam=cam, day=d,
        prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1),
        cells=day_cells(d, state.cfg.recording, recordings), status_text=STATUS_TEXT,
        selected=selected if selected and selected.on_disk else None,
        drive_only=drive_only, missing_at=missing_at, at=at,
        prev_rec=playable[idx - 1] if idx else None,
        next_rec=playable[idx + 1] if idx is not None and idx + 1 < len(playable) else None,
        archive_status=state.archive.status() if state.archive else None,
    )


@router.get("/archive/video/{rec_id}")
def video(request: Request, rec_id: int, download: int = 0, user: User = Depends(archive_only)):
    state = request.app.state
    r = state.recordings.get(rec_id)
    if r is None or not r.on_disk:
        raise HTTPException(status_code=404)
    path = state.cfg.recordings_dir / r.camera / r.file
    if (parse_segment_name(r.file) is None
            or not path.resolve().is_relative_to(state.cfg.recordings_dir.resolve())
            or not path.is_file()):
        raise HTTPException(status_code=404)
    if download:
        return FileResponse(path, media_type="video/mp4", filename=f"{r.camera}_{r.file}")
    return FileResponse(path, media_type="video/mp4")
