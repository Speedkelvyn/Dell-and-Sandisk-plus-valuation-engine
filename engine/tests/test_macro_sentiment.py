"""macro.py and sentiment.py, and how service.py uses them: readings, probabilities, weights, overlays, caching,
and a sentiment score that never touches the fair value."""

import datetime as dt
import gzip
import json
import math
import pathlib
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE))
import macro as M  # noqa: E402
import research as RP  # noqa: E402
import sentiment as SE  # noqa: E402
import service  # noqa: E402

TODAY = dt.date(2026, 11, 5)


@pytest.fixture(scope="module")
def data():
    return json.loads(gzip.decompress(service.EXAMPLE_MACRO.read_bytes()))


def fixture_feeds(t):
    return RP.from_fixture(json.loads(gzip.decompress((FIX / f"{t}_yahoo_2026-11-05.json.gz").read_bytes())))


# ------------------------------------------------------------------- macro
def test_fred_csv_skips_missing_observations():
    rows = M.parse_fred_csv("observation_date,T10Y3M\n2026-11-03,0.74\n2026-11-04,.\n2026-11-05,0.69\n")
    assert rows == [["2026-11-03", 0.74], ["2026-11-05", 0.69]]


def test_nyfed_probability_rises_as_the_curve_inverts():
    assert M.nyfed_probability(0.0) == pytest.approx(0.2969, abs=1e-3)  # Φ(−0.5333)
    assert M.nyfed_probability(-1.0) > M.nyfed_probability(0.0) > M.nyfed_probability(1.0)


def test_scenario_weights_are_neutral_at_the_long_run_probability_and_bounded():
    w = M.scenario_weights(M.NEUTRAL_P)
    assert w == pytest.approx({"Bear": 0.25, "Base": 0.50, "Bull": 0.25})
    for p in (0.02, 0.3, 0.6, 0.95):
        w = M.scenario_weights(p)
        assert sum(w.values()) == pytest.approx(1)
        assert 0.10 <= w["Bear"] <= 0.70 and 0.05 <= w["Bull"] <= 0.40
    assert M.scenario_weights(0.6)["Bear"] > M.scenario_weights(0.3)["Bear"]


def test_example_readings(data):
    s = M.summary(data)
    ind = s["indicators"]
    assert ind["curve"]["value"] > 0 and ind["sahm"]["value"] == 0.0
    assert ind["hy_spread"]["value"] == pytest.approx(3.32)
    assert ind["ai_trade"]["signal"] == "AI trade unwinding"
    assert s["regime"]["label"] == "Expansion, with market stress"
    assert 0.05 < s["regime"]["recession_prob"] < 0.25
    assert s["vol_scale"] == pytest.approx(23.47 / ind["vix"]["median5y"], rel=1e-3)


def test_a_triggered_sahm_rule_raises_the_probability(data):
    hot = json.loads(json.dumps(data))
    hot["fred"]["SAHMREALTIME"][-1][1] = 0.6
    p0 = M.summary(data)["regime"]["recession_prob"]
    p1 = M.summary(hot)["regime"]["recession_prob"]
    assert p1 == pytest.approx(min(0.95, p0 + 0.35))


def test_vol_scale_is_bounded():
    assert M.vol_scale({"vix": {"value": 80, "median5y": 17}}) == 1.75
    assert M.vol_scale({"vix": {"value": 9, "median5y": 17}}) == 0.75
    assert M.vol_scale({}) == 1.0


def test_fetch_records_failures_and_stops_asking_a_dead_source():
    calls = []

    def dead(*a):
        calls.append(a)
        raise OSError("blocked")

    out = M.fetch("2020-01-01", yahoo_chart=dead, fred_text=dead)
    assert not out["yahoo"] and not out["fred"]
    assert len(calls) == 6  # three tries per source, then skipped
    assert all(k in out["errors"] for k in (*M.YAHOO, *M.FRED))


def test_overlays_scale_with_exposure_and_the_margin_level():
    inp = service.load_published("sndk")
    assert M.overlay_overrides(inp, "Base", 0, 0) == {}
    ex = {"ai": 0.5, "china": 0.0, "industry": "x"}
    o = M.overlay_overrides(inp, "Base", 1.0, 0.0, ex)
    m_lr = max(v[1] for v in inp["scenarios"]["Base"]["margins"].values())
    assert o["all_growth_shift"] == pytest.approx(-0.10)
    assert o["margin_shift"] == pytest.approx(-0.25 * m_lr)
    up = M.overlay_overrides(inp, "Base", -0.5, 0.0, ex)
    assert up["all_growth_shift"] > 0 and up["margin_shift"] > 0


# --------------------------------------------------------------- sentiment
def test_headline_tone():
    items = [
        {"title": "Chipmaker beats estimates and raises guidance", "time": 1793880000},
        {"title": "Shares plunge after downgrade on weak demand", "time": 1793880000},
        {"title": "Company to present at conference", "time": 1793880000},
    ]
    n = SE.news(items, today=TODAY)
    tones = [h["tone"] for h in n["headlines"]]
    assert tones[0] > 0 and tones[1] < 0 and tones[2] == 0
    assert n["score"] == pytest.approx((1 + -1) / 2)


def test_old_headlines_are_ignored():
    assert SE.news([{"title": "Record quarter", "time": 1700000000}], today=TODAY) is None


