import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.web.auth import render

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html", None, error=None)


@router.post("/login")
async def login(request: Request, login: str = Form(...), password: str = Form(...)):
    state = request.app.state
    user = await run_in_threadpool(state.users.authenticate, login.strip(), password)
    if user is None:
        await asyncio.sleep(state.login_fail_delay)  # притормозить перебор паролей
        return render(request, "login.html", None, status_code=401, error="Неверный логин или пароль")
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
