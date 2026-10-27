"""GrowLog – kleines Grow-Tagebuch: Wiegen, Gießen (ml), Notizen, Discord-Export."""
import csv
import functools
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import statistics
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, redirect, request, send_file, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from markupsafe import escape
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Berlin"))
DB_PATH = os.environ.get("DB_PATH", "/data/growlog.db")
# Nur für den ersten Start: legen den Admin an, falls es noch keine Konten gibt
PASSWORD = os.environ.get("APP_PASSWORD", "")
USER = os.environ.get("APP_USER", "admin")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # Restore-Upload
# Server-Einstellungen (Zeitzone, Proxy) stehen in der Datenbank und werden im Web-UI geändert.
# Die Umgebungsvariablen TZ_NAME / TRUST_PROXY gelten nur als Startwert.
CFG = {"tz": os.environ.get("TZ_NAME", "Europe/Berlin"),
       "trust_proxy": os.environ.get("TRUST_PROXY", "1") == "1"}


class _Proxy:
    """Übernimmt echte IP/https von einem Reverse-Proxy, falls im UI aktiviert."""
    def __init__(self, wsgi):
        self.plain, self.fixed = wsgi, ProxyFix(wsgi, x_for=1, x_proto=1)

    def __call__(self, environ, start_response):
        return (self.fixed if CFG["trust_proxy"] else self.plain)(environ, start_response)


