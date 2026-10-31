from datetime import datetime, timedelta, timezone

import app as growlog

UTC = timezone.utc


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(c, steps=14, loss=60, water_at=None):
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=12 * steps)
    w = 3300
    for i in range(steps):
        c.post("/api/weigh", json={"ts": fmt(base + timedelta(hours=12 * i)), "values": {"AA": w}})
        w -= loss
    return base


def test_insights_empty(login):
    c, _ = login("anna")
    d = c.get("/api/insights").get_json()
    assert len(d["week"]["days"]) == 7 and d["week"]["ml_total"] == 0 and d["backtest"] == {}


def test_week_summary_counts_ml_and_loss(login):
    c, _ = login("anna")
    seed(c)
    c.post("/api/water", json={"ts": fmt(datetime.now(UTC) - timedelta(minutes=5)), "ml": {"AA": 250}})
    week = c.get("/api/insights").get_json()["week"]
    assert week["ml_total"] == 250
    # 14 Messungen, 13 Intervalle à 60 g; nur die letzten 7 Tage zählen, ungefähr 14 Intervalle * 60 g / 2 Tage
    assert 300 <= week["loss_total"] <= 13 * 60
    assert week["days"][-1]["ml"] == 250


def test_week_summary_ignores_watering_interval(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    now = datetime.now(UTC).replace(microsecond=0)
    c.post("/api/weigh", json={"ts": fmt(now - timedelta(hours=10)), "values": {"AA": 3000}})
    c.post("/api/water", json={"ts": fmt(now - timedelta(hours=6)), "ml": {"AA": 300}})
    c.post("/api/weigh", json={"ts": fmt(now - timedelta(hours=5)), "values": {"AA": 3250}})
    week = c.get("/api/insights").get_json()["week"]
    assert week["loss_total"] == 0 and week["ml_total"] == 300


def test_backtest_on_steady_loss_is_close(login):
    c, _ = login("anna")
    seed(c)
    bt = c.get("/api/insights").get_json()["backtest"]["AA"]
    assert bt["rows"] and all({"ts", "predicted", "actual", "err"} <= set(r) for r in bt["rows"])
    assert bt["mae"] < 60  # gleichmäßiger Verlust: Prognose liegt im Bereich eines Intervalls


def test_backtest_skips_watering_intervals(login):
    c, _ = login("anna")
    base = seed(c, steps=8)
    t = base + timedelta(hours=12 * 7 + 1)
    c.post("/api/water", json={"ts": fmt(t), "ml": {"AA": 200}})
    c.post("/api/weigh", json={"ts": fmt(t + timedelta(minutes=30)), "values": {"AA": 3000}})
    rows = c.get("/api/insights").get_json()["backtest"]["AA"]["rows"]
    assert all(r["ts"] != fmt(t + timedelta(minutes=30)) for r in rows)


def test_insights_only_own_data(login):
    a, _ = login("anna")
    b, _ = login("bernd")
    seed(a)
    d = b.get("/api/insights").get_json()
    assert d["backtest"] == {} and d["week"]["ml_total"] == 0 and d["week"]["loss_total"] == 0
