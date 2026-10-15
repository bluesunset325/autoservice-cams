import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.plate_fmt import normalize_manual
from app.users import User, can_delete_visit, can_edit_plate
from app.visits import group_by_plate, sort_groups
from app.web.auth import current_user, render, require

router = APIRouter()
SORTS = {"entry": "Заезд", "exit": "Выезд", "duration": "Длительность"}
SCREENSHOT_RE = re.compile(r"^screenshot_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?\.jpg$")


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("/journal")
def journal(request: Request, day: str | None = None, sort: str = "entry", desc: int = 0,
            user: User = Depends(current_user)):
    state = request.app.state
    now = state.clock()
    d = parse_date(day) or now.date()
    if sort not in SORTS:
        sort = "entry"
    visits_list = state.visits.for_day(d)
    # for_day включает все незакрытые заезды, начатые раньше; в будущих днях их показывать не нужно
    if d > now.date():
        visits_list = [v for v in visits_list if v.exited_at is not None]
    groups = sort_groups(group_by_plate(visits_list, d, now), sort, bool(desc))
    return render(
        request, "journal.html", user, nav="journal",
        day=d, prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1), today=now.date(),
        groups=groups, sort=sort, desc=bool(desc), sorts=SORTS, now=now,
        cameras={c.id: c.name for c in state.cfg.cameras},
    )


def _visit_or_404(request: Request, visit_id: int):
    visit = request.app.state.visits.get(visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Заезд не найден")
    return visit


def _back_to_day(visit) -> RedirectResponse:
    return RedirectResponse(f"/journal?day={visit.entered_at.date().isoformat()}", status_code=303)


@router.post("/visits/{visit_id}/plate")
def edit_plate(request: Request, visit_id: int, plate: str = Form(...),
               user: User = Depends(require(can_edit_plate))):
    visit = _visit_or_404(request, visit_id)
    new_plate = normalize_manual(plate)
    if new_plate is None:
        raise HTTPException(status_code=400, detail="Пустой или слишком длинный номер")
    request.app.state.visits.edit_plate(visit_id, new_plate, user.login, request.app.state.clock())
    return _back_to_day(visit)


@router.post("/visits/{visit_id}/delete")
def delete_visit(request: Request, visit_id: int, user: User = Depends(require(can_delete_visit))):
    visit = _visit_or_404(request, visit_id)
    request.app.state.visits.delete(visit_id)
    return _back_to_day(visit)


@router.get("/screenshots/{name}")
def screenshot(request: Request, name: str, user: User = Depends(current_user)):
    path = request.app.state.cfg.screenshots_dir / name
    if not SCREENSHOT_RE.match(name) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")
