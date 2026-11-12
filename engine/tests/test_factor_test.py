# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
import numpy as np
import pandas as pd

import factor_test as ft


def synthetic(n_firms=80, n_months=40, seed=0, edge=0.0):
    """Monthly panel. Profit is fixed per firm; next-month return = edge * profit z-score + noise."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2010-01-31", periods=n_months, freq="ME")
    prof = rng.normal(size=n_firms)
    rows = []
    for k in range(n_firms):
        for d in dates:
            fye = pd.Timestamp("2009-12-31") if d < pd.Timestamp("2011-01-01") else pd.Timestamp("2010-12-31")
            rows.append(
                {
                    "permno": k,
                    "date": d,
                    "datadate": fye,
                    "sic": 3500,
                    "prc": 20.0 + k,
                    "shrout": 1000.0,
                    "ret": 0.0,
                    "at": 100.0,
                    "ceq": 50.0,
                    "ib": 5.0,
                    "oiadp": 10.0 + 5 * prof[k],
                }
            )
    df = pd.DataFrame(rows)
    z = df["permno"].map(dict(enumerate(prof)))
    df["ret"] = 100 * (edge * z.shift(1).fillna(0) * 0.01 + rng.normal(0, 0.02, len(df)))
    return df


def test_fundamentals_wait_six_months():
    p = ft.build_panel(synthetic())

    def mi(ts):
        return ft._period_index(pd.Series([pd.Timestamp(ts)])).iloc[0]

    # fiscal year ended 2009-12 is usable from 2010-06; the year ended 2010-12 only from 2011-06
    assert (p.loc[p["date"] == pd.Timestamp("2011-05-31"), "avail"] == mi("2010-06-30")).all()
    assert (p.loc[p["date"] == pd.Timestamp("2011-06-30"), "avail"] == mi("2011-06-30")).all()
    assert (p["avail"].dropna() <= p.loc[p["avail"].notna(), "mi"]).all()


def test_next_month_return_is_aligned():
    df = synthetic(n_firms=5, n_months=6)
    p = ft.build_panel(df)
    a = p[(p["permno"] == 2)].sort_values("mi")
    assert np.allclose(a["fwd"].iloc[:-1].values, a["ret"].iloc[1:].values)
    assert np.isnan(a["fwd"].iloc[-1])


def test_ic_detects_a_planted_edge_and_not_noise():
    p = ft.add_ranks(ft.build_panel(synthetic(edge=0.0, seed=1)))
    ic0 = ft.ic_by_month(p, "op").dropna()
    assert abs(ic0.mean()) < 0.05
    q = ft.add_ranks(ft.build_panel(synthetic(edge=3.0, seed=1)))
    assert ft.ic_by_month(q, "op").dropna().mean() > 0.2


def test_momentum_skips_the_signal_month():
    df = synthetic(n_firms=3, n_months=30)
    df["ret"] = 0.0
    df.loc[df["date"] == pd.Timestamp("2011-03-31"), "ret"] = 10.0  # +10% in March 2011
    p = ft.build_panel(df)
    m = p[p["permno"] == 0].set_index("date")["mom"]
    assert abs(m[pd.Timestamp("2011-03-31")]) < 1e-9  # the signal month itself is skipped
    assert abs(m[pd.Timestamp("2011-04-30")] - 0.10) < 1e-9
    assert abs(m[pd.Timestamp("2011-12-31")] - 0.10) < 1e-9
    assert abs(m[pd.Timestamp("2012-02-29")] - 0.10) < 1e-9  # still inside months t-11..t-1
    assert abs(m[pd.Timestamp("2012-03-31")]) < 1e-9  # dropped out of the window


def test_financials_are_excluded():
    df = synthetic(n_firms=4, n_months=3)
    df.loc[df["permno"] == 0, "sic"] = 6021
    assert 0 not in set(ft.build_panel(df)["permno"])


def test_sp500_membership_is_point_in_time():
    sp = pd.DataFrame({"permno": [1], "startdt": ["2010-03-01"], "enddt": ["2010-06-15"]})
    m = ft.sp500_members(sp)
    assert (1, 2010 * 12 + 3) in m and (1, 2010 * 12 + 6) in m
    assert (1, 2010 * 12 + 2) not in m and (1, 2010 * 12 + 7) not in m


def test_newey_west_matches_plain_se_for_independent_data():
    x = np.random.default_rng(3).normal(size=4000)
    assert abs(ft.nw_se(x) - x.std() / np.sqrt(len(x))) < 0.2 * x.std() / np.sqrt(len(x))
