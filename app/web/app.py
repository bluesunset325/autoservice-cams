"""Сборка веб-приложения АП."""
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.archive import RecordingRepo
from app.capture import probe_rtsp
from app.config import AppConfig
from app.db import Database
from app.uploader import Rclone
from app.users import UserRepo
from app.visits import VisitRepo
from app.web import (
    fmt, pages_archive, pages_auth, pages_cameras, pages_journal, pages_search, pages_settings, pages_users, proxy,
)
from app.web.auth import NotAuthenticated

WEB_DIR = Path(__file__).parent
SESSION_MAX_AGE = 30 * 24 * 3600
ROUTERS = (pages_auth, pages_cameras, pages_journal, pages_search, pages_users, pages_settings, pages_archive, proxy)


def _exit_for_restart() -> None:
    os._exit(3)  # служба (NSSM) перезапустит программу


def create_app(cfg: AppConfig, config_path, db: Database, engine=None, clock=datetime.now,
               login_fail_delay: float = 1.0, probe=None, restart=None,
               archive=None, rclone=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    state = app.state
    state.cfg, state.config_path = cfg, Path(config_path)
    state.users, state.visits = UserRepo(db), VisitRepo(db)
    state.engine, state.clock, state.login_fail_delay = engine, clock, login_fail_delay
    state.probe = probe or probe_rtsp
    state.restart = restart or _exit_for_restart
    state.recordings = RecordingRepo(db)
    state.archive = archive
    state.rclone = rclone or Rclone(cfg)

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters.update(duration=fmt.fmt_duration, hm=fmt.fmt_time, dmy=fmt.fmt_date)
    state.templates = templates

    app.add_middleware(SessionMiddleware, secret_key=cfg.secret_key,
                       max_age=SESSION_MAX_AGE, same_site="lax")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, exc: NotAuthenticated):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Нужно войти"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    for module in ROUTERS:
        app.include_router(module.router)
    return app
