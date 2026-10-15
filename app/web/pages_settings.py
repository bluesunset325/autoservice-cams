import copy
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import WEEKDAYS, AppConfig, save_config, validate_limits, validate_recording
from app.go2rtc import go2rtc_rtsp_url
from app.uploader import RcloneError
from app.users import User, can_change_settings
from app.web.auth import render, require

router = APIRouter()
admin_only = require(can_change_settings)


def _apply_form(cfg: AppConfig, form) -> AppConfig:
    new = copy.deepcopy(cfg)
    try:
        for cam in new.cameras:
            cam.name = (form.get(f"cam_{cam.id}_name") or cam.name).strip()
            cam.source = (form.get(f"cam_{cam.id}_source") or "").strip()
            cam.recognize = form.get(f"cam_{cam.id}_recognize") == "on"
            if not cam.source:
                raise ValueError
        new.tracker.confirm_reads = int(form["confirm_reads"])
        new.tracker.confirm_window_s = float(form["confirm_window_s"])
        new.tracker.exit_after_s = float(form["exit_after_s"])
        new.analyze_fps = float(form["analyze_fps"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Неверные значения настроек") from None
    try:
        validate_limits(new)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверные значения настроек") from None
    return new


@router.get("/settings")
def settings_page(request: Request, saved: int = 0, user: User = Depends(admin_only)):
    cfg = request.app.state.cfg
    free_gb = shutil.disk_usage(cfg.data_path).free / 1024 ** 3
    return render(request, "settings.html", user, nav="settings", cfg=cfg,
                  free_gb=round(free_gb, 1), saved=bool(saved), weekdays=WEEKDAYS,
                  weekday_names={"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
                                 "fri": "Пт", "sat": "Сб", "sun": "Вс"})


@router.post("/settings")
async def save_settings(request: Request, user: User = Depends(admin_only)):
    state = request.app.state
    new = _apply_form(state.cfg, await request.form())
    save_config(new, state.config_path)
    state.cfg = new
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/check/{camera_id}")
def check_camera(request: Request, camera_id: str, user: User = Depends(admin_only)):
    cam = request.app.state.cfg.camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Нет такой камеры")
    ok, message = request.app.state.probe(go2rtc_rtsp_url(cam.id))  # через go2rtc: работает и для Xiaomi
    return {"ok": ok, "message": message}


@router.post("/settings/restart")
def restart(request: Request, background: BackgroundTasks, user: User = Depends(admin_only)):
    background.add_task(request.app.state.restart)
    return {"ok": True}


@router.post("/settings/recording")
async def save_recording(request: Request, user: User = Depends(admin_only)):
    state = request.app.state
    form = await request.form()
    new = copy.deepcopy(state.cfg)
    rec = new.recording
    try:
        rec.start = str(form["rec_start"]).strip()
        rec.end = str(form["rec_end"]).strip()
        rec.days = [d for d in WEEKDAYS if form.get(f"rec_day_{d}") == "on"]
        rec.segment_minutes = int(form["segment_minutes"])
        rec.upload_bwlimit_mbit = float(form["upload_bwlimit_mbit"])
        rec.drive_retention_days = int(form["drive_retention_days"])
        rec.local_cleanup_weekday = str(form["local_cleanup_weekday"])
        rec.low_space_gb = float(form["low_space_gb"])
        for cam in new.cameras:
            cam.record = form.get(f"cam_{cam.id}_record") == "on"
            cam.record_source = (form.get(f"cam_{cam.id}_record_source") or "").strip() or None
        validate_recording(rec)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Неверные настройки записи: {e}") from None
    save_config(new, state.config_path)
    state.cfg = new
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/check-drive")
def check_drive(request: Request, user: User = Depends(admin_only)):
    try:
        request.app.state.rclone.about()
    except RcloneError as e:
        return {"ok": False, "message": f"Google Диск не подключён: {e}"}
    return {"ok": True, "message": "Google Диск подключён"}
