"""Point-in-time backtest: the as-of filters, price handling, statistics, and the engine paths it exposed
(no peers, no consensus EPS, no cash line, no operating income tag)."""

import math
import pathlib
import sys

import numpy as np
import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
DATA = ENGINE.parent / "backtest" / "data"
sys.path.insert(0, str(ENGINE))
import edgar  # noqa: E402

import backtest as B  # noqa: E402
import research as RP  # noqa: E402
import valuation_v2 as v  # noqa: E402


def prices(rows, splits=()):
    return B.Prices({"rows": rows, "splits": list(splits)})


def test_a_filing_made_after_the_as_of_date_is_invisible():
    rows = [
        ["2022-01-01", "2022-12-31", 100.0, "FY", "10-K", "2023-02-10"],
        ["2022-01-01", "2022-12-31", 90.0, "FY", "10-K/A", "2023-09-01"],  # restated later
    ]
    facts = {"cik": 1, "name": "T", "facts": {"Revenues": {"USD": rows}}}
    assert B.point_in_time(facts, "2023-03-01")["facts"]["Revenues"]["USD"] == [rows[0]]
    assert len(B.point_in_time(facts, "2023-12-31")["facts"]["Revenues"]["USD"]) == 2
    assert "Revenues" not in B.point_in_time(facts, "2023-01-31")["facts"]


def test_quoted_price_undoes_later_splits_only():
    px = prices(
        [["2020-08-27", 125.0, 120.0], ["2020-08-28", 126.0, 121.0], ["2020-08-31", 129.0, 124.0]],
        splits=[["2020-08-31", 4, 1]],
    )
    # Yahoo shows pre-split days divided by the split ratio; what a screen showed then was four times that
    assert px.quoted("2020-08-28") == pytest.approx(504.0)
    assert px.quoted("2020-08-31") == pytest.approx(129.0)  # on the split day the price is already post-split


def test_forward_return_is_none_without_a_full_window_and_uses_adjusted_prices():
    px = prices([["2024-01-02", 100.0, 100.0], ["2024-12-31", 110.0, 121.0], ["2025-06-02", 90.0, 90.0]])
    assert px.forward_return("2024-01-02", 12) == pytest.approx(0.21)  # dividends included via adj close
    assert px.forward_return("2024-12-31", 12) is None


def test_quarter_ends_and_rank_statistics():
    assert B.quarter_ends("2024-02-01", "2024-12-31") == [
        "2024-03-31",
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
    ]
    assert B.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert B.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(B.spearman([1, 1, 1], [1, 2, 3]))
    assert list(B.rank([5, 5, 1])) == [1.5, 1.5, 0.0]


def test_summary_finds_a_planted_signal_and_reports_none_for_noise():
    rng = np.random.default_rng(1)
    recs = []
    for d in ("2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31"):
        for i in range(30):
            up = float(rng.normal(0, 0.3))
            recs.append(
                {
                    "ticker": f"T{i}", "asof": d, "model": "fcff", "upside": up, "call": "X", "fair": 1.0, "price": 1.0,
                    "excess12": up * 0.5 + float(rng.normal(0, 0.05)), "ret12": 0.1, "prob_above": 0.5,
                    "base_ey": None, "base_bp": None, "base_mom": None,
                }
            )  # fmt: skip
    assert B.summarize(recs, 12)["ic_pooled"] > 0.8
    for r in recs:
        r["excess12"] = float(rng.normal(0, 0.2))
    assert abs(B.summarize(recs, 12)["ic_pooled"]) < 0.3


@pytest.mark.skipif(not DATA.exists(), reason="backtest data not present")
def test_one_valuation_uses_only_what_existed_that_day():
    idx, tnx = B.Prices(B._read(DATA / "GSPC_px.json.gz")), B.Prices(B._read(DATA / "TNX_px.json.gz"))
    facts, px = B._read(DATA / "AAPL_edgar.json.gz"), B.Prices(B._read(DATA / "AAPL_px.json.gz"))
    f = B.feeds_at("AAPL", facts, px, idx, tnx, "2022-06-30")
    assert f["summary"]["price"]["regularMarketPrice"] == pytest.approx(
        136.72, abs=0.01
    )  # quoted close that day
    assert f["stock"][-1][0] == "2022-06-30" and f["index"][-1][0] == "2022-06-30"
    rev = f["annual"]["TotalRevenue"]
    assert rev[-1][0] == "2021-09-30"  # fiscal 2022 had not been filed yet
    rec = B.value_at("AAPL", facts, px, idx, tnx, "2022-06-30", mc_n=100)
    assert "error" not in rec and rec["fair"] > 0 and rec["excess12"] is not None