app.wsgi_app = _Proxy(app.wsgi_app)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1,
  webhook TEXT, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
  token TEXT PRIMARY KEY, created_by INTEGER NOT NULL, created TEXT NOT NULL,
  expires TEXT NOT NULL, used_by INTEGER
);
CREATE TABLE IF NOT EXISTS plants (
  user_id INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL, name TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, target INTEGER, threshold INTEGER,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS weigh (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 0, ts TEXT NOT NULL,
  plant TEXT NOT NULL, grams INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS water (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 0, ts TEXT NOT NULL,
  plant TEXT NOT NULL, ml INTEGER NOT NULL,
  ph REAL, alfa REAL, roots REAL, extra TEXT
);
CREATE TABLE IF NOT EXISTS note (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 0, ts TEXT NOT NULL,
  plant TEXT, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  user_id INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ---------------------------------------------------------------- DB / Zeit
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def table_cols(conn, name):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]


def create_user(conn, username, password, role="user", webhook=None):
    cur = conn.execute(
        "INSERT INTO users(username, pw_hash, role, webhook, created) VALUES (?,?,?,?,?)",
        (username, generate_password_hash(password), role, webhook or None, iso(now_utc())))
    return cur.lastrowid


def adopt_orphans(conn, user_id):
    """Daten aus Version 1/2 (ohne Besitzer) gehören dem ersten Admin."""
    for table in ("plants", "weigh", "water", "note", "settings"):
        conn.execute(f"UPDATE {table} SET user_id=? WHERE user_id=0", (user_id,))


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    if "share" not in table_cols(conn, "users"):
        conn.execute("ALTER TABLE users ADD COLUMN share INTEGER NOT NULL DEFAULT 0")
    pc = table_cols(conn, "plants")
    if "user_id" not in pc:  # Datenbank aus Version 1/2: auf Mehrbenutzer umstellen
        for col, ddl in (("active", "INTEGER NOT NULL DEFAULT 1"), ("target", "INTEGER"), ("threshold", "INTEGER")):
            if col not in pc:
                conn.execute(f"ALTER TABLE plants ADD COLUMN {col} {ddl}")
        conn.executescript("""
            CREATE TABLE plants_new (
              user_id INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL, name TEXT NOT NULL,
              sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, target INTEGER, threshold INTEGER,
              PRIMARY KEY (user_id, key));
            INSERT INTO plants_new(user_id, key, name, sort, active, target, threshold)
              SELECT 0, key, name, sort, active, target, threshold FROM plants;
            DROP TABLE plants;
            ALTER TABLE plants_new RENAME TO plants;""")
    if "user_id" not in table_cols(conn, "settings"):
        conn.executescript("""
            CREATE TABLE settings_new (user_id INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL,
              value TEXT NOT NULL, PRIMARY KEY (user_id, key));
            INSERT INTO settings_new(user_id, key, value) SELECT 0, key, value FROM settings;
            DROP TABLE settings;
            ALTER TABLE settings_new RENAME TO settings;""")
    for table in ("weigh", "water", "note"):
        if "user_id" not in table_cols(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS i_weigh_user ON weigh(user_id, ts);
        CREATE INDEX IF NOT EXISTS i_water_user ON water(user_id, ts);
        CREATE INDEX IF NOT EXISTS i_note_user ON note(user_id, ts);""")
    conn.commit()
    if PASSWORD and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        adopt_orphans(conn, create_user(conn, USER, PASSWORD, "admin", WEBHOOK))
        conn.commit()
    load_config(conn)
    conn.close()


def load_config(conn):
    """Gespeicherte Server-Einstellungen übernehmen (überschreiben die Startwerte aus den Umgebungsvariablen)."""
    global TZ
    for k, v in conn.execute("SELECT key, value FROM config"):
        if k == "tz":
            try:
                TZ = ZoneInfo(v)
                CFG["tz"] = v
            except Exception:
                pass
        elif k == "trust_proxy":
            CFG["trust_proxy"] = v == "1"


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_ts(value):
    """ISO-String -> aware UTC datetime. Ohne Zone gilt lokale Zeit."""
    if not value:
        return now_utc()
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def loc(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)


def day_label(d):
    return f"{WD[d.weekday()]} {d.day:02d}.{d.month:02d}"


# ---------------------------------------------------------------- Auth
COOKIE = "growlog_session"
SESSION_DAYS = 90
PUBLIC_PATHS = ("/login", "/register", "/setup", "/manifest.webmanifest", "/sw.js",
                "/static/style.css", "/static/icons/")
INVITE_DAYS = 7
_attempts = {}
_serializer = None
DUMMY_HASH = generate_password_hash("kein-benutzer")


def _secret():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(os.path.dirname(DB_PATH) or ".", "secret.key")
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        key = secrets.token_hex(32)
        with open(path, "w") as f:
            f.write(key)
        return key


def ser():
    global _serializer
    _serializer = _serializer or URLSafeTimedSerializer(_secret(), salt="growlog-session-v2")
    return _serializer


def user_from_cookie():
    try:
        data = ser().loads(request.cookies.get(COOKIE, ""), max_age=SESSION_DAYS * 86400)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    row = db().execute("SELECT * FROM users WHERE id=? AND active=1", (data.get("uid"),)).fetchone()
    # Passwortwechsel macht alte Sessions ungültig
    if row and hmac.compare_digest(str(data.get("v")), row["pw_hash"][-12:]):
        return row
    return None


def with_session(resp, user):
    resp.set_cookie(COOKIE, ser().dumps({"uid": user["id"], "v": user["pw_hash"][-12:]}),
                    max_age=SESSION_DAYS * 86400, httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


def no_users():
    return db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def too_many(key, limit, window):
    now_t = time.time()
    recent = [t for t in _attempts.get(key, []) if now_t - t < window]
    _attempts[key] = recent
    return len(recent) >= limit


def note_fail(*keys):
    for k in keys:
        _attempts.setdefault(k, []).append(time.time())


def valid_username(name):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", name or ""))


def password_problem(pw, username=""):
    if len(pw or "") < 8:
        return "Das Passwort braucht mindestens 8 Zeichen."
    if len(pw) > 200:
        return "Das Passwort ist zu lang."
    if username and pw.lower() == username.lower():
        return "Das Passwort darf nicht der Benutzername sein."
    return None


WEBHOOK_RE = re.compile(r"https://(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+")


@app.before_request
def require_auth():
    if request.path.startswith(PUBLIC_PATHS):
        return None
    g.user = user_from_cookie()
    if g.user:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Nicht angemeldet"), 401
    return redirect("/setup" if no_users() else "/login")


def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            return jsonify(error="Nur für Admins"), 403
        return fn(*args, **kwargs)
    return wrapper


def render_page(name, error="", **extra):
    with open(os.path.join(app.static_folder, name), encoding="utf-8") as f:
        html = f.read().replace("<!--ERR-->", f'<p class="err">{escape(error)}</p>' if error else "")
    for key, value in extra.items():
        html = html.replace(f"<!--{key}-->", str(escape(value)))
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_form():
    if no_users():
        return redirect("/setup")
    if user_from_cookie():
        return redirect("/")
    return render_page("login.html")


@app.post("/login")
def login_submit():
    if no_users():
        return redirect("/setup")
    ip = request.remote_addr or "?"
    username = request.form.get("username", "").strip()
    if too_many("ip:" + ip, 5, 300) or too_many("user:" + username.lower(), 20, 900):
        return render_page("login.html", "Zu viele Versuche. Bitte in ein paar Minuten erneut versuchen."), 429
    row = db().execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    password = request.form.get("password", "")
    ok = check_password_hash(row["pw_hash"] if row else DUMMY_HASH, password) and row is not None
    if not ok:
        note_fail("ip:" + ip, "user:" + username.lower())
        time.sleep(0.5)
        return render_page("login.html", "Benutzername oder Passwort falsch."), 401
    _attempts.pop("ip:" + ip, None)
    return with_session(redirect("/"), row)


@app.get("/setup")
def setup_form():
    if not no_users():
        return redirect("/login")
    return render_page("setup.html")


@app.post("/setup")
def setup_submit():
    if not no_users():
        return redirect("/login")
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not valid_username(username):
        return render_page("setup.html", "Benutzername: 3-32 Zeichen (Buchstaben, Zahlen, _ . -)."), 400
    problem = password_problem(password, username)
    if problem or password != request.form.get("password2", ""):
        return render_page("setup.html", problem or "Die Passwörter stimmen nicht überein."), 400
    conn = db()
    uid_ = create_user(conn, username, password, "admin", WEBHOOK)
    adopt_orphans(conn, uid_)
    conn.commit()
    return with_session(redirect("/"), conn.execute("SELECT * FROM users WHERE id=?", (uid_,)).fetchone())


def open_invite(token):
    return db().execute(
        "SELECT * FROM invites WHERE token=? AND used_by IS NULL AND expires > ?",
        (token or "", iso(now_utc()))).fetchone()


@app.get("/register")
def register_form():
    token = request.args.get("token", "")
    if not open_invite(token):
        return render_page("register.html", "Diese Einladung ist ungültig oder abgelaufen.", TOKEN="", DISABLED="disabled")
    return render_page("register.html", TOKEN=token, DISABLED="")


@app.post("/register")
def register_submit():
    token = request.form.get("token", "")
    inv = open_invite(token)
    if not inv:
        return render_page("register.html", "Diese Einladung ist ungültig oder abgelaufen.", TOKEN="", DISABLED="disabled"), 400
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    problem = None
    if not valid_username(username):
        problem = "Benutzername: 3-32 Zeichen (Buchstaben, Zahlen, _ . -)."
    elif db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        problem = "Diesen Benutzernamen gibt es schon."
    else:
        problem = password_problem(password, username)
        if not problem and password != request.form.get("password2", ""):
            problem = "Die Passwörter stimmen nicht überein."
    if problem:
        return render_page("register.html", problem, TOKEN=token, DISABLED=""), 400
    conn = db()
    uid_ = create_user(conn, username, password, "user")
    conn.execute("UPDATE invites SET used_by=? WHERE token=?", (uid_, token))
    conn.commit()
    return with_session(redirect("/"), conn.execute("SELECT * FROM users WHERE id=?", (uid_,)).fetchone())


@app.post("/logout")
def logout():
    resp = jsonify(ok=True)
    resp.delete_cookie(COOKIE)
    return resp


# ---------------------------------------------------------------- Daten
def uid():
    return g.user["id"]


def plants(u=None):
    rows = db().execute(
        "SELECT key, name, active, target, threshold FROM plants WHERE user_id=? ORDER BY sort, key",
        (u or uid(),)).fetchall()
    return [{"key": r["key"], "name": r["name"], "active": bool(r["active"]),
             "target": r["target"], "threshold": r["threshold"]} for r in rows]


def weigh_sessions(u=None):
    """Gruppiert nach Zeitstempel; neueste zuletzt."""
    rows = db().execute("SELECT id, ts, plant, grams FROM weigh WHERE user_id=? ORDER BY ts, id", (u or uid(),)).fetchall()
    sessions = {}
    for r in rows:
        s = sessions.setdefault(r["ts"], {"ts": r["ts"], "values": {}, "ids": []})
        s["values"][r["plant"]] = r["grams"]
        s["ids"].append(r["id"])
    return list(sessions.values())


def water_sessions(u=None):
    rows = db().execute(
        "SELECT id, ts, plant, ml, ph, alfa, roots, extra FROM water WHERE user_id=? ORDER BY ts, id",
        (u or uid(),)).fetchall()
    sessions = {}
    for r in rows:
        s = sessions.setdefault(
            r["ts"],
            {"ts": r["ts"], "ml": {}, "ids": [], "ph": r["ph"], "alfa": r["alfa"],
             "roots": r["roots"], "extra": r["extra"]},
        )
        s["ml"][r["plant"]] = r["ml"]
        s["ids"].append(r["id"])
    return list(sessions.values())


def with_deltas(sessions):
    last = {}
    for s in sessions:
        s["delta"] = {}
        for k, v in s["values"].items():
            if k in last:
                s["delta"][k] = v - last[k][0]
                s["hours"] = round(
                    (parse_ts(s["ts"]) - parse_ts(last[k][1])).total_seconds() / 3600, 1
                )
            last[k] = (v, s["ts"])
    return sessions


# ---------------------------------------------------------------- Discord-Text
def fmt_ml(d, keys):
    return " | ".join(f"{k} {d[k]} ml" for k in keys if k in d)


def build_discord(include_water=True):
    pl = plants()
    keys = [p["key"] for p in pl]
    sessions = weigh_sessions()
    if not sessions:
        return "Noch keine Gewichte eingetragen."

    waters = water_sessions()
    # Wiegen direkt nach dem Gießen: erste Messung nach einem Gieß-Zeitpunkt
    post_water = set()
    prev_ts = ""
    for s in sessions:
        if any(prev_ts < w["ts"] <= s["ts"] for w in waters):
            post_water.add(s["ts"])
        prev_ts = s["ts"]
    dry = [s for s in sessions if s["ts"] not in post_water] or sessions
    latest = dry[-1]  # Verlust immer vor dem Gießen messen
    cur_day = loc(sessions[-1]["ts"]).date()
    prev_day = cur_day - timedelta(days=1)

    shown = [s for s in sessions if loc(s["ts"]).date() in (prev_day, cur_day)]
    used = {k for s in shown for k in s["values"]}
    legend = " | ".join(f"{p['key']} = {p['name']}" for p in pl if p["key"] in used)
    lines = ["📊 Gewichte (g, mit Untersetzer)", legend, ""]
    prev_last = None
    for day in (prev_day, cur_day):
        day_sessions = [s for s in sessions if loc(s["ts"]).date() == day]
        if not day_sessions:
            continue
        lines.append(day_label(day))
        for s in day_sessions:
            vals = " | ".join(f"{k} {s['values'][k]}" for k in keys if k in s["values"])
            tag = "  (nach Gießen)" if s["ts"] in post_water else ""
            lines.append(f" {loc(s['ts']):%H:%M}   {vals}{tag}")
        lines.append("")
        if day == prev_day:
            prev_last = day_sessions[-1]  # inkl. Messung nach dem Gießen (voller Topf)

    if prev_last and prev_last["ts"] < latest["ts"]:
        hours = round((parse_ts(latest["ts"]) - parse_ts(prev_last["ts"])).total_seconds() / 3600)
        loss = {
            k: prev_last["values"][k] - latest["values"][k]
            for k in keys
            if k in prev_last["values"] and k in latest["values"]
        }
        # nur sinnvoll, wenn dazwischen nicht gegossen wurde
        watered = any(prev_last["ts"] < w["ts"] <= latest["ts"] for w in waters)
        if loss:
            pl_dt = loc(prev_last["ts"])
            head = f"Verlust seit {WD[pl_dt.weekday()]} {pl_dt:%H:%M} (~{hours} h)"
            if watered:
                head += " – dazwischen gegossen"
            lines.append(head)
            lines.append("         " + " | ".join(f"{k} {v}" for k, v in loss.items()))
            lines.append("")

    if include_water:
        wl = []
        for w in waters:
            d = loc(w["ts"]).date()
            if d in (prev_day, cur_day):
                wl.append(f"{day_label(d)} {loc(w['ts']):%H:%M} – {fmt_ml(w['ml'], keys)}")
                mix = []
                if w["ph"] is not None:
                    mix.append(f"pH {w['ph']:g}")
                if w["alfa"]:
                    mix.append(f"AlfaBoost {w['alfa']:g} ml/L")
                if w["roots"]:
                    mix.append(f"Betta Roots {w['roots']:g} ml/L")
                if w["extra"]:
                    mix.append(w["extra"])
                if mix:
                    wl.append("         " + " · ".join(mix))
        if wl:
            lines += ["💧 Gegossen"] + wl + [""]

    return "```\n" + "\n".join(lines).rstrip() + "\n```"


# ---------------------------------------------------------------- API
@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory("static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/sw.js")
def sw():
    resp = send_from_directory("static", "sw.js", mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/state")
def state():
    try:
        ensure_daily_backup()
    except OSError:
        pass
    notes = [dict(r) for r in db().execute(
        "SELECT id, ts, plant, text FROM note WHERE user_id=? ORDER BY ts DESC, id DESC LIMIT 100", (uid(),))]
    return jsonify(
        plants=plants(),
        weigh=with_deltas(weigh_sessions()),
        water=water_sessions(),
        notes=notes,
        webhook=bool(g.user["webhook"]),
        me={"username": g.user["username"], "role": g.user["role"], "share": bool(g.user["share"])},
        assist=compute_assist(),
        settings=get_settings(),
        last_backup=last_backup() if g.user["role"] == "admin" else None,
    )


def to_int(v):
    try:
        return int(round(float(str(v).replace(",", "."))))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


@app.post("/api/weigh")
def add_weigh():
    data = request.get_json(force=True)
    ts = iso(parse_ts(data.get("ts")))
    valid = {p["key"] for p in plants() if p["active"]}
    rows = []
    for k, v in (data.get("values") or {}).items():
        grams = to_int(v)
        if k in valid and grams and 100 < grams < 20000:
            rows.append((uid(), ts, k, grams))
    if not rows:
        return jsonify(error="Keine gültigen Gewichte"), 400
    db().executemany("INSERT INTO weigh(user_id, ts, plant, grams) VALUES (?,?,?,?)", rows)
    db().commit()
    return jsonify(ok=True, count=len(rows))


@app.post("/api/water")
def add_water():
    data = request.get_json(force=True)
    ts = iso(parse_ts(data.get("ts")))
    valid = {p["key"] for p in plants() if p["active"]}
    ph, alfa, roots = to_float(data.get("ph")), to_float(data.get("alfa")), to_float(data.get("roots"))
    extra = (data.get("extra") or "").strip() or None
    rows = []
    for k, v in (data.get("ml") or {}).items():
        ml = to_int(v)
        if k in valid and ml and 0 < ml < 5000:
            rows.append((uid(), ts, k, ml, ph, alfa, roots, extra))
    if not rows:
        return jsonify(error="Keine gültigen ml-Werte"), 400
    db().executemany(
        "INSERT INTO water(user_id, ts, plant, ml, ph, alfa, roots, extra) VALUES (?,?,?,?,?,?,?,?)", rows)
    db().commit()
    return jsonify(ok=True, count=len(rows))


@app.post("/api/note")
def add_note():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="Leere Notiz"), 400
    plant = data.get("plant") or None
    db().execute("INSERT INTO note(user_id, ts, plant, text) VALUES (?,?,?,?)",
                 (uid(), iso(parse_ts(data.get("ts"))), plant, text))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/<kind>/<ids>")
def delete(kind, ids):
    table = {"weigh": "weigh", "water": "water", "note": "note"}.get(kind)
    if not table:
        return jsonify(error="unbekannt"), 404
    id_list = [int(i) for i in ids.split(",") if i.isdigit()]
    if id_list:
        db().execute(f"DELETE FROM {table} WHERE user_id=? AND id IN ({','.join('?' * len(id_list))})",
                     [uid()] + id_list)
        db().commit()
    return jsonify(ok=True)


def clean_key(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()[:6]


def auto_key(name, taken):
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        base = "".join(w[0] for w in words[:3])
    elif words:
        base = words[0][:2]
    else:
        base = "P"
    base = clean_key(base) or "P"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}{n}", n + 1
    return key


@app.post("/api/plants")
def add_plant():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()[:60]
    if not name:
        return jsonify(error="Name fehlt"), 400
    taken = {p["key"] for p in plants()}
    key = clean_key(data.get("key"))
    if key in taken:
        return jsonify(error=f"Kürzel {key} gibt es schon"), 400
    key = key or auto_key(name, taken)
    nxt = db().execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM plants WHERE user_id=?", (uid(),)).fetchone()[0]
    db().execute("INSERT INTO plants(user_id, key, name, sort, active) VALUES (?,?,?,?,1)", (uid(), key, name, nxt))
    db().commit()
    return jsonify(ok=True, key=key)


@app.put("/api/plants/<key>")
def update_plant(key):
    data = request.get_json(force=True)
    row = db().execute("SELECT * FROM plants WHERE user_id=? AND key=?", (uid(), key)).fetchone()
    if not row:
        return jsonify(error="Pflanze unbekannt"), 404
    name = (data["name"] if data.get("name") is not None else row["name"]).strip()[:60]
    if not name:
        return jsonify(error="Name fehlt"), 400
    new_key = clean_key(data["key"]) if data.get("key") else key
    if not new_key:
        return jsonify(error="Kürzel ungültig"), 400
    if new_key != key and db().execute("SELECT 1 FROM plants WHERE user_id=? AND key=?", (uid(), new_key)).fetchone():
        return jsonify(error=f"Kürzel {new_key} gibt es schon"), 400
    active = row["active"] if data.get("active") is None else int(bool(data["active"]))
    target = to_int(data["target"]) if "target" in data else row["target"]
    threshold = to_int(data["threshold"]) if "threshold" in data else row["threshold"]
    db().execute("UPDATE plants SET key=?, name=?, active=?, target=?, threshold=? WHERE user_id=? AND key=?",
                 (new_key, name, active, target, threshold, uid(), key))
    if new_key != key:
        for table in ("weigh", "water", "note"):
            db().execute(f"UPDATE {table} SET plant=? WHERE user_id=? AND plant=?", (new_key, uid(), key))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/plants/<key>")
def delete_plant(key):
    used = any(
        db().execute(f"SELECT 1 FROM {t} WHERE user_id=? AND plant=? LIMIT 1", (uid(), key)).fetchone()
        for t in ("weigh", "water", "note")
    )
    if used:  # Verlauf bleibt erhalten, Pflanze wird nur ausgeblendet
        db().execute("UPDATE plants SET active=0 WHERE user_id=? AND key=?", (uid(), key))
    else:
        db().execute("DELETE FROM plants WHERE user_id=? AND key=?", (uid(), key))
    db().commit()
    return jsonify(ok=True, archived=used)


# ---------------------------------------------------------------- Konto / Admin
@app.get("/api/me")
def get_me():
    return jsonify(username=g.user["username"], role=g.user["role"], webhook=bool(g.user["webhook"]), share=bool(g.user["share"]))


@app.put("/api/me")
def put_me():
    data = request.get_json(force=True)
    resp = jsonify(ok=True)
    if "webhook" in data:
        hook = (data["webhook"] or "").strip()
        if hook and not WEBHOOK_RE.fullmatch(hook):
            return jsonify(error="Das ist keine gültige Discord-Webhook-Adresse."), 400
        db().execute("UPDATE users SET webhook=? WHERE id=?", (hook or None, uid()))
    if "share" in data:
        db().execute("UPDATE users SET share=? WHERE id=?", (1 if data["share"] else 0, uid()))
    if data.get("new_password"):
        if not check_password_hash(g.user["pw_hash"], data.get("current_password") or ""):
            return jsonify(error="Das aktuelle Passwort stimmt nicht."), 400
        problem = password_problem(data["new_password"], g.user["username"])
        if problem:
            return jsonify(error=problem), 400
        db().execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(data["new_password"]), uid()))
        db().commit()
        resp = with_session(resp, db().execute("SELECT * FROM users WHERE id=?", (uid(),)).fetchone())
    db().commit()
    return resp


@app.get("/api/community")
def community():
    rows = db().execute("SELECT id, username FROM users WHERE share=1 AND active=1 AND id<>? ORDER BY username COLLATE NOCASE",
                        (uid(),)).fetchall()
    return jsonify(users=[{"id": r["id"], "username": r["username"]} for r in rows])


@app.get("/api/profile/<int:user_id>")
def profile(user_id):
    r = db().execute("SELECT id, username FROM users WHERE id=? AND share=1 AND active=1 AND id<>?",
                     (user_id, uid())).fetchone()
    if not r:
        return jsonify(error="Dieses Profil ist nicht freigegeben."), 404
    pl = [p for p in plants(user_id) if p["active"]]
    keep = {p["key"] for p in pl}
    weigh = with_deltas([{**s, "values": {k: v for k, v in s["values"].items() if k in keep}, "ids": []}
                         for s in weigh_sessions(user_id)])
    water = [{"ts": w["ts"], "ml": {k: v for k, v in w["ml"].items() if k in keep}, "ph": w["ph"],
              "alfa": w["alfa"], "roots": w["roots"], "extra": w["extra"], "ids": []}
             for w in water_sessions(user_id)]
    return jsonify(username=r["username"], plants=[{"key": p["key"], "name": p["name"]} for p in pl],
                   weigh=weigh, water=water)


def user_json(r):
    return {"id": r["id"], "username": r["username"], "role": r["role"],
            "active": bool(r["active"]), "created": r["created"]}


def other_admins(excluding):
    return db().execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?",
                        (excluding,)).fetchone()[0]


@app.get("/api/config")
@admin_required
def get_config():
    return jsonify(tz=CFG["tz"], trust_proxy=CFG["trust_proxy"])


@app.put("/api/config")
@admin_required
def put_config():
    global TZ
    d = request.get_json(silent=True) or {}
    if "tz" in d:
        name = str(d["tz"]).strip()
        try:
            zone = ZoneInfo(name)
        except Exception:
            return jsonify(error="Unbekannte Zeitzone (z. B. Europe/Berlin)"), 400
        TZ, CFG["tz"] = zone, name
        db().execute("INSERT INTO config(key, value) VALUES ('tz', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name,))
    if "trust_proxy" in d:
        CFG["trust_proxy"] = bool(d["trust_proxy"])
        db().execute("INSERT INTO config(key, value) VALUES ('trust_proxy', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("1" if CFG["trust_proxy"] else "0",))
    db().commit()
    return jsonify(ok=True, tz=CFG["tz"], trust_proxy=CFG["trust_proxy"])


@app.get("/api/users")
@admin_required
def list_users():
    rows = db().execute("SELECT * FROM users ORDER BY id").fetchall()
    invites = db().execute(
        "SELECT token, created, expires FROM invites WHERE used_by IS NULL AND expires > ? ORDER BY created",
        (iso(now_utc()),)).fetchall()
    return jsonify(users=[user_json(r) for r in rows], invites=[dict(r) for r in invites])


@app.post("/api/users")
@admin_required
def create_user_api():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not valid_username(username):
        return jsonify(error="Benutzername: 3-32 Zeichen (Buchstaben, Zahlen, _ . -)."), 400
    if db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return jsonify(error="Diesen Benutzernamen gibt es schon."), 400
    problem = password_problem(password, username)
    if problem:
        return jsonify(error=problem), 400
    role = "admin" if data.get("role") == "admin" else "user"
    create_user(db(), username, password, role)
    db().commit()
    return jsonify(ok=True)


@app.put("/api/users/<int:user_id>")
@admin_required
def update_user(user_id):
    data = request.get_json(force=True)
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return jsonify(error="Benutzer unbekannt"), 404
    if user_id == uid() and (data.get("active") is False or data.get("role") == "user"):
        return jsonify(error="Du kannst dich nicht selbst sperren oder herabstufen."), 400
    if "active" in data:
        db().execute("UPDATE users SET active=? WHERE id=?", (int(bool(data["active"])), user_id))
    if data.get("role") in ("admin", "user"):
        if data["role"] == "user" and row["role"] == "admin" and not other_admins(user_id):
            return jsonify(error="Es muss mindestens einen Admin geben."), 400
        db().execute("UPDATE users SET role=? WHERE id=?", (data["role"], user_id))
    if data.get("password"):
        problem = password_problem(data["password"], row["username"])
        if problem:
            return jsonify(error=problem), 400
        db().execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(data["password"]), user_id))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/users/<int:user_id>")
@admin_required
def delete_user(user_id):
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return jsonify(error="Benutzer unbekannt"), 404
    if user_id == uid():
        return jsonify(error="Du kannst dich nicht selbst löschen."), 400
    if row["role"] == "admin" and not other_admins(user_id):
        return jsonify(error="Es muss mindestens einen Admin geben."), 400
    for table in ("weigh", "water", "note", "plants", "settings"):
        db().execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    db().execute("DELETE FROM invites WHERE created_by=?", (user_id,))
    db().execute("DELETE FROM users WHERE id=?", (user_id,))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/invites")
@admin_required
def create_invite():
    token = secrets.token_urlsafe(24)
    db().execute("INSERT INTO invites(token, created_by, created, expires) VALUES (?,?,?,?)",
                 (token, uid(), iso(now_utc()), iso(now_utc() + timedelta(days=INVITE_DAYS))))
    db().commit()
    return jsonify(ok=True, token=token, path=f"/register?token={token}", days=INVITE_DAYS)


@app.delete("/api/invites/<token>")
@admin_required
def delete_invite(token):
    db().execute("DELETE FROM invites WHERE token=? AND used_by IS NULL", (token,))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- Einstellungen
SETTING_DEFAULTS = {"light_on": "17:00", "light_off": "11:00", "water_time": "17:30"}


def get_settings():
    st = dict(SETTING_DEFAULTS)
    for r in db().execute("SELECT key, value FROM settings WHERE user_id=?", (uid(),)):
        if r["key"] in st:
            st[r["key"]] = r["value"]
    return st


@app.put("/api/settings")
def put_settings():
    data = request.get_json(force=True)
    for k in SETTING_DEFAULTS:
        if k in data:
            v = str(data[k]).strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                return jsonify(error=f"Ungültige Uhrzeit ({k})"), 400
            db().execute("INSERT INTO settings(user_id, key, value) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value", (uid(), k, v))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- Gieß-Assistent
def _hm(value):
    h, m = value.split(":")
    return int(h), int(m)


def lit_hours(t0, t1, on, off):
    """Stunden mit Licht zwischen zwei UTC-Zeitpunkten (Lichtfenster gilt in lokaler Zeit)."""
    if t1 <= t0:
        return 0.0
    (oh, om), (fh, fm) = _hm(on), _hm(off)
    total = 0.0
    day = t0.astimezone(TZ).date() - timedelta(days=1)
    last = t1.astimezone(TZ).date()
    while day <= last:
        start = datetime(day.year, day.month, day.day, oh, om, tzinfo=TZ).astimezone(timezone.utc)
        end_day = day if (fh, fm) > (oh, om) else day + timedelta(days=1)
        end = datetime(end_day.year, end_day.month, end_day.day, fh, fm, tzinfo=TZ).astimezone(timezone.utc)
        lo, hi = max(t0, start), min(t1, end)
        if hi > lo:
            total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total


HALF_LIFE_DAYS = 2.0   # neuere Messungen zählen stärker
DEFAULT_DARK_RATIO = 0.25
DEFAULT_GROWTH = 0.03  # Verbrauchszuwachs pro Tag, wenn zu wenig Daten für eine Schätzung da sind


def fit_model(intervals, now):
    """intervals: {pflanze: [(ende, verlust_g, licht_h, dunkel_h)]}.
    Verlust ≈ a · (licht_h + r · dunkel_h). a = Verbrauch pro Lichtstunde (neuere Messungen
    stärker gewichtet), r = Dunkel/Licht-Verhältnis (gemeinsam geschätzt), k = Wachstum pro Tag."""
    def wgt(end):
        return 0.5 ** (max(0.0, (now - end).total_seconds() / 86400) / HALF_LIFE_DAYS)

    def fit_a(rows, r):
        num = sum(wgt(e) * (li + r * da) * loss for e, loss, li, da in rows)
        den = sum(wgt(e) * (li + r * da) ** 2 for e, loss, li, da in rows)
        return (num / den) if den > 0 else None

    rows_total = sum(len(v) for v in intervals.values())
    r = DEFAULT_DARK_RATIO
    if rows_total >= 8:
        best = None
        for cand in [x / 20 for x in range(1, 13)]:  # 0,05 … 0,60
            sse = 0.0
            for rows in intervals.values():
                a = fit_a(rows, cand)
                if a is not None:
                    sse += sum(wgt(e) * (loss - a * (li + cand * da)) ** 2 for e, loss, li, da in rows)
            if best is None or sse < best[0]:
                best = (sse, cand)
        r = best[1]

    slopes = []
    for rows in intervals.values():
        pts = [((e - now).total_seconds() / 86400, math.log(loss / (li + r * da)))
               for e, loss, li, da in rows if li + r * da >= 4 and loss > 0]
        if len(pts) >= 3 and pts[-1][0] - pts[0][0] >= 1.5:
            mx = sum(x for x, _ in pts) / len(pts)
            my = sum(y for _, y in pts) / len(pts)
            var = sum((x - mx) ** 2 for x, _ in pts)
            if var > 0:
                slopes.append(sum((x - mx) * (y - my) for x, y in pts) / var)
    k = max(0.0, min(0.10, statistics.median(slopes))) if slopes else DEFAULT_GROWTH
    return {key: fit_a(rows, r) for key, rows in intervals.items()}, r, k


def project(cur, start, a, r, k, st, days=7):
    """Erwartetes Gewicht in 15-Minuten-Schritten ab der Messung (Licht/Dunkel, Wachstum)."""
    pts, t, w = [], start, float(cur)
    step = timedelta(minutes=15)
    for _ in range(days * 96):
        t2 = t + step
        lit = lit_hours(t, t2, st["light_on"], st["light_off"])
        growth = math.exp(k * (t - start).total_seconds() / 86400)
        w -= a * growth * (lit + r * (0.25 - lit))
        t = t2
        pts.append((t, w))
    return pts


def weight_at(pts, start, when):
    idx = int((when - start).total_seconds() // 900) - 1
    return pts[max(0, min(len(pts) - 1, idx))][1]


def compute_assist(now=None):
    """Richtwerte pro Pflanze: Zielgewicht (auto oder manuell), Verbrauch getrennt
    nach Licht/Dunkel, Prognose zur nächsten Gießzeit und ml-Vorschlag."""
    now = now or now_utc()
    st = get_settings()
    sessions, waters = weigh_sessions(), water_sessions()
    info, intervals = {}, {}
    for p in plants():
        if not p["active"]:
            continue
        k = p["key"]
        entries = [(s["ts"], s["values"][k]) for s in sessions if k in s["values"]]
        if not entries:
            continue
        wlist = [w for w in waters if k in w["ml"]]
        post, prev = [], ""
        for ts, _g in entries:
            post.append(any(prev < w["ts"] <= ts for w in wlist))  # erste Messung nach dem Gießen
            prev = ts

        auto_target = None
        for i in range(len(entries) - 1, -1, -1):
            if post[i]:
                auto_target = entries[i][1]
                break

        ratios = []  # gemessene g Zuwachs pro ml
        for i in range(1, len(entries)):
            if post[i] and (parse_ts(entries[i][0]) - parse_ts(entries[i - 1][0])).total_seconds() <= 6 * 3600:
                ml = sum(w["ml"][k] for w in wlist if entries[i - 1][0] < w["ts"] <= entries[i][0])
                gain = entries[i][1] - entries[i - 1][1]
                if ml > 0 and gain > 0:
                    ratios.append(gain / ml)
        ratio = max(0.8, min(1.3, statistics.median(ratios))) if ratios else 1.0

        rows = []
        for i in range(1, len(entries)):
            if post[i]:
                continue
            t0, t1 = parse_ts(entries[i - 1][0]), parse_ts(entries[i][0])
            hrs = (t1 - t0).total_seconds() / 3600
            loss = entries[i - 1][1] - entries[i][1]
            if hrs >= 3 and loss >= 0 and (now - t1).total_seconds() <= 10 * 86400:
                lit = lit_hours(t0, t1, st["light_on"], st["light_off"])
                rows.append((t1, loss, lit, hrs - lit))
        if rows:
            intervals[k] = rows
        info[k] = {"plant": p, "entries": entries, "ratio": ratio, "auto_target": auto_target}

    rates, r, growth = fit_model(intervals, now) if intervals else ({}, DEFAULT_DARK_RATIO, DEFAULT_GROWTH)

    out = {}
    for k, d in info.items():
        p, entries, ratio = d["plant"], d["entries"], d["ratio"]
        cur_ts, cur = entries[-1]
        start = parse_ts(cur_ts)
        target = p["target"] or d["auto_target"]
        a = rates.get(k)
        res = {
            "current": cur, "current_ts": cur_ts,
            "target": target, "target_manual": bool(p["target"]), "auto_target": d["auto_target"],
            "ratio": round(ratio, 2), "use24": None, "rate_lit": None, "rate_dark": None,
            "slot_at": None, "slot_expected": None, "slot_today": None, "suggest_ml": 0,
        }
        if a:
            pts = project(cur, start, a, r, growth, st)
            lit24 = lit_hours(now, now + timedelta(hours=24), st["light_on"], st["light_off"])
            res["rate_lit"], res["rate_dark"] = round(a, 1), round(a * r, 1)
            res["use24"] = round(a * (lit24 + r * (24 - lit24)))
            # Gießtermin: nächste Gießzeit aus den Einstellungen (ob gegossen wird, entscheidet Erik nach Gefühl)
            wh, wm = _hm(st["water_time"])
            nl = now.astimezone(TZ)
            chosen = datetime(nl.year, nl.month, nl.day, wh, wm, tzinfo=TZ).astimezone(timezone.utc)
            if chosen < now:
                chosen += timedelta(days=1)
            expected = weight_at(pts, start, chosen)
            res["slot_at"], res["slot_expected"] = iso(chosen), round(expected)
            res["slot_today"] = chosen.astimezone(TZ).date() == nl.date()
            if target and expected < target - 5:
                res["suggest_ml"] = int(round((target - expected) / ratio / 5.0) * 5)
        elif target and cur < target - 5:
            res["suggest_ml"] = int(round((target - cur) / ratio / 5.0) * 5)
        out[k] = res
    return out


# ---------------------------------------------------------------- Export / Backup
BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH) or ".", "backups")
BACKUP_KEEP = 14


def backup_to(path):
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_files():
    if not os.path.isdir(BACKUP_DIR):
        return []
    return sorted(f for f in os.listdir(BACKUP_DIR) if re.fullmatch(r"growlog-\d{4}-\d\d-\d\d\.db", f))


def ensure_daily_backup():
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    path = os.path.join(BACKUP_DIR, f"growlog-{day}.db")
    if os.path.exists(path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_to(path)
    for old in backup_files()[:-BACKUP_KEEP]:
        os.unlink(os.path.join(BACKUP_DIR, old))


def last_backup():
    files = backup_files()
    return files[-1][8:-3] if files else None


def de_num(v):
    return "" if v is None else f"{v:g}".replace(".", ",")


def local_str(ts):
    return f"{loc(ts):%Y-%m-%d %H:%M}"


@app.get("/api/csv/<kind>")
def export_csv(kind):
    names = {p["key"]: p["name"] for p in plants()}
    if kind == "weigh":
        header = ["Zeit", "Kürzel", "Pflanze", "Gramm"]
        rows = [[local_str(r["ts"]), r["plant"], names.get(r["plant"], ""), r["grams"]]
                for r in db().execute("SELECT ts, plant, grams FROM weigh WHERE user_id=? ORDER BY ts, id", (uid(),))]
    elif kind == "water":
        header = ["Zeit", "Kürzel", "Pflanze", "ml", "pH", "AlfaBoost ml/L", "Betta Roots ml/L", "Sonstiges"]
        rows = [[local_str(r["ts"]), r["plant"], names.get(r["plant"], ""), r["ml"],
                 de_num(r["ph"]), de_num(r["alfa"]), de_num(r["roots"]), r["extra"] or ""]
                for r in db().execute("SELECT * FROM water WHERE user_id=? ORDER BY ts, id", (uid(),))]
    elif kind == "notes":
        header = ["Zeit", "Kürzel", "Notiz"]
        rows = [[local_str(r["ts"]), r["plant"] or "", r["text"]]
                for r in db().execute("SELECT ts, plant, text FROM note WHERE user_id=? ORDER BY ts, id", (uid(),))]
    else:
        return jsonify(error="unbekannt"), 404
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="growlog-{kind}-{datetime.now(TZ):%Y-%m-%d}.csv"'})


@app.get("/api/backup")
@admin_required
def backup_download():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    backup_to(tmp.name)
    resp = send_file(tmp.name, as_attachment=True, mimetype="application/octet-stream",
                     download_name=f"growlog-{datetime.now(TZ):%Y-%m-%d_%H%M}.db")
    resp.call_on_close(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
    return resp


@app.post("/api/restore")
@admin_required
def restore():
    upload = request.files.get("file")
    if not upload:
        return jsonify(error="Keine Datei gewählt"), 400
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        upload.save(tmp.name)
        src = sqlite3.connect(tmp.name)
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"plants", "weigh", "water", "note"} <= tables:
                return jsonify(error="Das ist keine GrowLog-Datenbank"), 400
            os.makedirs(BACKUP_DIR, exist_ok=True)  # Sicherheitskopie vor dem Überschreiben
            backup_to(os.path.join(BACKUP_DIR, f"vor-restore-{datetime.now(TZ):%Y-%m-%d_%H%M%S}.db"))
            dst = sqlite3.connect(DB_PATH)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.DatabaseError:
        return jsonify(error="Datei ist keine gültige Datenbank"), 400
    finally:
        os.unlink(tmp.name)
    init_db()  # Migrationen auf ältere Dateien anwenden
    return jsonify(ok=True)


@app.get("/api/discord")
def discord_text():
    return jsonify(text=build_discord(request.args.get("water", "1") != "0"))


@app.post("/api/discord/post")
def discord_post():
    hook = g.user["webhook"]
    if not hook or not WEBHOOK_RE.fullmatch(hook):
        return jsonify(error="Kein Discord-Webhook hinterlegt (Tab Konto)"), 400
    data = request.get_json(silent=True) or {}
    text = data.get("text") or build_discord()
    req = urllib.request.Request(
        hook,
        data=json.dumps({"content": text[:1990]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "GrowLog/1.0"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.URLError as exc:
        return jsonify(error=f"Discord: {exc}"), 502
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8087)))
