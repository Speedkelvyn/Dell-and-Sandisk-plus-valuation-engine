"""service.py: the engine as a plain API. Valuations must match the engine, assumptions must move the value
the right way, and one failing ticker must never stop a watchlist."""

import copy
import json
import math
import pathlib
import sys

import pandas as pd
import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import run_valuation  # noqa: E402
import service  # noqa: E402
import valuation_v2 as v  # noqa: E402

MC = 600  # enough draws for the tests, which compare like with like


@pytest.fixture(scope="module")
def dell():
    return service.value_published("dell", mc=MC)


@pytest.fixture(scope="module")
def sndk():
    return service.value_published("sndk", mc=MC)


def fake_feeds(ticker, peers=None):
    """What fetch_yahoo.fetch returns, with empty feeds: the fake builder below ignores them."""
    return {
        "ticker": ticker, "today": "2026-10-21", "tnx": 4.0, "summary": {}, "annual": {}, "quarterly": {},
        "stock": [], "index": [], "peers": {},
    }  # fmt: skip


def builder_of(inp):
    def build(feeds):
        out = copy.deepcopy(inp)
        out["meta"]["ticker"] = feeds["ticker"]
        return out

    return build


# ------------------------------------------------------------------ tickers
def test_parse_tickers_splits_cleans_and_drops_repeats():
    good, bad = service.parse_tickers("aapl, MSFT  nvda;aapl\nBRK-B ticker!! $$ x" + "y" * 12)
    assert good == ["AAPL", "MSFT", "NVDA", "BRK-B"]
    assert bad == ["ticker!!", "$$", "x" + "y" * 12]
    assert service.parse_tickers("") == ([], [])


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", "x y", ".hidden", "TOOLONGSYMBOL1"])
def test_unsafe_tickers_are_rejected(bad):
    with pytest.raises(ValueError):
        service.clean_ticker(bad)


# -------------------------------------------------------------- the engine
def test_service_matches_the_engine_exactly(dell):
    inp = run_valuation.load("dell")
    direct = v.run_all(inp, "Base", mc_n=MC)
    assert dell.ok and dell.status == "Valued"
    assert dell.fair_value == direct["verdict"]["fair"]
    assert dell.verdict["call"] == direct["verdict"]["call"] == "OVERVALUED"
    assert dell.result["scen3"]["Base"]["perShare"] == direct["scen3"]["Base"]["perShare"]


def test_published_valuations_keep_their_calls(dell, sndk):
    assert dell.verdict["call"] == "OVERVALUED" and dell.upside < -0.3
    assert sndk.verdict["call"] == "FAIRLY VALUED" and sndk.verdict["confidence"] == "low"


def test_unknown_scenario_is_reported_not_raised():
    val = service.run(run_valuation.load("dell"), "Sideways", MC)
    assert not val.ok and val.status == "Error" and "Sideways" in val.error


# --------------------------------------------------------------- assumptions
def test_assumptions_start_at_the_models_own_values(dell):
    items = {a["key"]: a for a in service.default_assumptions(dell)}
    inp = run_valuation.load("dell")
    assert items["g"]["value"] == inp["terminal"]["g"]
    assert items["capex_pct"]["value"] == inp["operating"]["capex_pct"]
    assert items["beta"]["value"] == pytest.approx(dell.result["W"]["beta"]["chosen"])
    assert items["erp"]["value"] == pytest.approx(inp["capital"]["rm"] - inp["capital"]["rf"])
    assert items["all_growth_shift"]["value"] == 0 and items["margin_shift"]["value"] == 0


def test_overrides_do_not_touch_the_original_inputs():
    inp = run_valuation.load("dell")
    before = json.dumps(inp, sort_keys=True)
    service.run(inp, "Base", MC, overrides={"g": 0.02, "beta": 1.1, "all_growth_shift": -0.02, "rf": 0.04})
    assert json.dumps(inp, sort_keys=True) == before


@pytest.mark.parametrize(
    ("override", "direction"),
    [
        ({"beta": 2.4}, -1),  # a riskier stock is worth less
        ({"beta": 1.0}, +1),
        ({"erp": 0.06}, -1),
        ({"rf": 0.07}, -1),
        ({"g": 0.04}, +1),
        ({"all_growth_shift": -0.03}, -1),
        ({"all_growth_shift": 0.03}, +1),
        ({"margin_shift": -0.03}, -1),
        ({"capex_pct": 0.06}, -1),
        ({"nwc_pct": 0.05}, -1),  # the model starts at -7%: moving up ties up cash
    ],
)
def test_each_assumption_moves_the_value_the_right_way(dell, override, direction):
    changed = service.run(dell.base_inputs, "Base", MC, overrides=override)
    assert changed.ok
    delta = changed.result["scen3"]["Base"]["perShare"] - dell.result["scen3"]["Base"]["perShare"]
    assert delta * direction > 0, (override, delta)


