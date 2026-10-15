from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.users import ROLE_NAMES, ROLES, User, UserError, can_manage, can_manage_users
from app.web.auth import render, require

router = APIRouter()


def _page(request: Request, user: User, error: str | None = None, status_code: int = 200):
    return render(
        request, "users.html", user, status_code=status_code, nav="users",
        users=request.app.state.users.list(), roles=[r for r in ROLES if can_manage(user, r)],
        role_names=ROLE_NAMES, can_manage=can_manage, error=error,
    )


def _target(request: Request, user_id: int, actor: User, allow_self: bool = False) -> User:
    target = request.app.state.users.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not (can_manage(actor, target.role) or (allow_self and target.id == actor.id)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return target


@router.get("/users")
def users_page(request: Request, user: User = Depends(require(can_manage_users))):
    return _page(request, user)


@router.post("/users")
def create_user(request: Request, login: str = Form(...), password: str = Form(...),
                role: str = Form(...), user: User = Depends(require(can_manage_users))):
    if role not in ROLES or not can_manage(user, role):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    try:
        request.app.state.users.create(login.strip(), password, role)
    except UserError as e:
        return _page(request, user, error=str(e), status_code=400)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int, user: User = Depends(require(can_manage_users))):
    target = _target(request, user_id, user)
    if target.id == user.id:
        return _page(request, user, error="Нельзя удалить самого себя", status_code=400)
    request.app.state.users.delete(target.id)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/password")
def change_password(request: Request, user_id: int, password: str = Form(...),
                    user: User = Depends(require(can_manage_users))):
    target = _target(request, user_id, user, allow_self=True)
    try:
        request.app.state.users.set_password(target.id, password)
    except UserError as e:
        return _page(request, user, error=str(e), status_code=400)
    return RedirectResponse("/users", status_code=303)