def test_live_snapshot_sentiment():
    f = fixture_feeds("SNDK")
    s = SE.score(f["summary"], f["stock"], f["index"], f["news"], today=TODAY)
    c = s["components"]
    assert c["revisions"]["score"] > 0.5  # consensus EPS up 6-9% in 30 days
    assert c["trend"]["score"] < -0.5  # 50% down in a month
    assert c["news"]["score"] < 0
    lv = c["trend"]["levels"]
    assert lv["10-day low"] <= lv["20-day average"] <= lv["10-day high"]
    assert -100 <= s["score"] <= 100 and s["label"] in ("Negative", "Neutral")


def test_composite_reweights_over_available_components():
    summary = {
        "price": {"regularMarketPrice": 10},
        "defaultKeyStatistics": {"shortPercentOfFloat": {"raw": 0.25}},
    }
    s = SE.score(summary, today=TODAY)
    assert s["score"] == pytest.approx(100 * s["components"]["short"]["score"])  # the only component
    assert SE.score({"price": {}}, today=TODAY)["score"] is None


def test_setup_sentence_reads_value_and_sentiment_together():
    neg = {"score": -40, "components": {"revisions": {"score": -0.5}, "trend": {"score": -0.8}}}
    assert "value-trap" in SE.setup("UNDERVALUED", 0.4, neg)
    pos = {"score": 40, "components": {"revisions": {"score": 0.5}, "trend": {"score": 0.5}}}
    assert "agree" in SE.setup("UNDERVALUED", 0.4, pos)
    assert SE.setup("NOT VALUED", None, pos) == ""


def test_loughran_mcdonald_loader(tmp_path):
    p = tmp_path / "Loughran-McDonald_MasterDictionary_1993-2024.csv"
    p.write_text("Word,Negative,Positive\nLOSS,2009,0\nGAIN,0,2009\nCLOSED,-2020,0\n", encoding="utf-8")
    assert SE.find_lm(tmp_path) == p
    pos, neg = SE.load_lm(p)
    assert pos == {"gain"} and neg == {"loss"}  # a negative flag means the word was removed


# ----------------------------------------------------------------- service
def test_get_macro_caches_live_data_and_falls_back(tmp_path, data):
    live = service.get_macro(cache_dir=tmp_path, fetcher=lambda: data, today="2026-11-05")
    assert live["source"] == "live" and (tmp_path / "macro_2026-11-05.json").exists()
    again = service.get_macro(
        cache_dir=tmp_path, fetcher=lambda: pytest.fail("should use the cache"), today="2026-11-05"
    )
    assert again["source"] == "cache"
    stale = service.get_macro(
        cache_dir=tmp_path, fetcher=lambda: {"yahoo": {}, "fred": {}}, today="2026-11-06"
    )
    assert stale["source"] == "cache (2026-11-05)"
    ex = service.get_macro(
        cache_dir=tmp_path / "empty", fetcher=lambda: {"yahoo": {}, "fred": {}}, today="2026-11-06"
    )
    assert ex["source"].startswith("example")


def test_macro_weighted_dcf_and_monte_carlo_scale():
    m = service.example_macro()
    val = service.value_published("dell", mc=300, macro=m)
    s3, w = val.result["scen3"], m["weights"]
    assert val.macro_weighted == pytest.approx(sum(w[k] * s3[k]["perShare"] for k in w))
    assert val.inputs["monte_carlo"]["vol_scale"] == pytest.approx(m["vol_scale"])
    assert math.isnan(service.value_published("dell", mc=300).macro_weighted)
    assert any(line.startswith("Macro:") for line in service.reading(val))


def test_sentiment_never_changes_the_value():
    plain = service.value_published("dell", mc=300)
    inp = service.load_published("dell")
    inp["sentiment"] = {"score": -90.0, "label": "Very negative", "components": {}}
    gloomy = service.run(inp, "Base", 300)
    assert gloomy.fair_value == plain.fair_value and gloomy.sentiment["label"] == "Very negative"


def test_stress_table_orders_the_shocks():
    m = service.example_macro()
    val = service.value_published("sndk", mc=300, macro=m)
    t = service.stress_table(val).set_index("Scenario")["Value per share"]
    base = t["Base case"]
    assert t["Both"] < t["AI data-center spending slows"] < base < t["AI spending accelerates"]
    assert t["Chinese competition intensifies"] < base


def test_overlay_assumptions_are_sliders_and_move_the_value():
    val = service.value_published("sndk", mc=300)
    keys = {s["key"] for s in service.default_assumptions(val)}
    assert {"ai_shock", "china_shock", "ai_exposure", "china_exposure"} <= keys
    hit = service.run(val.base_inputs, "Base", 300, {"ai_shock": 1.0})
    assert hit.result["base"]["perShare"] < val.result["base"]["perShare"]


def test_live_build_attaches_sentiment_and_a_build_out_is_reported(tmp_path):
    vals = service.value_many(
        ["NBIS", "ON"], mc=300, cache_dir=tmp_path, fetcher=lambda t, p=None: fixture_feeds(t), refresh=True
    )
    for v_ in vals.values():
        assert v_.sentiment["score"] is not None and not v_.sentiment.get("partial")
    assert any("Build-out company" in x for x in service.reading(vals["NBIS"]))
    row = service.summary(vals["ON"])
    assert row["Sentiment label"] and math.isfinite(row["Sentiment"])


def test_not_valued_status_when_every_method_fails(tmp_path):
    inp = RP.build(fixture_feeds("NBIS"))
    for sc in inp["scenarios"].values():
        sc["margins"]["total"][1] = -0.3  # a perpetual loss: no method can give a positive value
    val = service.run(inp, "Base", 300)
    assert val.ok and not val.valued and val.status == "Not valued"
    assert val.note.startswith("No usable value")
    assert service.reading(val)[0].startswith("No usable value")
