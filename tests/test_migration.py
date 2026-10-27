import sqlite3

import app as growlog

V1_SCHEMA = """
CREATE TABLE plants (key TEXT PRIMARY KEY, name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE weigh (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, plant TEXT NOT NULL, grams INTEGER NOT NULL);
CREATE TABLE water (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, plant TEXT NOT NULL, ml INTEGER NOT NULL,
  ph REAL, alfa REAL, roots REAL, extra TEXT);
CREATE TABLE note (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, plant TEXT, text TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def make_v1(path):
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO plants(key, name) VALUES ('BM', 'Blueberry Muffin')")
    conn.execute("INSERT INTO weigh(ts, plant, grams) VALUES ('2026-10-25T05:30:00Z', 'BM', 3100)")
    conn.execute("INSERT INTO water(ts, plant, ml) VALUES ('2026-10-25T15:30:00Z', 'BM', 250)")
    conn.execute("INSERT INTO note(ts, plant, text) VALUES ('2026-10-25T15:40:00Z', 'BM', 'alt')")
    conn.execute("INSERT INTO settings(key, value) VALUES ('water_time', '18:00')")
    conn.commit()
    conn.close()


def test_v1_database_migrates_and_is_adopted_by_first_admin(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    make_v1(path)
    monkeypatch.setattr(growlog, "DB_PATH", path)
    growlog.init_db()
    growlog.init_db()  # idempotent
    conn = sqlite3.connect(path)
    for t in ("plants", "weigh", "water", "note", "settings"):
        assert "user_id" in growlog.table_cols(conn, t)
    conn.close()

    c = growlog.app.test_client()
    r = c.post("/setup", data={"username": "erik", "password": "geheim123", "password2": "geheim123"})
    assert r.status_code == 302
    s = c.get("/api/state").get_json()
    assert [p["key"] for p in s["plants"]] == ["BM"]
    assert s["weigh"][0]["values"] == {"BM": 3100}
    assert s["water"][0]["ml"] == {"BM": 250}
    assert s["notes"][0]["text"] == "alt"
    assert s["settings"]["water_time"] == "18:00"

    # ein später angelegter Nutzer sieht die Altdaten nicht
    token = c.post("/api/invites").get_json()["token"]
    other = growlog.app.test_client()
    other.post("/register", data={"token": token, "username": "anna", "password": "geheim123",
                                  "password2": "geheim123"})
    assert other.get("/api/state").get_json()["plants"] == []


def test_orphans_adopted_via_env_admin(tmp_path, monkeypatch):
    path = str(tmp_path / "old2.db")
    make_v1(path)
    monkeypatch.setattr(growlog, "DB_PATH", path)
    monkeypatch.setattr(growlog, "PASSWORD", "geheim123")
    monkeypatch.setattr(growlog, "USER", "boss")
    growlog.init_db()
    conn = sqlite3.connect(path)
    uid = conn.execute("SELECT id FROM users WHERE username='boss'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM weigh WHERE user_id=?", (uid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM weigh WHERE user_id=0").fetchone()[0] == 0
    conn.close()