def test_regression_without_peers_is_left_out_instead_of_crashing():
    inp = RP.build(_feeds_without_peers())
    out = v.regression(inp, 0.1)
    assert out["n"] < 3 and math.isnan(out["implied"])


def test_pvgo_without_consensus_eps_is_nan():
    inp = RP.build(_feeds_without_peers())
    inp["market"]["eps_ntm"] = None
    assert all(math.isnan(x) for x in v.pvgo(inp).values())


def test_operating_income_is_built_from_pretax_income_when_never_tagged():
    out = {"PretaxIncome": [["2023-12-31", 90.0]], "InterestExpense": [["2023-12-31", 10.0]]}
    edgar._operating_fallback(out)
    assert out["OperatingIncome"] == [["2023-12-31", 100.0]]
    keep = {"OperatingIncome": [["2023-12-31", 5.0]], "PretaxIncome": [["2023-12-31", 90.0]]}
    edgar._operating_fallback(keep)
    assert keep["OperatingIncome"] == [["2023-12-31", 5.0]]


def _feeds_without_peers():
    import gzip
    import json

    fx = pathlib.Path(__file__).resolve().parent / "fixtures" / "ON_yahoo_2026-11-05.json.gz"
    f = RP.from_fixture(json.loads(gzip.decompress(fx.read_bytes())))
    f["peers"] = {}
    return f


def test_build_survives_a_zero_revenue_year():
    f = _feeds_without_peers()
    rev = f["annual"]["TotalRevenue"]
    rev[0] = [rev[0][0], 0.0]
    inp = RP.build(f)
    assert inp["scenarios"]["Base"]["margins"]["total"]


def test_band_coverage_counts_only_named_tickers_with_an_outcome():
    recs = [
        {"ticker": "A", "mc_p10": 1, "mc_p90": 3, "later_price12": 2},
        {"ticker": "A", "mc_p10": 1, "mc_p90": 3, "later_price12": 5},
        {"ticker": "A", "error": "x"},
        {"ticker": "B", "mc_p10": 1, "mc_p90": 3, "later_price12": 2},
        {"ticker": "A", "mc_p10": 1, "mc_p90": 3, "later_price12": None},
    ]
    assert B.band_coverage(recs, {"A"}) == (2, 0.5)
    assert B.band_coverage(recs, {"Z"})[0] == 0


@pytest.mark.skipif(not (DATA / "GS_edgar.json.gz").exists(), reason="backtest data not present")
@pytest.mark.parametrize(("t", "asof"), [("GS", "2020-06-30"), ("MS", "2020-06-30"), ("CI", "2020-06-30")])
def test_filers_that_used_to_crash_now_build_and_value(t, asof):
    """Goldman Sachs and Morgan Stanley tag no revenue line; Cigna filed a share count of zero for 2016-17. Each
    crashed the build (fromisoformat on None, division by zero) and dropped out of the backtest."""
    facts, px = B._read(DATA / f"{t}_edgar.json.gz"), B.Prices(B._read(DATA / f"{t}_px.json.gz"))
    idx, tnx = B.Prices(B._read(DATA / "GSPC_px.json.gz")), B.Prices(B._read(DATA / "TNX_px.json.gz"))
    inp = RP.build(B.feeds_at(t, facts, px, idx, tnx, asof))
    out = v.run_all(inp, "Base", mc_n=50)
    assert not out["checks"].get("blocking")


def test_engine_tolerates_a_missing_capex_fade_and_missing_debt_lines():
    assert v._debt({"bridge": {"core_debt": None, "leases": None}}) == 0.0
    assert v._debt({"bridge": {"core_debt": 5.0, "leases": 1.5}}) == 6.5


def test_without_consensus_next_years_growth_is_pulled_toward_the_median_not_carried_forward():
    """AAPL grew 33% in FY21; with no analyst estimates the engine should expect 6.5% + 25% of the gap, not 33%."""
    d = DATA
    if not (d / "AAPL_edgar.json.gz").exists():
        pytest.skip("backtest data not present")
    facts = B._read(d / "AAPL_edgar.json.gz")
    px, idx, tnx = (B.Prices(B._read(d / f"{n}_px.json.gz")) for n in ("AAPL", "GSPC", "TNX"))
    f = B.feeds_at("AAPL", facts, px, idx, tnx, "2022-06-30")
    inp = RP.build(f)
    rows = f["annual"]["TotalRevenue"]
    g = rows[-1][1] / rows[-2][1] - 1
    expected = 0.065 + 0.25 * (g - 0.065)
    assert inp["y1_segments"]["total"] / inp["rev_y0"] - 1 == pytest.approx(expected, rel=1e-6)
    assert expected < g / 2
