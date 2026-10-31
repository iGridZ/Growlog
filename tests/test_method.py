from datetime import datetime, timedelta, timezone

import pytest

import app as growlog

UTC = timezone.utc


def fake_backtest(ema_errs, avg_errs):
    def fake(u, now, n=6, half_life=growlog.HALF_LIFE_DAYS):
        errs = ema_errs if half_life else avg_errs
        return {"AA": {"rows": [{"err": e} for e in errs], "mae": 0}}
    return fake


def pick(monkeypatch, uid, ema_errs, avg_errs):
    monkeypatch.setattr(growlog, "backtest", fake_backtest(ema_errs, avg_errs))
    with growlog.app.app_context():
        return growlog.pick_method(uid, datetime.now(UTC))


def test_fit_model_average_vs_ema():
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    old = [(now - timedelta(days=d), 100.0, 10.0, 14.0) for d in (8, 9)]   # früher hoher Verbrauch
    new = [(now - timedelta(days=d), 60.0, 10.0, 14.0) for d in (1, 2)]    # zuletzt niedriger
    rows = {"AA": old + new}
    avg = growlog.fit_model(rows, now, None)[0]["AA"]
    ema = growlog.fit_model(rows, now, 2.0)[0]["AA"]
    plain = sum(x[1] for x in old + new) / 4 / 13.5
    assert avg == pytest.approx(plain, rel=0.02)
    assert ema < avg  # EMA folgt den neueren, niedrigeren Werten


def test_ema_chosen_only_when_clearly_better(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, [10, -10, 12, -8, 9], [30, -25, 28, -30, 22])
    assert r["method"] == "ema" and r["half_life"] == growlog.HALF_LIFE_DAYS
    assert r["mae_ema"] < r["mae_avg"]


def test_slightly_better_ema_is_not_enough(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, [19, -19, 19, -19], [20, -20, 20, -20])  # nur 5 % besser
    assert r["method"] == "avg" and r["half_life"] is None


def test_worse_or_equal_ema_stays_with_average(login, monkeypatch):
    _, uid = login("anna")
    assert pick(monkeypatch, uid, [30, 30, 30, 30], [10, 10, 10, 10])["method"] == "avg"
    growlog._method_cache.clear()
    assert pick(monkeypatch, uid, [10, 10, 10, 10], [10, 10, 10, 10])["method"] == "avg"


def test_too_few_rows_stays_with_average(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, [1, 1, 1], [50, 50, 50])
    assert r["method"] == "avg"


def test_perfect_average_stays(login, monkeypatch):
    _, uid = login("anna")
    assert pick(monkeypatch, uid, [0, 0, 0, 0], [0, 0, 0, 0])["method"] == "avg"


def test_result_is_cached_until_data_changes(login, monkeypatch):
    c, uid = login("anna")
    calls = []
    real = fake_backtest([1] * 5, [9] * 5)

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(growlog, "backtest", counting)
    with growlog.app.app_context():
        growlog.pick_method(uid, datetime.now(UTC))
        n = len(calls)
        growlog.pick_method(uid, datetime.now(UTC))
        assert len(calls) == n
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    c.post("/api/weigh", json={"values": {"AA": 3000}})
    with growlog.app.app_context():
        growlog.pick_method(uid, datetime.now(UTC))
    assert len(calls) > n


def test_assist_uses_chosen_half_life(login, monkeypatch):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    c.post("/api/weigh", json={"values": {"AA": 3000}})
    seen = []
    real = growlog.fit_model
    monkeypatch.setattr(growlog, "fit_model", lambda rows, now, half_life=2.0: seen.append(half_life) or real(rows, now, half_life))
    monkeypatch.setattr(growlog, "pick_method", lambda u, now: {"method": "avg", "half_life": None})
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    for i in range(1, 6):
        c.post("/api/weigh", json={"ts": (base + timedelta(hours=12 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "values": {"AA": 3000 - 60 * i}})
    c.get("/api/state")
    assert seen and set(seen) == {None}


def test_insights_reports_method(login):
    c, _ = login("anna")
    d = c.get("/api/insights").get_json()
    assert d["method"]["method"] == "avg" and d["method"]["half_life"] is None
