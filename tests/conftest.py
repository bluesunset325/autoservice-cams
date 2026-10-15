from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.archive import RecordingRepo
from app.config import AppConfig, CameraConfig, save_config
from app.db import Database
from app.users import UserRepo
from app.visits import VisitRepo
from app.web.app import create_app

NOW = datetime(2026, 10, 2, 17, 0)
PASSWORD = "secret1"


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    import bcrypt
    real_gensalt = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=4, prefix=b"2b": real_gensalt(4, prefix))


@pytest.fixture
def env(tmp_path):
    cfg = AppConfig(
        cameras=[CameraConfig("box", "Бокс", "rtsp://u:p@1.2.3.4/s", True),
                 CameraConfig("office", "Офис", "rtsp://1.2.3.5/s")],
        secret_key="test" * 8,
        base_dir=tmp_path,
    )
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg, cfg_path)
    cfg.screenshots_dir.mkdir()
    db = Database(":memory:")
    users = UserRepo(db)
    for login in ("admin", "owner", "viewer"):
        users.create(login, PASSWORD, login)
    app = create_app(cfg, cfg_path, db, engine=None, clock=lambda: NOW, login_fail_delay=0,
                     probe=lambda url: (True, "ok"), restart=lambda: None)
    return SimpleNamespace(app=app, cfg=cfg, cfg_path=cfg_path, users=users, visits=VisitRepo(db),
                           recordings=RecordingRepo(db))


@pytest.fixture
def login(env):
    def _login(user: str) -> TestClient:
        client = TestClient(env.app)
        r = client.post("/login", data={"login": user, "password": PASSWORD}, follow_redirects=False)
        assert r.status_code == 303, r.text
        return client
    return _login


def _dt(h, m=0, d=2):
    return datetime(2026, 10, d, h, m)


@pytest.fixture
def seeded(env):
    """А123ВС77 — два заезда (10:00–11:00, 15:00–16:30); В456ОР99 — в боксе с 12:00;
    Е789КХ50 — с 23:00 прошлого дня до 01:00."""
    v = env.visits
    a1 = v.create("А123ВС77", "A123BC77", "box", _dt(10), "screenshot_2026-10-02_10-00-00.jpg")
    v.close(a1, _dt(11))
    a2 = v.create("А123ВС77", "A123BC77", "box", _dt(15), "screenshot_2026-10-02_15-00-00.jpg")
    v.close(a2, _dt(16, 30))
    b = v.create("В456ОР99", "B456OP99", "box", _dt(12), "screenshot_2026-10-02_12-00-00.jpg")
    c = v.create("Е789КХ50", "E789KX50", "box", _dt(23, 0, 1), "screenshot_2026-10-01_23-00-00.jpg")
    v.close(c, _dt(1))
    return SimpleNamespace(a1=a1, a2=a2, b=b, c=c)
