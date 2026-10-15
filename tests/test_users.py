import pytest

from app.config import load_config
from app.db import Database
from app.manage import main as manage_main
from app.users import (
    User, UserError, UserRepo, can_delete_visit, can_edit_plate, can_manage,
    can_manage_users, can_change_settings, can_view_archive, perms_for,
)


@pytest.fixture
def users():
    return UserRepo(Database(":memory:"))


def test_create_and_authenticate(users):
    u = users.create("admin", "secret1", "admin")
    assert u == User(u.id, "admin", "admin")
    assert users.authenticate("admin", "secret1") == u
    assert users.authenticate("admin", "wrong!!") is None
    assert users.authenticate("nobody", "secret1") is None


def test_password_not_stored_in_plain_text(users):
    users.create("admin", "secret1", "admin")
    [row] = users._db.query("SELECT password_hash FROM users")
    assert "secret1" not in row["password_hash"]


@pytest.mark.parametrize("login, password, role", [
    ("ab", "secret1", "viewer"),          # логин короче 3
    ("bad login", "secret1", "viewer"),   # пробел
    ("bob", "123", "viewer"),             # короткий пароль
    ("bob", "x" * 65, "viewer"),          # длинный пароль
    ("bob", "secret1", "root"),           # нет такой роли
])
def test_create_validation(users, login, password, role):
    with pytest.raises(UserError):
        users.create(login, password, role)


def test_duplicate_login(users):
    users.create("bob", "secret1", "viewer")
    with pytest.raises(UserError, match="занят"):
        users.create("bob", "secret2", "viewer")


def test_list_get_delete_count(users):
    a = users.create("admin", "secret1", "admin")
    b = users.create("bob", "secret1", "viewer")
    assert users.list() == [a, b]
    assert users.get_by_login("bob") == b
    assert users.count() == 2
    users.delete(b.id)
    assert users.get(b.id) is None
    assert users.count() == 1


def test_set_password(users):
    u = users.create("bob", "secret1", "viewer")
    users.set_password(u.id, "newpass1")
    after = users.authenticate("bob", "newpass1")
    assert after.id == u.id and after.session_version == u.session_version + 1
    assert users.authenticate("bob", "secret1") is None


def test_permission_matrix():
    admin, owner, viewer = User(1, "a", "admin"), User(2, "o", "owner"), User(3, "v", "viewer")
    assert [can_edit_plate(u) for u in (admin, owner, viewer)] == [True, True, False]
    assert [can_delete_visit(u) for u in (admin, owner, viewer)] == [True, False, False]
    assert [can_manage_users(u) for u in (admin, owner, viewer)] == [True, True, False]
    assert [can_change_settings(u) for u in (admin, owner, viewer)] == [True, False, False]
    assert [can_view_archive(u) for u in (admin, owner, viewer)] == [True, True, False]
    assert all(can_manage(admin, r) for r in ("admin", "owner", "viewer"))
    assert [can_manage(owner, r) for r in ("admin", "owner", "viewer")] == [False, False, True]
    assert not any(can_manage(viewer, r) for r in ("admin", "owner", "viewer"))
    assert perms_for(owner) == {"edit_plate": True, "delete_visit": False, "users": True, "settings": False,
                                    "archive": True}


def test_manage_create_user_and_set_password(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("cameras: []\n", encoding="utf-8")
    assert manage_main(["--config", str(cfg_path), "create-user", "admin", "--role", "admin"],
                       ask_password=lambda: "secret1") == 0
    assert manage_main(["--config", str(cfg_path), "set-password", "admin"],
                       ask_password=lambda: "newpass1") == 0
    assert manage_main(["--config", str(cfg_path), "set-password", "ghost"],
                       ask_password=lambda: "newpass1") == 1
    users = UserRepo(Database(load_config(cfg_path).db_path))
    assert users.authenticate("admin", "newpass1").role == "admin"


def test_password_byte_limit_cyrillic(users):
    long_cyrillic = "пароль" * 7  # 42 chars, 84 bytes (exceeds bcrypt's 72-byte limit)
    with pytest.raises(UserError):
        users.create("bob", long_cyrillic, "viewer")


def test_authenticate_rejects_long_cyrillic_password(users):
    users.create("bob", "secret1", "viewer")
    long_cyrillic = "пароль" * 7  # 42 chars, 84 bytes
    assert users.authenticate("bob", long_cyrillic) is None


def test_login_with_newline_rejected(users):
    with pytest.raises(UserError):
        users.create("bob\n", "secret1", "viewer")
