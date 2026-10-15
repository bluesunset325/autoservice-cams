from fastapi import APIRouter, Depends, Request

from app.users import User, can_view_archive
from app.web.auth import current_user, render
from app.web.fmt import fmt_duration, fmt_time

router = APIRouter()


@router.get("/")
def cameras(request: Request, user: User = Depends(current_user)):
    return render(request, "cameras.html", user, nav="cameras", cameras=request.app.state.cfg.cameras)


@router.get("/api/status")
def status(request: Request, user: User = Depends(current_user)):
    state = request.app.state
    now = state.clock()
    statuses = state.engine.statuses() if state.engine else {}
    cameras, in_box = {}, []
    for cam in state.cfg.cameras:
        st = statuses.get(cam.id)
        cameras[cam.id] = {
            "online": st.online if st else None,
            "offline_since": fmt_time(st.offline_since) if st and st.offline_since else None,
        }
        if cam.recognize:
            for v in state.visits.open_visits(cam.id):
                in_box.append({
                    "plate": v.plate, "camera_id": cam.id,
                    "entered_at": fmt_time(v.entered_at), "duration": fmt_duration(v.duration(now)),
                })
    archive = state.archive.status() if state.archive and can_view_archive(user) else None
    return {"cameras": cameras, "in_box": in_box, "archive": archive}
