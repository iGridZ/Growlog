"""GrowLog – kleines Grow-Tagebuch: Wiegen, Gießen (ml), Notizen, Discord-Export."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, redirect, request, send_file, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Berlin"))
DB_PATH = os.environ.get("DB_PATH", "/data/growlog.db")
PASSWORD = os.environ.get("APP_PASSWORD", "")
USER = os.environ.get("APP_USER", "grow")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # Restore-Upload
if os.environ.get("TRUST_PROXY", "1") == "1":  # hinter Nginx Proxy Manager: echte IP und https übernehmen
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plants (
  key TEXT PRIMARY KEY, name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, target INTEGER, threshold INTEGER
);
CREATE TABLE IF NOT EXISTS weigh (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  plant TEXT NOT NULL, grams INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS water (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  plant TEXT NOT NULL, ml INTEGER NOT NULL,
  ph REAL, alfa REAL, roots REAL, extra TEXT
);
CREATE TABLE IF NOT EXISTS note (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  plant TEXT, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS i_weigh_ts ON weigh(ts);
CREATE INDEX IF NOT EXISTS i_water_ts ON water(ts);
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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plants)")]
    if "active" not in cols:  # Migration für Datenbanken aus Version 1
        conn.execute("ALTER TABLE plants ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    for col in ("target", "threshold"):
        if col not in cols:
            conn.execute(f"ALTER TABLE plants ADD COLUMN {col} INTEGER")
    conn.commit()
    conn.close()


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
PUBLIC_PATHS = ("/login", "/manifest.webmanifest", "/sw.js", "/static/style.css", "/static/icons/")
MAX_FAILS, LOCK_SECONDS = 5, 300
_fails = {}  # ip -> (fehlversuche, gesperrt_bis)
_serializer = None


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


def _pw_tag():
    return hashlib.sha256(PASSWORD.encode()).hexdigest()[:16]


def make_token():
    global _serializer
    _serializer = _serializer or URLSafeTimedSerializer(_secret(), salt="growlog-session")
    return _serializer.dumps({"u": USER, "p": _pw_tag()})


def valid_token(token):
    global _serializer
    _serializer = _serializer or URLSafeTimedSerializer(_secret(), salt="growlog-session")
    try:
        data = _serializer.loads(token, max_age=SESSION_DAYS * 86400)
    except BadSignature:
        return False
    # Passwortwechsel macht alte Sessions ungültig
    return isinstance(data, dict) and data.get("u") == USER and data.get("p") == _pw_tag()


@app.before_request
def require_auth():
    if not PASSWORD or request.path.startswith(PUBLIC_PATHS):
        return None
    if valid_token(request.cookies.get(COOKIE, "")):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Nicht angemeldet"), 401
    return redirect("/login")


def login_page(error=""):
    with open(os.path.join(app.static_folder, "login.html"), encoding="utf-8") as f:
        html = f.read().replace("<!--ERR-->", error)
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_form():
    if not PASSWORD or valid_token(request.cookies.get(COOKIE, "")):
        return redirect("/")
    return login_page()


@app.post("/login")
def login_submit():
    if not PASSWORD:
        return redirect("/")
    ip = request.remote_addr or "?"
    count, locked_until = _fails.get(ip, (0, 0))
    if locked_until > time.time():
        return login_page('<p class="err">Zu viele Versuche. Bitte in ein paar Minuten erneut versuchen.</p>'), 429
    user_ok = hmac.compare_digest(request.form.get("username", "").encode(), USER.encode())
    pw_ok = hmac.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode())
    if not (user_ok and pw_ok):
        count += 1
        _fails[ip] = (0, time.time() + LOCK_SECONDS) if count >= MAX_FAILS else (count, 0)
        time.sleep(0.5)
        return login_page('<p class="err">Benutzername oder Passwort falsch.</p>'), 401
    _fails.pop(ip, None)
    resp = redirect("/")
    resp.set_cookie(COOKIE, make_token(), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Lax", secure=request.is_secure)
    return resp


@app.post("/logout")
def logout():
    resp = jsonify(ok=True)
    resp.delete_cookie(COOKIE)
    return resp


# ---------------------------------------------------------------- Daten
def plants():
    rows = db().execute(
        "SELECT key, name, active, target, threshold FROM plants ORDER BY sort, key").fetchall()
    return [{"key": r["key"], "name": r["name"], "active": bool(r["active"]),
             "target": r["target"], "threshold": r["threshold"]} for r in rows]


def weigh_sessions():
    """Gruppiert nach Zeitstempel; neueste zuletzt."""
    rows = db().execute("SELECT id, ts, plant, grams FROM weigh ORDER BY ts, id").fetchall()
    sessions = {}
    for r in rows:
        s = sessions.setdefault(r["ts"], {"ts": r["ts"], "values": {}, "ids": []})
        s["values"][r["plant"]] = r["grams"]
        s["ids"].append(r["id"])
    return list(sessions.values())


def water_sessions():
    rows = db().execute(
        "SELECT id, ts, plant, ml, ph, alfa, roots, extra FROM water ORDER BY ts, id"
    ).fetchall()
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
        "SELECT id, ts, plant, text FROM note ORDER BY ts DESC, id DESC LIMIT 100")]
    return jsonify(
        plants=plants(),
        weigh=with_deltas(weigh_sessions()),
        water=water_sessions(),
        notes=notes,
        webhook=bool(WEBHOOK),
        auth=bool(PASSWORD),
        assist=compute_assist(),
        last_backup=last_backup(),
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
            rows.append((ts, k, grams))
    if not rows:
        return jsonify(error="Keine gültigen Gewichte"), 400
    db().executemany("INSERT INTO weigh(ts, plant, grams) VALUES (?,?,?)", rows)
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
            rows.append((ts, k, ml, ph, alfa, roots, extra))
    if not rows:
        return jsonify(error="Keine gültigen ml-Werte"), 400
    db().executemany(
        "INSERT INTO water(ts, plant, ml, ph, alfa, roots, extra) VALUES (?,?,?,?,?,?,?)", rows)
    db().commit()
    return jsonify(ok=True, count=len(rows))


@app.post("/api/note")
def add_note():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="Leere Notiz"), 400
    plant = data.get("plant") or None
    db().execute("INSERT INTO note(ts, plant, text) VALUES (?,?,?)",
                 (iso(parse_ts(data.get("ts"))), plant, text))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/<kind>/<ids>")
def delete(kind, ids):
    table = {"weigh": "weigh", "water": "water", "note": "note"}.get(kind)
    if not table:
        return jsonify(error="unbekannt"), 404
    id_list = [int(i) for i in ids.split(",") if i.isdigit()]
    if id_list:
        db().execute(f"DELETE FROM {table} WHERE id IN ({','.join('?' * len(id_list))})", id_list)
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
    nxt = db().execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM plants").fetchone()[0]
    db().execute("INSERT INTO plants(key, name, sort, active) VALUES (?,?,?,1)", (key, name, nxt))
    db().commit()
    return jsonify(ok=True, key=key)


@app.put("/api/plants/<key>")
def update_plant(key):
    data = request.get_json(force=True)
    row = db().execute("SELECT * FROM plants WHERE key=?", (key,)).fetchone()
    if not row:
        return jsonify(error="Pflanze unbekannt"), 404
    name = (data["name"] if data.get("name") is not None else row["name"]).strip()[:60]
    if not name:
        return jsonify(error="Name fehlt"), 400
    new_key = clean_key(data["key"]) if data.get("key") else key
    if not new_key:
        return jsonify(error="Kürzel ungültig"), 400
    if new_key != key and db().execute("SELECT 1 FROM plants WHERE key=?", (new_key,)).fetchone():
        return jsonify(error=f"Kürzel {new_key} gibt es schon"), 400
    active = row["active"] if data.get("active") is None else int(bool(data["active"]))
    target = to_int(data["target"]) if "target" in data else row["target"]
    threshold = to_int(data["threshold"]) if "threshold" in data else row["threshold"]
    db().execute("UPDATE plants SET key=?, name=?, active=?, target=?, threshold=? WHERE key=?",
                 (new_key, name, active, target, threshold, key))
    if new_key != key:
        for table in ("weigh", "water", "note"):
            db().execute(f"UPDATE {table} SET plant=? WHERE plant=?", (new_key, key))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/plants/<key>")
def delete_plant(key):
    used = any(
        db().execute(f"SELECT 1 FROM {t} WHERE plant=? LIMIT 1", (key,)).fetchone()
        for t in ("weigh", "water", "note")
    )
    if used:  # Verlauf bleibt erhalten, Pflanze wird nur ausgeblendet
        db().execute("UPDATE plants SET active=0 WHERE key=?", (key,))
    else:
        db().execute("DELETE FROM plants WHERE key=?", (key,))
    db().commit()
    return jsonify(ok=True, archived=used)


# ---------------------------------------------------------------- Gieß-Assistent
def compute_assist():
    """Richtwerte pro Pflanze: Ziel- und Schwellengewicht (auto aus dem letzten Gießen
    oder manuell), Verbrauch pro 24 h, ml-Vorschlag und voraussichtlich nächstes Gießen."""
    now = now_utc()
    sessions, waters = weigh_sessions(), water_sessions()
    out = {}
    for p in plants():
        if not p["active"]:
            continue
        k = p["key"]
        entries = [(s["ts"], s["values"][k]) for s in sessions if k in s["values"]]
        if not entries:
            continue
        wts = [w["ts"] for w in waters if k in w["ml"]]
        post, prev = [], ""
        for ts, _g in entries:
            post.append(any(prev < wt <= ts for wt in wts))  # erste Messung nach dem Gießen
            prev = ts
        cur_ts, cur = entries[-1]

        auto_target = auto_thr = None
        for i in range(len(entries) - 1, -1, -1):
            if post[i]:
                auto_target = entries[i][1]
                if i > 0:
                    gap = (parse_ts(entries[i][0]) - parse_ts(entries[i - 1][0])).total_seconds() / 3600
                    if gap <= 36:
                        auto_thr = entries[i - 1][1]
                break
        target = p["target"] or auto_target
        threshold = p["threshold"] or auto_thr

        loss = hours = 0.0
        for i in range(1, len(entries)):
            if post[i]:
                continue
            dt_end = parse_ts(entries[i][0])
            h = (dt_end - parse_ts(entries[i - 1][0])).total_seconds() / 3600
            if h <= 0 or (now - dt_end).total_seconds() > 4 * 86400:
                continue
            loss += entries[i - 1][1] - entries[i][1]
            hours += h
        use24 = round(loss / hours * 24) if hours >= 6 and loss > 0 else None

        hours_left = due_at = None
        if threshold and use24:
            elapsed = (now - parse_ts(cur_ts)).total_seconds() / 3600
            hours_left = max(0.0, (cur - threshold) / use24 * 24 - elapsed)
            due_at = iso(now + timedelta(hours=hours_left))
        pct = None
        if target and threshold and target > threshold:
            pct = max(0, min(100, round((cur - threshold) / (target - threshold) * 100)))
        out[k] = {
            "current": cur, "current_ts": cur_ts,
            "target": target, "target_manual": bool(p["target"]), "auto_target": auto_target,
            "threshold": threshold, "threshold_manual": bool(p["threshold"]), "auto_threshold": auto_thr,
            "use24": use24, "hours_left": None if hours_left is None else round(hours_left, 1),
            "due_at": due_at, "pct": pct,
            "suggest_ml": int(round((target - cur) / 5.0) * 5) if target and cur < target - 5 else 0,
        }
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
                for r in db().execute("SELECT ts, plant, grams FROM weigh ORDER BY ts, id")]
    elif kind == "water":
        header = ["Zeit", "Kürzel", "Pflanze", "ml", "pH", "AlfaBoost ml/L", "Betta Roots ml/L", "Sonstiges"]
        rows = [[local_str(r["ts"]), r["plant"], names.get(r["plant"], ""), r["ml"],
                 de_num(r["ph"]), de_num(r["alfa"]), de_num(r["roots"]), r["extra"] or ""]
                for r in db().execute("SELECT * FROM water ORDER BY ts, id")]
    elif kind == "notes":
        header = ["Zeit", "Kürzel", "Notiz"]
        rows = [[local_str(r["ts"]), r["plant"] or "", r["text"]]
                for r in db().execute("SELECT ts, plant, text FROM note ORDER BY ts, id")]
    else:
        return jsonify(error="unbekannt"), 404
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="growlog-{kind}-{datetime.now(TZ):%Y-%m-%d}.csv"'})


@app.get("/api/backup")
def backup_download():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    backup_to(tmp.name)
    resp = send_file(tmp.name, as_attachment=True, mimetype="application/octet-stream",
                     download_name=f"growlog-{datetime.now(TZ):%Y-%m-%d_%H%M}.db")
    resp.call_on_close(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
    return resp


@app.post("/api/restore")
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
    if not WEBHOOK:
        return jsonify(error="DISCORD_WEBHOOK_URL nicht gesetzt"), 400
    data = request.get_json(silent=True) or {}
    text = data.get("text") or build_discord()
    req = urllib.request.Request(
        WEBHOOK,
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
