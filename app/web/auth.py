"""Вход и проверка прав для страниц АП."""
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from app.users import User, perms_for


class NotAuthenticated(Exception):
    pass


def user_from_session(session, users) -> User | None:
    """Пользователь из сессии; None, если сессия отозвана (смена пароля) или пользователь удалён."""
    uid = session.get("uid")
    user = users.get(uid) if uid else None
    if user is None or session.get("sv") != user.session_version:
        return None
    return user


def current_user(request: Request) -> User:
    user = user_from_session(request.session, request.app.state.users)
    if user is None:
        raise NotAuthenticated()
    return user


def require(check: Callable[[User], bool]):
    def dependency(user: User = Depends(current_user)) -> User:
        if not check(user):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return dependency


def render(request: Request, name: str, user: User | None, status_code: int = 200, **ctx):
    context = {"user": user, "perms": perms_for(user) if user else {}, **ctx}
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status_code)
