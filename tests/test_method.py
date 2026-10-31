from datetime import datetime, timedelta, timezone

import pytest

import app as growlog

UTC = timezone.utc
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def row(days_ago, loss, lit, dark, hour=0):
    """Intervall-Zeile (Ende, Verlust g, Licht h, Dunkel h) -- Ende liegt `days_ago` Tage vor NOW."""
    return (NOW - timedelta(days=days_ago, hours=hour), float(loss), float(lit), float(dark))


# ---------------------------------------------------------------- einzelne Verfahren
def test_fit_model_average_vs_ema():
    old = [row(d, 100, 10, 14) for d in (8, 9)]   # früher hoher Verbrauch
    new = [row(d, 60, 10, 14) for d in (1, 2)]    # zuletzt niedriger
    avg = growlog.fit_model({"AA": old + new}, NOW, None)[0]["AA"]
    ema = growlog.fit_model({"AA": old + new}, NOW, 2.0)[0]["AA"]
    assert avg == pytest.approx(sum(x[1] for x in old + new) / 4 / 13.5, rel=0.02)
    assert ema < avg  # EMA folgt den neueren, niedrigeren Werten


def test_state_rates_solves_light_and_dark_exactly():
    # Licht 5 g/h, Dunkel 1 g/h; zwei Intervalle mit verschiedenem Lichtanteil
    rows = [row(2, 5 * 2 + 1 * 10, 2, 10), row(1, 5 * 10 + 1 * 2, 10, 2)]
    a, r = growlog.state_rates(rows, 0.25)
    assert a == pytest.approx(5.0) and r == pytest.approx(0.2)


def test_state_rates_falls_back_to_last_delta_with_one_state():
    rows = [row(2, 60, 10, 2), row(1, 66, 10, 2)]  # beide Intervalle gleicher Zustand
    a, r = growlog.state_rates(rows, 0.25)
    assert r == 0.25 and a == pytest.approx(66 / (10 + 0.25 * 2))


def test_estimate_last_delta_and_median():
    rows = [row(6 - i, loss, 10, 14) for i, loss in enumerate([60, 61, 90, 62, 63, 64])]
    last = growlog.estimate("last", {"AA": rows}, NOW)["AA"]
    med = growlog.estimate("median", {"AA": rows}, NOW)["AA"]
    r = last["r"]
    assert last["a"] == pytest.approx(64 / (10 + r * 14)) and last["growth"] == 0.0
    assert med["a"] == pytest.approx(63 / (10 + r * 14))  # Median der letzten 5 Verluste (61, 90, 62, 63, 64) = 63


def test_median_ignores_outlier():
    rows = [row(6 - i, loss, 10, 14) for i, loss in enumerate([60, 61, 200, 62, 63, 64])]
    avg = growlog.estimate("avg", {"AA": rows}, NOW)["AA"]["a"]
    med = growlog.estimate("median", {"AA": rows}, NOW)["AA"]["a"]
    assert med < avg


def test_profile_uses_same_clock_time_of_earlier_day():
    t1 = datetime(2026, 10, 26, 17, 30, tzinfo=UTC)
    rows = [(t1, 99.0, 5.5, 5.5)]  # 06:30–17:30 am Vortag, 11 h, 9 g/h
    prof = growlog.make_profile(rows)
    assert prof(datetime(2026, 10, 27, 10, 0, tzinfo=UTC), datetime(2026, 10, 27, 10, 15, tzinfo=UTC)) == pytest.approx(9.0)
    assert prof(datetime(2026, 10, 28, 12, 0, tzinfo=UTC), datetime(2026, 10, 28, 12, 15, tzinfo=UTC)) == pytest.approx(9.0)
    assert prof(datetime(2026, 10, 27, 20, 0, tzinfo=UTC), datetime(2026, 10, 27, 20, 15, tzinfo=UTC)) is None


def test_profile_prefers_newest_interval():
    old = (datetime(2026, 10, 25, 17, 30, tzinfo=UTC), 55.0, 5.5, 5.5)   # 5 g/h
    new = (datetime(2026, 10, 26, 17, 30, tzinfo=UTC), 99.0, 5.5, 5.5)   # 9 g/h
    prof = growlog.make_profile([old, new])
    t = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    assert prof(t, t + timedelta(minutes=15)) == pytest.approx(9.0)


def test_project_with_profile_uses_profile_rate():
    st = {"light_on": "17:00", "light_off": "11:00", "water_time": "17:30"}
    pts = growlog.project(3000, NOW, 99, 0.25, 0.0, st, days=1, profile=lambda t, t2: 10.0)
    assert growlog.weight_at(pts, NOW, NOW + timedelta(hours=2)) == pytest.approx(3000 - 20)


def test_project_profile_none_falls_back_to_rates():
    st = {"light_on": "17:00", "light_off": "11:00", "water_time": "17:30"}
    a = growlog.project(3000, NOW, 5, 0.25, 0.0, st, days=1)
    b = growlog.project(3000, NOW, 5, 0.25, 0.0, st, days=1, profile=lambda t, t2: None)
    assert [w for _, w in a] == [w for _, w in b]


# ---------------------------------------------------------------- Auswahl
def fake_runs(errs_by_algo, default_errs):
    def fake(u, now, n, algos):
        return {a: {"AA": [{"err": e} for e in errs_by_algo.get(a, default_errs)]} for a in algos}
    return fake


