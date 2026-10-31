from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def weigh(c, ts, grams):
    assert c.post("/api/weigh", json={"ts": fmt(ts), "values": {"AA": grams}}).status_code == 200


def setup(login, ratio_ml=None):
    """Mehrere Tage fallendes Gewicht; optional ein eingetragenes Gießen (100 ml -> +120 g = 1,2 g/ml)."""
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=12 * 8)
    w = 3300
    for i in range(6):
        weigh(c, base + timedelta(hours=12 * i), w)
        w -= 60
    w += 60  # w = zuletzt gemessenes Gewicht
    t = base + timedelta(hours=12 * 5 + 1)
    if ratio_ml:
        c.post("/api/water", json={"ts": fmt(t), "ml": {"AA": ratio_ml}})
        w += int(ratio_ml * 1.2)
        weigh(c, t + timedelta(minutes=30), w)
        t += timedelta(hours=11)
        w -= 50
        weigh(c, t, w)
    return c, t, w


def assist(c):
    return c.get("/api/state").get_json()["assist"]["AA"]


def test_no_hint_without_jump(login):
    c, _, _ = setup(login)
    assert assist(c)["unlogged"] == []


def test_forgotten_watering_estimated_with_logged_ratio(login):
    c, t, w = setup(login, ratio_ml=100)
    jump = t + timedelta(hours=11)
    weigh(c, jump, w + 240)  # +240 g ohne Gießeintrag
    ev = assist(c)["unlogged"]
    assert len(ev) == 1 and ev[0]["ts"] == fmt(jump) and ev[0]["gain"] == 240
    # Verhältnis 1,2 g/ml aus dem eingetragenen Gießen: 240 g / 1,2 = 200 ml plus Verbrauch im Intervall
    assert ev[0]["tol"] == 5
    assert 200 - 5 <= ev[0]["ml"] <= 300


def test_without_logged_watering_ratio_defaults_to_one(login):
    c, t, w = setup(login)
    weigh(c, t + timedelta(hours=12), w + 200)
    ev = assist(c)["unlogged"]
    assert len(ev) == 1 and ev[0]["ml"] >= 200


def test_logging_the_watering_clears_the_hint(login):
    c, t, w = setup(login, ratio_ml=100)
    jump = t + timedelta(hours=11)
    weigh(c, jump, w + 240)
    ev = assist(c)["unlogged"][0]
    assert c.post("/api/water", json={"ts": ev["ts"], "ml": {"AA": ev["ml"]}}).status_code == 200
    assert assist(c)["unlogged"] == []


def test_small_gain_is_ignored(login):
    c, t, w = setup(login)
    weigh(c, t + timedelta(hours=12), w + 20)
    assert assist(c)["unlogged"] == []


def test_old_events_not_shown(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    old = datetime.now(UTC) - timedelta(days=10)
    weigh(c, old, 3000)
    weigh(c, old + timedelta(hours=12), 3300)
    weigh(c, datetime.now(UTC) - timedelta(hours=1), 3250)
    assert assist(c)["unlogged"] == []
