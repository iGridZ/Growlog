from datetime import datetime, timedelta, timezone

import app as growlog

UTC = timezone.utc


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_lit_hours_window_crossing_midnight():
    tz = growlog.TZ
    t0 = datetime(2026, 10, 20, 12, 0, tzinfo=tz).astimezone(UTC)  # nach Licht-Aus 11:00
    t1 = datetime(2026, 10, 20, 20, 0, tzinfo=tz).astimezone(UTC)  # 17:00-20:00 = 3 h Licht
    assert growlog.lit_hours(t0, t1, "17:00", "11:00") == 3.0
    t0 = datetime(2026, 10, 20, 17, 0, tzinfo=tz).astimezone(UTC)
    assert growlog.lit_hours(t0, t0 + timedelta(hours=24), "17:00", "11:00") == 18.0


def test_lit_hours_empty_interval():
    t = datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert growlog.lit_hours(t, t, "17:00", "11:00") == 0.0


def test_fit_model_recovers_rate():
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    rows = [(now - timedelta(days=d), 40 * (10.0 + 0.25 * 14.0), 10.0, 14.0) for d in range(1, 6)]
    rates, r, k = growlog.fit_model({"AA": rows}, now)
    assert abs(rates["AA"] - 40) < 1
    assert r == growlog.DEFAULT_DARK_RATIO  # weniger als 8 Intervalle: Standardverhältnis
    assert 0 <= k <= 0.10


def test_project_decreases_monotonically():
    start = datetime(2026, 10, 27, 12, tzinfo=UTC)
    st = {"light_on": "17:00", "light_off": "11:00", "water_time": "17:30"}
    pts = growlog.project(3000, start, 30, 0.25, 0.03, st, days=2)
    weights = [w for _, w in pts]
    assert weights == sorted(weights, reverse=True) and weights[-1] < 3000


def test_assist_via_api_gives_suggestion(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=4)
    w = 3200
    for i in range(8):  # 2 Messungen pro Tag, fallendes Gewicht
        c.post("/api/weigh", json={"ts": fmt(base + timedelta(hours=12 * i)), "values": {"AA": w}})
        w -= 60
    t_w = base + timedelta(hours=12 * 7 + 1)  # Gießen, danach Messung -> Ziel
    c.post("/api/water", json={"ts": fmt(t_w), "ml": {"AA": 200}})
    c.post("/api/weigh", json={"ts": fmt(t_w + timedelta(minutes=30)), "values": {"AA": w + 200}})
    a = c.get("/api/state").get_json()["assist"]["AA"]
    assert a["auto_target"] == w + 200
    assert a["rate_lit"] and a["use24"] > 0
    assert a["slot_at"] and a["slot_expected"] is not None
    assert a["suggest_ml"] % 5 == 0


def test_manual_target_wins_over_auto(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    c.post("/api/weigh", json={"ts": "2026-10-20T07:00:00Z", "values": {"AA": 3000}})
    c.put("/api/plants/AA", json={"target": 3300, "threshold": 3100})
    a = c.get("/api/state").get_json()["assist"]["AA"]
    assert a["target"] == 3300 and a["target_manual"] and a["threshold"] == 3100
    assert a["suggest_ml"] > 0
