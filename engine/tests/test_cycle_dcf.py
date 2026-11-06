import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cycle_dcf as cd

# A published worked example (Chipotle, TTM Aug-2023): three 6-year growth cycles, annual end-year discounting.
# The source notebook printed: operating assets 39.96, cash 1.72, debt 3.89, firm 41.68, equity 37.79 (USD bn).
CHIPOTLE = {
    "base_revenue": 9.284,
    "cycles": [
        {"length": 6, "g0": 0.11, "g1": 0.16},
        {"length": 6, "g0": 0.14, "g1": 0.09},
        {"length": 6, "g0": 0.08, "g1": 0.04},
    ],
    "margin": {"start": 0.15, "end": 0.175},
    "tax": {"start": 0.24, "end": 0.25},
    "mtr": 0.25,
    "sales_to_capital": {"start": 1.7, "end": 1.3},
    "invested_capital": 6.062,
    "equity": 51.016,
    "debt": 3.887,
    "beta": {"start": 0.9, "end": 0.9},
    "kd": {"start": 0.06, "end": 0.055, "hold": 3},
    "rf": 0.04,
    "erp": 0.048,
    "excess": 0.02,
    "cash": 1.722,
    "shares": 0.0275898,
}


def test_reproduces_published_worked_example():
    v = cd.value(CHIPOTLE)
    assert v["ev"] == pytest.approx(39.96, abs=0.01)
    assert v["ev"] + 1.722 == pytest.approx(41.68, abs=0.01)
    assert v["equity"] == pytest.approx(37.79, abs=0.01)
    assert v["n"] == 18


def test_glide_holds_then_moves_in_a_straight_line():
    assert list(cd.glide(0.10, 0.04, 4)) == pytest.approx([0.10, 0.08, 0.06, 0.04])
    assert list(cd.glide(1.0, 3.0, 4, hold=3)) == pytest.approx([1.0, 1.0, 1.0, 3.0])
    assert list(cd.glide(0.2, 0.0, 1)) == [0.2]


def test_knot_path_goes_through_turning_points_and_stays_flat_outside():
    p = cd.knot_path([(2, 0.8), (4, 0.2)], 6)
    assert list(p) == pytest.approx([0.8, 0.8, 0.5, 0.2, 0.2, 0.2])


def test_discount_factor_compounds_yearly_rates():
    spec = {**CHIPOTLE, "cycles": [{"length": 2, "g0": 0.0, "g1": 0.0}], "excess": 0.0}
    spec["cycles"][0]["g1"] = 0.0
    v = cd.value(spec)
    cum = np.cumprod(1 + v["wacc"])
    assert v["df"] == pytest.approx(1 / cum)


def test_stub_and_mid_year_pull_cash_flows_forward():
    base = cd.value(CHIPOTLE)["equity"]
    mid = cd.value({**CHIPOTLE, "timing": {"first_period": 1.0, "stub": 1.0, "mid_year": True}})["equity"]
    assert mid > base
    part = cd.value({**CHIPOTLE, "timing": {"first_period": 0.5, "stub": 0.5, "mid_year": True}})
    assert part["df"][0] == pytest.approx(1 / (1 + part["wacc"][0]) ** 0.25)


def test_terminal_growth_must_stay_below_the_discount_rate():
    bad = {**CHIPOTLE, "cycles": [{"length": 3, "g0": 0.05, "g1": 0.20}]}
    with pytest.raises(ValueError):
        cd.value(bad)


def test_revenue_decline_releases_nothing_by_default_and_something_with_liquidation():
    cyc = [{"length": 2, "g0": -0.2, "g1": -0.1}, {"length": 2, "g0": 0.02, "g1": 0.02}]
    none = cd.value({**CHIPOTLE, "cycles": cyc})
    some = cd.value({**CHIPOTLE, "cycles": cyc, "liquidation": 0.5})
    assert (none["reinvestment"][:2] == 0).all()
    assert some["equity"] > none["equity"]


def test_draw_respects_marginals_and_correlation():
    dists = {"a": ("tri", 0.0, 1.0, 4.0), "b": ("uni", 10.0, 20.0), "c": ("norm", 5.0, 2.0)}
    s = cd.draw(dists, [("a", "b", 0.8)], 20000, seed=3)
    assert s["a"].min() >= 0 and s["a"].max() <= 4
    assert s["b"].min() >= 10 and s["b"].max() <= 20
    assert s["c"].mean() == pytest.approx(5.0, abs=0.1) and s["c"].std() == pytest.approx(2.0, abs=0.1)
    assert np.corrcoef(s["a"], s["b"])[0, 1] > 0.6
    assert abs(np.corrcoef(s["a"], s["c"])[0, 1]) < 0.05


def test_inconsistent_correlations_are_repaired_not_rejected():
    dists = dict.fromkeys("abc", ("uni", 0.0, 1.0))
    s = cd.draw(dists, [("a", "b", 0.95), ("b", "c", 0.95), ("a", "c", -0.95)], 200, seed=1)
    assert all(len(v) == 200 for v in s.values())


def test_monte_carlo_is_seeded_and_ranks_drivers():
    def build(x):
        return {**CHIPOTLE, "margin": {"start": 0.15, "end": x["m"]}, "rf": x["rf"]}

    dists = {"m": ("tri", 0.12, 0.175, 0.22), "rf": ("norm", 0.04, 0.001)}
    a = cd.monte_carlo(build, dists, [], size=300, seed=5)
    b = cd.monte_carlo(build, dists, [], size=300, seed=5)
    assert (a["equity"] == b["equity"]).all()
    assert cd.drivers(a["inputs"], a["equity"])[0][0] == "m"
    tab = cd.percentile_table(a["equity"], 51.016, 0.0275898)
    assert [r["pct"] for r in tab] == list(range(0, 101, 10))
    assert tab[0]["equity"] <= tab[5]["equity"] <= tab[-1]["equity"]


def test_monte_carlo_drops_draws_that_cannot_be_valued():
    def build(x):
        return {**CHIPOTLE, "cycles": [{"length": 3, "g0": 0.05, "g1": x["g"]}]}

    r = cd.monte_carlo(build, {"g": ("uni", 0.0, 0.2)}, [], size=200, seed=2)
    assert r["dropped"] > 0 and len(r["equity"]) == 200 - r["dropped"]
