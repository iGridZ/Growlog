import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="growlog-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "growlog.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("APP_PASSWORD", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import app as growlog  # noqa: E402

TABLES = ("weigh", "water", "note", "plants", "settings", "invites", "users", "config")


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    """Jeder Test startet mit leerer Datenbank und ohne Rate-Limit-Zustand."""
    monkeypatch.setattr(growlog.time, "sleep", lambda _s: None)
    monkeypatch.setattr(growlog, "DB_PATH", os.environ["DB_PATH"])
    growlog.init_db()
    conn = growlog.sqlite3.connect(growlog.DB_PATH)
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    growlog._attempts.clear()
    yield


@pytest.fixture
def make_client():
    def factory():
        return growlog.app.test_client()
    return factory


def _create(username, role="user", password="geheim123"):
    conn = growlog.sqlite3.connect(growlog.DB_PATH)
    uid = growlog.create_user(conn, username, password, role)
    conn.commit()
    conn.close()
    return uid


@pytest.fixture
def login(make_client):
    """login('name', role='user') -> (client, user_id)"""
    def do(username, role="user", password="geheim123"):
        uid = _create(username, role, password)
        c = make_client()
        r = c.post("/login", data={"username": username, "password": password})
        assert r.status_code == 302
        return c, uid
    return do


@pytest.fixture
def admin(login):
    return login("admin", "admin")[0]
