"""value_sndk_cycle.py: the SNDK scenarios build, tie to the Street and stay ordered."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import value_sndk_cycle as vs

import cycle_dcf as cd

INP = json.loads((pathlib.Path(__file__).resolve().parents[2] / "sndk" / "SNDK_inputs.json").read_text())


def test_scenarios_are_ordered_and_valid():
    v = {k: cd.value(vs.build(x, INP))["per_share"] for k, x in vs.SCENARIOS.items()}
    assert v["Bear"] < v["Base"] < v["Bull"]


def test_base_year_one_and_two_tie_to_consensus():
    b = cd.value(vs.build(vs.SCENARIOS["Base"], INP))
    sh = INP["market"]["shares_basic"] + INP["market"]["dilution"]
    eps = INP["consensus"]["eps"]
    assert b["nopat"][0] / sh == pytest.approx(eps["FY27"], rel=0.05)
    assert b["nopat"][1] / sh == pytest.approx(eps["FY28"], rel=0.08)
    assert b["revenue"][0] == pytest.approx(INP["consensus"]["revenue"]["FY27"], rel=0.005)
    assert b["revenue"][1] == pytest.approx(INP["consensus"]["revenue"]["FY28"], rel=0.005)


def test_horizon_follows_boom_and_downturn_length():
    for lb, xa in ((3, 0), (4, 1), (6, 2)):
        v = cd.value(vs.build({**vs.SCENARIOS["Base"], "LB": lb, "extA": xa}, INP))
        assert v["n"] == 2 + xa + lb + 8


def test_a_longer_boom_is_worth_more():
    base = vs.SCENARIOS["Base"]
    a = cd.value(vs.build(base, INP))["per_share"]
    b = cd.value(vs.build({**base, "extA": 2}, INP))["per_share"]
    assert b > a


def test_implied_mid_cycle_margin_reprices_to_the_market():
    base = vs.SCENARIOS["Base"]
    m = vs.implied_mid_cycle_margin(INP, base, INP["market"]["price"])
    assert m is not None
    got = cd.value(vs.build({**base, "mid": m}, INP))["per_share"]
    assert got == pytest.approx(INP["market"]["price"], rel=1e-4)
    assert vs.implied_mid_cycle_margin(INP, base, 1e9) is None


def test_run_is_seeded_and_percentiles_are_ordered():
    a = vs.run(INP, size=400, seed=3)
    b = vs.run(INP, size=400, seed=3)
    assert a["percentiles"] == b["percentiles"]
    ps = [r["per_share"] for r in a["percentiles"]]
    assert ps == sorted(ps)
    assert 0 <= a["p_above_price"] <= 1
    assert json.dumps(vs.to_json(a))  # serialisable


def test_model_error_widens_the_band_but_not_the_median_or_the_order():
    import numpy as np

    import cycle_dcf as cd

    z90 = 1.2815515655446004
    rng = np.random.default_rng(0)
    v = np.exp(rng.normal(np.log(900), 0.2, 4000))

    def spread(a):
        return np.log(np.percentile(a, 90) / np.percentile(a, 10)) / (2 * z90)

    w, _ = cd.widen(v, 0.5)
    assert abs(np.median(w) - np.median(v)) < 1e-6 * np.median(v)
    assert (np.argsort(w) == np.argsort(v)).all()
    assert abs(spread(w) - (spread(v) ** 2 + 0.5**2) ** 0.5) < 1e-6
    same, k0 = cd.widen(v, 0.0)
    assert k0 == 1.0 and (same == v).all()


def test_run_widens_the_published_band():
    narrow = vs.run(INP, size=400, seed=3)
    assert narrow["mc"]["widen"] > 1.5
    lo, hi = narrow["percentiles"][1]["per_share"], narrow["percentiles"][9]["per_share"]
    assert hi / lo > 3  # a band this wide cannot come from parameter shocks alone
