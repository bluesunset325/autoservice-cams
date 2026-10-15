"""Пользователи АП и права ролей."""
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import bcrypt

from app.db import Database, to_ts

ROLES = ("admin", "owner", "viewer")
ROLE_NAMES = {"admin": "Админ", "owner": "Владелец", "viewer": "Просмотр"}
LOGIN_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN, MAX_PASSWORD_LEN = 6, 64


class UserError(ValueError):
    pass


@dataclass(frozen=True)
class User:
    id: int
    login: str
    role: str
    session_version: int = 0


def _user(row) -> User:
    return User(row["id"], row["login"], row["role"], row["session_version"])


def _check_password(password: str) -> None:
    if not MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN:
        raise UserError(f"Пароль должен быть от {MIN_PASSWORD_LEN} до {MAX_PASSWORD_LEN} символов")
    if len(password.encode()) > 72:
        raise UserError("Пароль слишком длинный")


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


class UserRepo:
    def __init__(self, db: Database):
        self._db = db

    def create(self, login: str, password: str, role: str) -> User:
        if not LOGIN_RE.fullmatch(login):
            raise UserError("Логин: 3–32 символа, латиница, цифры, _ . -")
        if role not in ROLES:
            raise UserError("Неизвестная роль")
        _check_password(password)
        try:
            user_id = self._db.insert(
                "INSERT INTO users (login, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (login, _hash(password), role, to_ts(datetime.now())),
            )
        except sqlite3.IntegrityError:
            raise UserError("Логин уже занят") from None
        return User(user_id, login, role)

    def authenticate(self, login: str, password: str) -> User | None:
        rows = self._db.query("SELECT * FROM users WHERE login = ?", (login,))
        if not rows or len(password) > MAX_PASSWORD_LEN or len(password.encode()) > 72:
            return None
        row = rows[0]
        if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return None
        return _user(row)

    def get(self, user_id: int) -> User | None:
        rows = self._db.query("SELECT * FROM users WHERE id = ?", (user_id,))
        return _user(rows[0]) if rows else None

    def get_by_login(self, login: str) -> User | None:
        rows = self._db.query("SELECT * FROM users WHERE login = ?", (login,))
        return _user(rows[0]) if rows else None

    def list(self) -> list[User]:
        return [_user(r) for r in self._db.query("SELECT * FROM users ORDER BY id")]

    def delete(self, user_id: int) -> None:
        self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def set_password(self, user_id: int, password: str) -> None:
        _check_password(password)
        self._db.execute("UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?", (_hash(password), user_id))

    def count(self) -> int:
        return self._db.query("SELECT COUNT(*) AS n FROM users")[0]["n"]


def can_edit_plate(user: User) -> bool:
    return user.role in ("admin", "owner")


def can_delete_visit(user: User) -> bool:
    return user.role == "admin"


def can_manage_users(user: User) -> bool:
    return user.role in ("admin", "owner")


def can_change_settings(user: User) -> bool:
    return user.role == "admin"


def can_view_archive(user: User) -> bool:
    return user.role in ("admin", "owner")


def can_manage(actor: User, target_role: str) -> bool:
    """Может ли actor создавать/удалять/менять пароль пользователям с ролью target_role."""
    return actor.role == "admin" or (actor.role == "owner" and target_role == "viewer")


def perms_for(user: User) -> dict[str, bool]:
    return {
        "edit_plate": can_edit_plate(user),
        "delete_visit": can_delete_visit(user),
        "users": can_manage_users(user),
        "settings": can_change_settings(user),
        "archive": can_view_archive(user),
    }
