# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""Tests for credit_score.py and credit_backtest.py: hand-computed values and the labelling rules."""

import math

import numpy as np
import pandas as pd
import pytest

import credit_backtest as cb
import credit_score as cs

F = {
    "at": 100.0,
    "lt": 50.0,
    "sale": 150.0,
    "ni": 5.0,
    "oibdp": 20.0,
    "oiadp": 10.0,
    "xint": 2.0,
    "act": 50.0,
    "lct": 30.0,
    "dlc": 5.0,
    "dltt": 15.0,
    "re": 20.0,
    "ceq": 50.0,
}


def test_points_interpolate_and_clip():
    t = [(0.0, 0), (1.0, 100)]
    assert cs.points(0.25, t) == pytest.approx(25)
    assert cs.points(-3, t) == 0 and cs.points(7, t) == 100
    d = [(0.6, 0), (0.2, 100)]  # decreasing table: lower is better
    assert cs.points(0.4, d) == pytest.approx(50)
    assert cs.points(0.9, d) == 0 and cs.points(0.0, d) == 100
    assert math.isnan(cs.points(float("nan"), t))


def test_spread_ratios():
    s = cs.spread(F)
    assert s["debt"] == 20 and s["debt_assets"] == pytest.approx(0.2)
    assert s["debt_ebitda"] == pytest.approx(1.0) and s["coverage"] == pytest.approx(10.0)
    assert s["current_ratio"] == pytest.approx(50 / 30) and s["roa"] == pytest.approx(0.05)


def test_spread_edge_cases():
    no_debt = dict(F, dlc=0.0, dltt=0.0, xint=0.0)
    assert cs.spread(no_debt)["coverage"] == 99.0 and cs.spread(no_debt)["debt_ebitda"] == 0.0
    loss = dict(F, oibdp=-4.0)
    assert cs.spread(loss)["debt_ebitda"] == 99.0  # debt with no EBITDA to service it


def test_altman_z_hand_computed():
    # 1.2(.2) + 1.4(.2) + 3.3(.1) + 0.6(60/50) + 1.0(1.5) = 3.07
    assert cs.altman_z(F, 60.0) == pytest.approx(3.07)
    # 6.56(.2) + 3.26(.2) + 6.72(.1) + 1.05(50/50) = 3.686
    assert cs.altman_z2(F) == pytest.approx(3.686)
    assert math.isnan(cs.altman_z(F, None))


def test_altman_z_matches_ll_flooring_memo():
    ll = {
        "at": 614.0,
        "lt": 357.9,
        "sale": 1110.679,
        "ni": -12.081,
        "oibdp": 16.4,
        "oiadp": -11.722,
        "xint": 1.8,
        "act": 369.748,
        "lct": 180.524,
        "dlc": 72.0,
        "dltt": 0.0,
        "re": 177.5,
        "ceq": 256.081,
    }
    assert cs.altman_z(ll, 3.80 * 29.257) == pytest.approx(
        2.71, abs=0.01
    )  # the figure in the LL Flooring memo


def test_distance_to_default_hand_computed():
    # E=F=100, sigma_e .4, mu .1: sigma_d .15, sigma_v .275, DD = (ln 2 + .1 - .5(.275^2)) / .275
    expected = (math.log(2) + 0.1 - 0.5 * 0.275**2) / 0.275
    assert cs.distance_to_default(100, 100, 0.4, 0.1) == pytest.approx(expected)
    assert cs.distance_to_default(100, 0, 0.4, 0.1) == 10.0
    assert cs.distance_to_default(100, 1e6, 0.4, -0.9) == -6.0
    assert math.isnan(cs.distance_to_default(-5, 100, 0.4, 0.1))


def test_grade_bands():
    assert cs.grade(100) == 1 and cs.grade(85) == 1 and cs.grade(84.9) == 2
    assert cs.grade(45) == 5 and cs.grade(15) == 8 and cs.grade(14.9) == 9 and cs.grade(0) == 9
    assert cs.grade(float("nan")) is None


def test_stronger_company_gets_better_grade():
    strong = cs.rate(F, market_equity=500.0, sigma_e=0.25, past_return=0.15)
    weak_f = dict(F, ni=-20.0, oibdp=1.0, oiadp=-8.0, dlc=40.0, dltt=30.0, act=20.0, lct=45.0, re=-30.0)
    weak = cs.rate(weak_f, market_equity=10.0, sigma_e=0.9, past_return=-0.7)
    assert strong["grade"] < weak["grade"] and strong["score"] > weak["score"]
    assert strong["grade"] <= 2 and weak["grade"] >= 8


