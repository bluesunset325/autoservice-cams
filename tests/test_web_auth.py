from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.capture import CameraStatus


def test_cameras_page_requires_login(env):
    r = TestClient(env.app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_api_requires_login(env):
    assert TestClient(env.app).get("/api/status").status_code == 401


def test_wrong_password(env):
    r = TestClient(env.app).post("/login", data={"login": "admin", "password": "nope!!"})
    assert r.status_code == 401
    assert "Неверный логин или пароль" in r.text


def test_cameras_page_shows_live_players(login):
    html = login("viewer").get("/").text
    assert "Бокс" in html and "Офис" in html
    assert "/go2rtc/stream.html?src=box" in html
    assert "Сейчас в боксе" in html


@pytest.mark.parametrize("role, users_link, settings_link", [
    ("viewer", False, False), ("owner", True, False), ("admin", True, True),
])
def test_nav_depends_on_role(login, role, users_link, settings_link):
    html = login(role).get("/").text
    assert ('href="/users"' in html) is users_link
    assert ('href="/settings"' in html) is settings_link


def test_logout(login):
    client = login("admin")
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_status_lists_cars_in_box(env, login):
    env.visits.create("А123ВС77", "A123BC77", "box", datetime(2026, 10, 2, 16, 0), "s.jpg")
    data = login("viewer").get("/api/status").json()
    assert data["cameras"]["box"] == {"online": None, "offline_since": None}
    assert data["in_box"] == [
        {"plate": "А123ВС77", "camera_id": "box", "entered_at": "16:00", "duration": "1 ч 0 мин"}
    ]


def test_status_uses_engine(env, login):
    env.app.state.engine = SimpleNamespace(statuses=lambda: {
        "box": CameraStatus(False, datetime(2026, 10, 2, 14, 32)),
        "office": CameraStatus(True, None),
    })
    data = login("viewer").get("/api/status").json()
    assert data["cameras"]["box"] == {"online": False, "offline_since": "14:32"}
    assert data["cameras"]["office"] == {"online": True, "offline_since": None}


def test_go2rtc_proxy_requires_login_and_whitelist(env, login):
    r = TestClient(env.app).get("/go2rtc/stream.html", follow_redirects=False)
    assert r.status_code == 303
    assert login("viewer").get("/go2rtc/api/config").status_code == 404


def test_ws_proxy_rejects_anonymous(env):
    with pytest.raises(WebSocketDisconnect):
        with TestClient(env.app).websocket_connect("/go2rtc/api/ws?src=box"):
            pass


@pytest.mark.parametrize("src", ["rtsp://evil", "nope"])
def test_ws_proxy_rejects_unknown_src(login, src):
    with pytest.raises(WebSocketDisconnect):
        with login("viewer").websocket_connect(f"/go2rtc/api/ws?src={src}"):
            pass


def test_deleted_user_session_not_reused_by_new_user(env, login):
    old = login("viewer")
    viewer = env.users.get_by_login("viewer")
    env.users.delete(viewer.id)
    env.users.create("newbie", "secret1", "admin")  # id не должен совпасть
    r = old.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_password_change_logs_out_existing_sessions(env, login):
    other = login("viewer")
    admin = login("admin")
    assert other.get("/", follow_redirects=False).status_code == 200
    viewer = env.users.get_by_login("viewer")
    assert admin.post(f"/users/{viewer.id}/password", data={"password": "newpass1"},
                      follow_redirects=False).status_code == 303
    assert other.get("/", follow_redirects=False).status_code == 303
    assert admin.get("/", follow_redirects=False).status_code == 200  # чужой пароль — админ остаётся


def test_changing_own_password_logs_out_self(env, login):
    admin = login("admin")
    me = env.users.get_by_login("admin")
    assert admin.post(f"/users/{me.id}/password", data={"password": "newpass1"},
                      follow_redirects=False).status_code == 303
    r = admin.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_ws_proxy_rejects_stale_session(env, login):
    client = login("viewer")
    env.users.set_password(env.users.get_by_login("viewer").id, "newpass1")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/go2rtc/api/ws?src=box"):
            pass