def test_scenario_shifts_move_bear_base_and_bull_together(dell):
    changed = service.run(dell.base_inputs, "Base", MC, overrides={"all_growth_shift": -0.03})
    s = changed.result["scen3"]
    assert s["Bear"]["perShare"] < s["Base"]["perShare"] < s["Bull"]["perShare"]
    for k in ("Bear", "Base", "Bull"):
        assert s[k]["perShare"] < dell.result["scen3"][k]["perShare"]


def test_risk_free_change_keeps_the_equity_premium(dell):
    inp = service.apply_overrides(dell.base_inputs, {"rf": 0.07})
    cap = inp["capital"]
    assert cap["rf"] == 0.07
    assert cap["rm"] - cap["rf"] == pytest.approx(
        dell.base_inputs["capital"]["rm"] - dell.base_inputs["capital"]["rf"]
    )


def test_an_impossible_assumption_is_explained_not_crashed(dell):
    val = service.run(dell.base_inputs, "Base", MC, overrides={"g": 0.30})
    assert not val.ok and val.fair_value != val.fair_value  # NaN
    assert "growth" in val.note.lower()
    assert any(n["level"] == "red" for n in service.data_notes(val))


# ------------------------------------------------------------- batch and data
def test_one_failing_ticker_never_stops_the_watchlist(tmp_path):
    inp = run_valuation.load("dell")

    def fetcher(t, peers=None):
        if t == "BAD":
            raise SystemExit(f"Yahoo returned nothing for {t}")
        if t == "BOOM":
            raise ConnectionError("network down")
        return fake_feeds(t, peers)

    calls = []
    out = service.value_many(
        ["DELL", "BAD", "BOOM", "$$"], mc=MC, cache_dir=tmp_path, fetcher=fetcher, builder=builder_of(inp),
        progress=lambda i, n, t: calls.append((i, n, t)),
    )  # fmt: skip
    assert out["DELL"].ok
    assert out["BAD"].status == "Error" and "nothing for BAD" in out["BAD"].error
    assert out["BOOM"].status == "Error" and "network down" in out["BOOM"].error
    assert out["$$"].status == "Error" and "not a ticker" in out["$$"].error
    assert calls[0] == (0, 4, "DELL") and calls[-1] == (4, 4, "")
    assert list(out) == ["DELL", "BAD", "BOOM", "$$"]


def test_feeds_are_cached_for_the_day_and_refresh_forces_a_new_pull(tmp_path):
    calls = []

    def fetcher(t, peers=None):
        calls.append(t)
        return fake_feeds(t, peers)

    feeds, src = service.get_feeds("dell", cache_dir=tmp_path, fetcher=fetcher, today="2026-10-21")
    assert src == "yahoo" and calls == ["DELL"] and feeds["ticker"] == "DELL"
    assert (tmp_path / "DELL_yahoo_2026-10-21.json").exists()
    feeds2, src2 = service.get_feeds("DELL", cache_dir=tmp_path, fetcher=fetcher, today="2026-10-21")
    assert src2 == "cache" and calls == ["DELL"] and feeds2["ticker"] == "DELL"
    _, src3 = service.get_feeds("DELL", cache_dir=tmp_path, fetcher=fetcher, today="2026-10-21", refresh=True)
    assert src3 == "yahoo" and calls == ["DELL", "DELL"]
    _, src4 = service.get_feeds(
        "DELL", ["HPQ", "HPE"], cache_dir=tmp_path, fetcher=fetcher, today="2026-10-21"
    )
    assert src4 == "yahoo"  # a different peer list is a different pull


def test_a_missing_yfinance_is_a_clear_error_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fetch_yahoo", None)  # import fails like an uninstalled yfinance
    val = service.value_ticker("AAPL", cache_dir=tmp_path)
    assert val.status == "Error" and "AAPL" in val.error


def test_inputs_and_snapshots_load_from_uploaded_text():
    inp = run_valuation.load("dell")
    assert service.inputs_from_text(json.dumps(inp))["meta"]["ticker"] == "DELL"
    with pytest.raises(service.DataError):
        service.inputs_from_text("not json")
    with pytest.raises(service.DataError):
        service.inputs_from_text(json.dumps({"hello": 1}))


# ----------------------------------------------------------------- the tables
def test_summary_table_ranks_by_upside_and_keeps_failures_last(dell, sndk):
    bad = service.Valuation(ticker="BAD", scenario="Base", error="Yahoo returned nothing for BAD")
    df = service.summary_table({"BAD": bad, "DELL": dell, "SNDK": sndk})
    assert list(df["Ticker"]) == ["SNDK", "DELL", "BAD"]  # -3.7% beats -34%; the failure is last
    assert list(df.columns) == service.SUMMARY_COLUMNS
    assert df.loc[df.Ticker == "BAD", "Status"].item() == "Error"
    assert df.loc[df.Ticker == "DELL", "Fair value"].item() == dell.fair_value
    assert df.loc[df.Ticker == "DELL", "Street target"].item() == 593.0


def test_football_field_ranges_are_ordered_and_finite(dell, sndk):
    for val in (dell, sndk):
        ff = service.football(val)
        assert {"DCF, Bear to Bull", "Monte Carlo, 10th to 90th percentile", "52-week trading range"} <= set(
            ff.Method
        )
        assert (ff.Low <= ff.Mid + 1e-9).all() and (ff.Mid <= ff.High + 1e-9).all()
        assert ff[["Low", "Mid", "High"]].notna().all().all()


