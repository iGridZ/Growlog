from datetime import datetime, timedelta, timezone

import pytest

import app as growlog

UTC = timezone.utc
DEFAULT = growlog.DEFAULT_METHOD


def fake_runs(errs_by_method, default_errs):
    """Ersetzt backtest_methods: je Methode feste Fehlerlisten, alles andere wie das Standardverfahren."""
    def fake(u, now, n, methods):
        return {m: {"AA": [{"err": e} for e in errs_by_method.get(m, default_errs)]} for m in methods}
    return fake


def pick(monkeypatch, uid, errs_by_method, default_errs=(30, -30, 30, -30, 30)):
    monkeypatch.setattr(growlog, "backtest_methods", fake_runs(errs_by_method, default_errs))
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


def test_most_accurate_method_is_chosen(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {(None, 0.0): [10, -10, 10, -10, 10]})  # 10 g gegen 30 g
    assert (r["half_life"], r["growth_scale"]) == (None, 0.0)
    assert r["mae"] == 10 and r["mae_default"] == 30 and r["enough"]
    assert "Durchschnitt" in r["label"] and "Wachstum aus" in r["label"]


def test_ema_variant_can_win(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {(4.0, 0.5): [5, -5, 5, -5, 5], (None, 1.0): [28, -28, 28, -28, 28]})
    assert (r["half_life"], r["growth_scale"]) == (4.0, 0.5)
    assert r["mae_average"] == 28


def test_tiny_difference_keeps_default(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {(1.0, 1.0): [29.8, -29.8, 29.8, -29.8, 29.8]})  # weniger als 2 % besser
    assert (r["half_life"], r["growth_scale"]) == DEFAULT


def test_default_stays_when_it_is_best(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {m: [50] * 5 for m in [(None, 1.0), (1.0, 0.0)]}, default_errs=(5, -5, 5, -5, 5))
    assert (r["half_life"], r["growth_scale"]) == DEFAULT and r["mae"] == 5


def test_too_few_rows_uses_default(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {(None, 0.0): [1, 1, 1]}, default_errs=(40, 40, 40))
    assert (r["half_life"], r["growth_scale"]) == DEFAULT and not r["enough"]


def test_no_data_uses_default(login):
    _, uid = login("anna")
    with growlog.app.app_context():
        r = growlog.pick_method(uid, datetime.now(UTC))
    assert (r["half_life"], r["growth_scale"]) == DEFAULT and r["rows"] == 0 and r["mae"] is None


def test_result_is_cached_until_data_changes(login, monkeypatch):
    c, uid = login("anna")
    calls = []
    real = fake_runs({}, [1] * 5)

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(growlog, "backtest_methods", counting)
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


def test_assist_uses_chosen_method(login, monkeypatch):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    seen_hl, seen_growth = [], []
    real_fit, real_project = growlog.fit_model, growlog.project
    monkeypatch.setattr(growlog, "fit_model",
                        lambda rows, now, half_life=2.0: seen_hl.append(half_life) or real_fit(rows, now, half_life))
    monkeypatch.setattr(growlog, "project",
                        lambda cur, start, a, r, k, st, days=7: seen_growth.append(k) or real_project(cur, start, a, r, k, st, days))
    monkeypatch.setattr(growlog, "pick_method", lambda u, now: {"half_life": None, "growth_scale": 0.0})
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    for i in range(0, 6):
        c.post("/api/weigh", json={"ts": (base + timedelta(hours=12 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "values": {"AA": 3000 - 60 * i}})
    c.get("/api/state")
    assert seen_hl and set(seen_hl) == {None}
    assert seen_growth and set(seen_growth) == {0.0}


def test_real_comparison_runs_end_to_end(login):
    c, uid = login("anna")
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=8)
    w = 3300
    for i in range(16):
        c.post("/api/weigh", json={"ts": (base + timedelta(hours=12 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "values": {"AA": w}})
        w -= 55 + i  # Verbrauch steigt langsam
    with growlog.app.app_context():
        r = growlog.pick_method(uid, datetime.now(UTC))
    assert r["enough"] and r["rows"] >= growlog.METHOD_MIN_ROWS
    assert r["mae"] is not None and r["mae"] <= r["mae_default"]
    assert r["half_life"] in growlog.HALF_LIVES and r["growth_scale"] in growlog.GROWTH_SCALES


def test_insights_reports_method(login):
    c, _ = login("anna")
    d = c.get("/api/insights").get_json()
    assert d["method"]["enough"] is False and d["method"]["half_life"] == growlog.HALF_LIFE_DAYS
