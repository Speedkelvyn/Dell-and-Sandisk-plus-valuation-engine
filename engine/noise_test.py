# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
noise_test.py -- do price moves that look like noise reverse? Short-term reversal, split by unusual trading volume.

If a month's return was driven by rumour or attention rather than information, it should reverse the next month. If it
carried information, it should hold. Heavy volume marks news and attention, so the test is whether reversal depends on
how unusual the month's volume was. Uses the same survivorship-free CRSP/Compustat sample as factor_test.py.

    python noise_test.py CCM_slim.parquet FF4.parquet sp500list.parquet

Fixed in advance: signal = minus the month-t return (high = recent loser, expected to bounce); abnormal volume =
month-t volume over its mean in months t-12 to t-1, split into terciles each month; the same universes, period, scoring
and Newey-West errors as factor_test.py. Nasdaq volume before 2004 double-counts dealer trades, which is why volume is
scaled by the firm's own history.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import factor_test as ft


def add_noise_signals(p: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["mi"] = ft._period_index(d["date"])
    v = d.pivot(index="mi", columns="permno", values="vol").astype(float)
    v = v.reindex(range(int(v.index.min()), int(v.index.max()) + 1))
    prior = v.shift(1).rolling(12, min_periods=8).mean()
    av = (v / prior).replace([np.inf, -np.inf], np.nan).stack().rename("avol").reset_index()
    p = p.merge(av, on=["mi", "permno"], how="left")
    p["rev"] = -p["ret"]
    g = p.groupby("mi")["avol"]
    p["vol_bin"] = g.transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if s.notna().sum() >= 90 else np.nan
    )
    return p


def score(p: pd.DataFrame, ff: pd.DataFrame, sig: str, lo: int, hi: int) -> dict:
    q = p[(p["mi"] >= lo) & (p["mi"] <= hi)]
    ic = ft.ic_by_month(q, sig).dropna()
    qr = ft.quintile_returns(q, sig)
    sp = qr[5] - qr[1]
    a, ta = ft.alpha(sp, ff)
    return {
        "n": len(ic),
        "ic": ic.mean(),
        "ic_t": ic.mean() / ft.nw_se(ic.values),
        "spread": sp.mean(),
        "spread_t": sp.mean() / ft.nw_se(sp.values),
        "alpha": a,
        "alpha_t": ta,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    cols = ["permno", "date", "datadate", "ret", "prc", "shrout", "sic", "vol", "at", "ceq", "ib", "oiadp"]
    df = pd.read_parquet(argv[1], columns=cols)
    p = add_noise_signals(ft.add_ranks(ft.build_panel(df)), df)
    ff = ft.load_ff(argv[2])
    top = ft.universe_top(p)
    sp = ft.universe_sp500(p[p["px"] >= ft.MIN_PRICE], ft.sp500_members(pd.read_parquet(argv[3])))
    s, e, mid = ft.mi("2002-02"), ft.mi("2024-11"), ft.mi("2013-01")
    print(
        f"{'universe':20s}{'period':8s}{'volume':10s}{'n':>5s}{'IC':>8s}{'t':>6s}{'Q5-Q1':>8s}{'t':>6s}{'alpha':>8s}{'t':>6s}"
    )
    for uname, u in (("top 1000", top), ("S&P 500", sp)):
        for plabel, lo, hi in (("all", s, e), ("-2012", s, mid - 1), ("2013-", mid, e)):
            for vlabel, sub in (
                ("all", u),
                ("low", u[u["vol_bin"] == 0]),
                ("mid", u[u["vol_bin"] == 1]),
                ("high", u[u["vol_bin"] == 2]),
            ):
                r = score(sub, ff, "rev", lo, hi)
                print(
                    f"{uname:20s}{plabel:8s}{vlabel:10s}{r['n']:5d}{r['ic']:8.3f}{r['ic_t']:6.1f}{r['spread']:8.2f}{r['spread_t']:6.1f}{r['alpha']:8.2f}{r['alpha_t']:6.1f}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