def test_forecast_table_shows_the_ten_years(dell):
    t = service.forecast_table(dell)
    assert t.shape[1] == 10 and {"Revenue", "Free cash flow", "Operating margin"} <= set(t.index)
    assert t.loc["Revenue"].iloc[0] == dell.result["base"]["rows"][0]["rev"]


def test_whats_priced_in_solves_back_to_todays_price(dell):
    t = service.whats_priced_in(dell).set_index("Assumption")
    inp, scen = dell.inputs, dell.inputs["scenarios"]["Base"]
    for knob, label in (
        ("all_growth_shift", "Revenue growth, shift per year"),
        ("wacc", "Discount rate (WACC)"),
    ):
        needed = t.loc[label, "Needed for today's price"]
        assert math.isfinite(needed)
        assert v.value_at(inp, scen, knob, needed) == pytest.approx(inp["market"]["price"], abs=1e-3)
    # Dell is overvalued here, so today's price needs a lower discount rate and faster growth than the model's
    assert t.loc["Discount rate (WACC)", "Needed for today's price"] < t.loc["Discount rate (WACC)", "Model"]
    assert t.loc["Revenue growth, shift per year", "Needed for today's price"] > 0


def test_sensitivity_grid_is_centred_on_the_base_value(dell):
    g = service.sensitivity_grid(dell, "wacc_g")
    assert g.shape == (7, 7)
    assert g.iloc[3, 3] == pytest.approx(dell.result["base"]["perShare"])
    assert (g.diff(axis=0).dropna() < 0).all().all()  # higher discount rate, lower value
    g2 = service.sensitivity_grid(dell, "growth_margin")
    assert g2.iloc[3, 3] == pytest.approx(dell.result["base"]["perShare"])


# ----------------------------------------------------------- local files, exports
def test_watchlist_and_holdings_round_trip(tmp_path):
    assert service.load_watchlist(tmp_path) == []
    service.save_watchlist(["aapl", "msft", "AAPL", "bad ticker!"], tmp_path)
    assert service.load_watchlist(tmp_path) == ["AAPL", "MSFT", "BAD"]
    h = pd.DataFrame(
        {
            "Ticker": ["dell", "DELL", "sndk", "??", "x"],
            "Shares": [10, 5, 2, 1, 0],
            "Cost per share": [100.0, 200.0, None, 1.0, 1.0],
        }
    )
    service.save_holdings(h, tmp_path)
    back = service.load_holdings(tmp_path)
    assert list(back.Ticker) == ["DELL", "SNDK"]
    assert back.loc[back.Ticker == "DELL", "Shares"].item() == 15
    assert back.loc[back.Ticker == "DELL", "Cost per share"].item() == pytest.approx(
        (10 * 100 + 5 * 200) / 15
    )
    assert math.isnan(back.loc[back.Ticker == "SNDK", "Cost per share"].item())


def test_portfolio_totals(dell, sndk):
    h = pd.DataFrame(
        {"Ticker": ["DELL", "SNDK", "ZZZ"], "Shares": [10, 2, 7], "Cost per share": [400.0, 1000.0, 5.0]}
    )
    p = service.portfolio(h, {"DELL": dell, "SNDK": sndk})
    mv_dell, mv_sndk = 10 * dell.price, 2 * sndk.price
    assert p.set_index("Ticker").loc["DELL", "Market value"] == pytest.approx(mv_dell)
    assert p.Weight.sum() == pytest.approx(1.0)
    t = service.portfolio_totals(p)
    assert t["positions"] == 3 and t["valued"] == 2
    fv = 10 * dell.fair_value + 2 * sndk.fair_value
    assert t["fair_value"] == pytest.approx(fv)
    assert t["upside"] == pytest.approx(fv / (mv_dell + mv_sndk) - 1)
    assert math.isnan(p.set_index("Ticker").loc["ZZZ", "Fair value"])  # not valued, still listed


def test_exports_are_real_files(dell):
    xlsx = service.workbook_bytes(dell)
    assert xlsx[:2] == b"PK" and len(xlsx) > 20_000
    page = service.page_html(dell)
    assert 'id="valuation-data"' in page and "Dell Technologies" in page
    assert "function monteCarlo" in page and "web/model.js" not in page  # the engine is inlined: one file
    assert json.loads(service.inputs_json(dell))["meta"]["ticker"] == "DELL"


def test_reading_states_the_numbers_the_model_produced(dell, sndk):
    d = " ".join(service.reading(dell))
    assert f"${dell.fair_value:,.2f}" in d and "above even the Bull case" in d and "0%" in d
    assert "mean analyst target is $593.00" in d and "Earnings quality grade B" in d
    s = " ".join(service.reading(sndk))
    assert "inside the DCF range" in s and "Earnings quality grade C" in s
    assert service.reading(service.Valuation(ticker="X", scenario="Base", error="boom")) == []