def pick(monkeypatch, uid, errs_by_algo, default_errs=(30, -30, 30, -30, 30)):
    monkeypatch.setattr(growlog, "backtest_algos", fake_runs(errs_by_algo, default_errs))
    with growlog.app.app_context():
        return growlog.pick_method(uid, datetime.now(UTC))


def test_most_accurate_algorithm_is_chosen(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {"state": [10, -10, 10, -10, 10], "last": [20, -20, 20, -20, 20]})
    assert r["algo"] == "state" and r["mae"] == 10 and r["enough"]
    assert [x["algo"] for x in r["ranking"]][:2] == ["state", "last"]
    assert len(r["ranking"]) == len(growlog.ALGOS)


@pytest.mark.parametrize("winner", ["avg", "last", "median", "state", "slot"])
def test_every_algorithm_can_win(login, monkeypatch, winner):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {winner: [5, -5, 5, -5, 5]})
    assert r["algo"] == winner and r["label"] == growlog.ALGO_LABELS[winner]


def test_tiny_difference_keeps_default(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {"slot": [29.8, -29.8, 29.8, -29.8, 29.8]})  # weniger als 2 % besser
    assert r["algo"] == growlog.DEFAULT_ALGO


def test_default_stays_when_it_is_best(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {a: [50] * 5 for a in growlog.ALGOS if a != "ema"}, default_errs=(5, -5, 5, -5, 5))
    assert r["algo"] == "ema" and r["mae"] == 5


def test_too_few_rows_uses_default(login, monkeypatch):
    _, uid = login("anna")
    r = pick(monkeypatch, uid, {"slot": [1, 1, 1]}, default_errs=(40, 40, 40))
    assert r["algo"] == growlog.DEFAULT_ALGO and not r["enough"]


def test_no_data_uses_default(login):
    _, uid = login("anna")
    with growlog.app.app_context():
        r = growlog.pick_method(uid, datetime.now(UTC))
    assert r["algo"] == growlog.DEFAULT_ALGO and r["rows"] == 0 and r["mae"] is None and r["ranking"] == []


def test_result_is_cached_until_data_changes(login, monkeypatch):
    c, uid = login("anna")
    calls = []
    real = fake_runs({}, [1] * 5)

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(growlog, "backtest_algos", counting)
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


# ---------------------------------------------------------------- Ende zu Ende
def seed_twice_daily(c, days=8, night=140, day=90):
    """Messungen morgens 06:30 und abends 17:30 (lokale Zeit), Verbrauch je Abschnitt verschieden."""
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    today = datetime.now(growlog.TZ).replace(hour=6, minute=30, second=0, microsecond=0)
    w = 3500
    for d in range(days, 0, -1):
        day0 = today - timedelta(days=d)
        for hour, minute, loss in ((6, 30, night), (17, 30, day)):
            ts = day0.replace(hour=hour, minute=minute).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            c.post("/api/weigh", json={"ts": ts, "values": {"AA": w}})
            w -= loss


@pytest.mark.parametrize("algo", growlog.ALGOS)
def test_every_algorithm_produces_a_forecast(login, algo):
    c, uid = login("anna")
    seed_twice_daily(c)
    with growlog.app.app_context():
        st, info, fits = growlog.model_state(uid, datetime.now(UTC), algo=algo)
    fit = fits["AA"]
    assert fit["a"] > 0 and 0.05 <= fit["r"] <= 0.6 and fit["growth"] >= 0
    assert (fit["profile"] is not None) == (algo == "slot")


def test_slot_profile_forecast_is_close_on_regular_pattern(login):
    c, uid = login("anna")
    seed_twice_daily(c)
    with growlog.app.app_context():
        runs = growlog.backtest_algos(uid, datetime.now(UTC), 8, ["slot"])
    errs = [abs(x["err"]) for x in runs["slot"]["AA"]]
    assert errs and max(errs) <= 5  # gleiches Muster jeden Tag: Tageszeit-Profil trifft fast exakt


def test_real_comparison_runs_end_to_end(login):
    c, uid = login("anna")
    seed_twice_daily(c)
    with growlog.app.app_context():
        r = growlog.pick_method(uid, datetime.now(UTC))
    assert r["enough"] and r["rows"] >= growlog.METHOD_MIN_ROWS
    assert {x["algo"] for x in r["ranking"]} == set(growlog.ALGOS)
    assert r["mae"] <= min(x["mae"] for x in r["ranking"]) + 1


def test_assist_uses_chosen_algorithm(login, monkeypatch):
    c, _ = login("anna")
    seed_twice_daily(c)
    seen = []
    real = growlog.project
    monkeypatch.setattr(growlog, "project",
                        lambda *a, **kw: seen.append(kw.get("profile")) or real(*a, **kw))
    monkeypatch.setattr(growlog, "pick_method", lambda u, now: {"algo": "slot"})
    c.get("/api/state")
    assert seen and all(p is not None for p in seen)
    seen.clear()
    monkeypatch.setattr(growlog, "pick_method", lambda u, now: {"algo": "last"})
    c.get("/api/state")
    assert seen and all(p is None for p in seen)


def test_insights_reports_method(login):
    c, _ = login("anna")
    d = c.get("/api/insights").get_json()
    assert d["method"]["enough"] is False and d["method"]["algo"] == growlog.DEFAULT_ALGO