def test_rating_without_a_price_uses_statements_only():
    r = cs.rate(F)
    assert math.isnan(r["dd"]) and r["grade"] is not None
    assert math.isnan(r["altman_z"]) and not math.isnan(r["altman_z2"])


def test_too_little_data_is_not_rated():
    sparse = {"at": 100.0, "ni": 1.0}
    assert cs.rate(sparse)["grade"] is None


def test_auc():
    y = np.array([0, 0, 1, 1])
    assert cb.auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert cb.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert cb.auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    assert math.isnan(cb.auc(np.zeros(4), np.arange(4.0)))


def _monthly():
    rows = []
    for g, tic, start, n, last_prc in (
        (1, "ABCDQ", "2005-01-31", 40, 0.2),
        (2, "LKQ", "2005-01-31", 40, 30.0),
        (3, "SAFE", "2005-01-31", 40, 20.0),
        (4, "OLDQ", "2019-01-31", 70, 1.0),
        (5, "PENY", "2005-01-31", 30, 0.4),
    ):
        for i in range(n):
            rows.append(
                {
                    "gvkey": g,
                    "tic": tic,
                    "date": pd.Timestamp(start) + pd.offsets.MonthEnd(i),
                    "prc": last_prc if i == n - 1 else 5.0,
                }
            )
    return pd.DataFrame(rows)


def test_failure_labels():
    firm = cb.failures(_monthly())
    assert bool(firm.loc[1, "fail"])  # Q ticker, history ends in 2008
    assert not firm.loc[2, "fail"]  # LKQ is too short to be a bankruptcy marker
    assert not bool(firm.loc[3, "fail"])  # left at a normal price: a merger, not a failure
    assert bool(firm.loc[4, "unknown"]) and not bool(
        firm.loc[4, "fail"]
    )  # Q ticker still trading at the end: dropped
    assert bool(firm.loc[5, "penny_exit"]) and not bool(firm.loc[5, "fail"])
    assert not bool(firm.loc[3, "penny_exit"])


def test_horizon_labels_and_gone_firms_dropped():
    firm = pd.DataFrame(
        {
            "tic": ["XYZQ"],
            "last": [pd.Timestamp("2010-06-30")],
            "fail": [True],
            "unknown": [False],
            "penny_exit": [False],
            "last_prc": [0.2],
        },
        index=pd.Index([1], name="gvkey"),
    )
    p = pd.DataFrame({"gvkey": [1, 1, 1], "info": pd.to_datetime(["2008-04-30", "2009-04-30", "2010-07-31"])})
    out = cb.attach(p, firm)
    assert len(out) == 2  # the statement dated after the exit is dropped
    assert out["months_to_exit"].tolist() == [26, 14]
    assert out["y24"].tolist() == [0, 1] and out["y12"].tolist() == [0, 0]


def _row(**kw):
    base = {
        "fail": False,
        "unknown": False,
        "penny_exit": False,
        "last": pd.Timestamp("2020-01-31"),
        "months": 20,
    }
    return type("R", (), {**base, **kw})()


def test_fate_of_classifies_each_outcome():
    assert cb.fate_of(_row(fail=True)).startswith("1")
    assert cb.fate_of(_row(fail=True, months=40)).startswith("5")
    assert cb.fate_of(_row(unknown=True, last=pd.Timestamp("2024-12-31"))).startswith("2")
    assert cb.fate_of(_row(penny_exit=True)).startswith("3")
    assert cb.fate_of(_row()).startswith("4")  # left at a normal price: a merger
    assert cb.fate_of(_row(last=pd.Timestamp("2024-12-31"), months=40)).startswith("6")


def test_overlay_notches_are_capped_and_ordered():
    import credit_ceiling as cc

    d = pd.DataFrame(
        {"ret12": [0.1, -0.35, -0.6, -0.6], "d_roa": [0.0, 0.0, 0.0, -0.2], "growth": [0.0, 0.0, 0.0, 0.0]}
    )
    assert cc.notches(d).tolist() == [0, 1, 2, 2]  # two notches at most
    assert cc.notches(d, market=False).tolist() == [0, 0, 0, 1]
