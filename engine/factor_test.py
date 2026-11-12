# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
factor_test.py -- do value, profitability and momentum rank next month's stock returns? A survivorship-free test.

The wide backtest (backtest.py) uses today's index members, so firms that failed or left the index are missing. This
test uses a CRSP/Compustat merged monthly file that keeps delisted firms, so the sample at each date is the sample
an investor could have seen then.

    python factor_test.py CCM_slim.parquet FF4.parquet sp500list.parquet [--start 2001-01] [--end 2024-11]

The data are licensed (WRDS), so the files are not in this repository; the script only needs their paths.

Input columns (CCM_slim.parquet): permno, date (month end), datadate (fiscal year end), ret (percent), prc,
shrout (thousands), sic, and the Compustat items at, ceq, ib, oiadp. FF4.parquet: mdate 'YYYY-MM', Mkt-RF, SMB, HML,
Mom, RF in percent. sp500list.parquet: permno, startdt, enddt.

Design, fixed before looking at results:
  * Fundamentals are used 6 months after the fiscal year end, the Fama-French convention, and no later than 18 months.
  * Signals at month t predict the return in month t+1. Financials (SIC 6000-6999) are excluded and so are prices
    under $5.
  * Universes: the 1,000 largest firms by market value each month, and point-in-time S&P 500 members.
  * Signals: book-to-market (ceq / market value), earnings yield (ib / market value), operating profitability
    (oiadp / assets), momentum (return over months t-11 to t-1, skipping month t). Value is the average percentile
    rank of the first two. The composite is the average percentile rank of value, profitability and momentum.
  * Scoring: monthly rank correlation of signal with next-month return (mean, t-statistic, share of months positive),
    and the return and Fama-French-plus-momentum alpha of the top minus bottom quintile (Newey-West, 6 lags).
Nothing here is tuned. The six signals are all that were run.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

SIGNALS = ["bm", "ep", "value", "op", "mom", "composite"]
LAG_MONTHS, MAX_STALE = 6, 18
MIN_PRICE, TOP_N = 5.0, 1000


def _period_index(s: pd.Series) -> pd.Series:
    p = s.dt.to_period("M")
    return p.dt.year * 12 + p.dt.month


def fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (permno, fiscal year end) with the month it may first be used."""
    f = df.drop_duplicates(["permno", "datadate"])[["permno", "datadate", "at", "ceq", "ib", "oiadp"]].copy()
    f["avail"] = _period_index(f["datadate"]) + LAG_MONTHS
    return f.sort_values(["avail", "permno"])


def build_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Firm-month panel with market value, the four signals and next month's return."""
    d = df[~df["sic"].between(6000, 6999)].copy()
    d["mi"] = _period_index(d["date"])
    d = d.sort_values(["mi", "permno"])
    d["mcap"] = d["prc"].abs() * d["shrout"] / 1e3  # $ million
    d["px"] = d["prc"].abs()

    f = fundamentals(df)
    d = pd.merge_asof(
        d,
        f.drop(columns=["datadate"]).rename(
            columns=lambda c: c + "_f" if c in ("at", "ceq", "ib", "oiadp") else c
        ),
        left_on="mi",
        right_on="avail",
        by="permno",
        direction="backward",
    )
    stale = d["mi"] - d["avail"] > MAX_STALE - LAG_MONTHS
    for c in ("at_f", "ceq_f", "ib_f", "oiadp_f"):
        d.loc[stale, c] = np.nan
    d["bm"] = np.where(d["ceq_f"] > 0, d["ceq_f"] / d["mcap"], np.nan)
    d["ep"] = d["ib_f"] / d["mcap"]
    d["op"] = np.where(d["at_f"] > 0, d["oiadp_f"] / d["at_f"], np.nan)

    # momentum: compound the returns of months t-11 .. t-1, needing at least ten of the eleven
    r = d.pivot(index="mi", columns="permno", values="ret") / 100.0
    full = r.reindex(range(int(r.index.min()), int(r.index.max()) + 1))
    logr = np.log1p(full)
    window = logr.shift(1).rolling(11, min_periods=10).sum()  # months t-11 .. t-1
    mom = np.expm1(window).stack().rename("mom").reset_index()
    d = d.merge(mom, on=["mi", "permno"], how="left")

    nxt = full.shift(-1).stack().rename("fwd").reset_index()  # month t+1 return, percent/100
    nxt["fwd"] *= 100.0
    d = d.merge(nxt, on=["mi", "permno"], how="left")
    return d


def add_ranks(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    g = p.groupby("mi")
    for c in ("bm", "ep", "op", "mom"):
        p["r_" + c] = g[c].rank(pct=True)
    p["bm_s"], p["ep_s"], p["op_s"], p["mom_s"] = p["r_bm"], p["r_ep"], p["r_op"], p["r_mom"]
    p["value"] = p[["r_bm", "r_ep"]].mean(axis=1, skipna=False)
    p["r_value"] = p.groupby("mi")["value"].rank(pct=True)
    p["composite"] = p[["r_value", "r_op", "r_mom"]].mean(axis=1, skipna=False)
    return p


def universe_top(p: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    q = p[p["px"] >= MIN_PRICE].copy()
    q["size_rank"] = q.groupby("mi")["mcap"].rank(ascending=False, method="first")
    return q[q["size_rank"] <= n]


def sp500_members(sp: pd.DataFrame) -> set[tuple[int, int]]:
    out = set()
    for permno, a, b in zip(
        sp["permno"], pd.to_datetime(sp["startdt"]), pd.to_datetime(sp["enddt"]), strict=True
    ):
        b = pd.Timestamp("2100-01-01") if pd.isna(b) else b
        lo = a.year * 12 + a.month
        hi = b.year * 12 + b.month
        out.update((int(permno), m) for m in range(lo, hi + 1))
    return out


def universe_sp500(p: pd.DataFrame, members: set[tuple[int, int]]) -> pd.DataFrame:
    key = list(zip(p["permno"].astype(int), p["mi"].astype(int), strict=True))
    return p[[k in members for k in key]]


def nw_se(x: np.ndarray, lags: int = 6) -> float:
    """Newey-West standard error of the mean."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n, e = len(x), x - np.nanmean(x)
    v = float(e @ e) / n
    for k in range(1, lags + 1):
        v += 2 * (1 - k / (lags + 1)) * float(e[k:] @ e[:-k]) / n
    return float(np.sqrt(v / n))


def ic_by_month(p: pd.DataFrame, sig: str) -> pd.Series:
    def one(g):
        g = g[[sig, "fwd"]].dropna()
        return g[sig].rank().corr(g["fwd"].rank()) if len(g) >= 50 else np.nan

    return p.groupby("mi").apply(one, include_groups=False)


def quintile_returns(p: pd.DataFrame, sig: str, weighted: bool = False) -> pd.DataFrame:
    """Monthly return of each quintile of the signal (1 lowest, 5 highest), percent."""
    q = p[[sig, "fwd", "mcap", "mi"]].dropna().copy()
    q["bin"] = q.groupby("mi")[sig].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)) + 1
    if weighted:
        q["w"] = q["mcap"]
        res = q.groupby(["mi", "bin"]).apply(
            lambda g: np.average(g["fwd"], weights=g["w"]), include_groups=False
        )
    else:
        res = q.groupby(["mi", "bin"])["fwd"].mean()
    return res.unstack("bin")


def alpha(spread: pd.Series, ff: pd.DataFrame, lags: int = 6) -> tuple[float, float]:
    """Intercept (percent per month) and its Newey-West t-statistic from a four-factor regression."""
    import statsmodels.api as sm

    x = ff.reindex(spread.index)[["Mkt-RF", "SMB", "HML", "Mom"]]
    ok = x.notna().all(axis=1) & spread.notna()
    if ok.sum() < 36:
        return float("nan"), float("nan")
    m = sm.OLS(spread[ok], sm.add_constant(x[ok])).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(m.params["const"]), float(m.tvalues["const"])


def load_ff(path: str) -> pd.DataFrame:
    ff = pd.read_parquet(path)
    per = pd.PeriodIndex(ff["mdate"].astype(str), freq="M")
    ff["mi"] = per.year * 12 + per.month
    return ff.set_index("mi")


def evaluate(
    p: pd.DataFrame, ff: pd.DataFrame, start: int, end: int, name: str, weighted: bool = False
) -> list[dict]:
    p = p[(p["mi"] >= start) & (p["mi"] <= end)]
    rows = []
    for sig in SIGNALS:
        ic = ic_by_month(p, sig).dropna()
        qr = quintile_returns(p, sig, weighted)
        spread = qr[5] - qr[1]
        a, ta = alpha(spread, ff)
        rows.append(
            {
                "universe": name,
                "signal": sig,
                "months": len(ic),
                "ic": float(ic.mean()),
                "ic_t": float(ic.mean() / nw_se(ic.values)),
                "ic_pos": float((ic > 0).mean()),
                "q1": float(qr[1].mean()),
                "q5": float(qr[5].mean()),
                "spread": float(spread.mean()),
                "spread_t": float(spread.mean() / nw_se(spread.values)),
                "alpha": a,
                "alpha_t": ta,
            }
        )
    return rows


def table(rows: list[dict]) -> str:
    h = f"{'universe':22s}{'signal':11s}{'n':>5s}{'IC':>8s}{'t':>6s}{'%>0':>6s}{'Q1':>7s}{'Q5':>7s}{'Q5-Q1':>7s}{'t':>6s}{'alpha':>7s}{'t':>6s}"
    L = [h]
    for r in rows:
        L.append(
            f"{r['universe']:22s}{r['signal']:11s}{r['months']:5d}{r['ic']:8.3f}{r['ic_t']:6.1f}{r['ic_pos'] * 100:5.0f}%"
            f"{r['q1']:7.2f}{r['q5']:7.2f}{r['spread']:7.2f}{r['spread_t']:6.1f}{r['alpha']:7.2f}{r['alpha_t']:6.1f}"
        )
    return "\n".join(L)


def mi(s: str) -> int:
    return int(s[:4]) * 12 + int(s[5:7])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ccm")
    ap.add_argument("ff4")
    ap.add_argument("sp500")
    ap.add_argument("--start", default="2001-01")
    ap.add_argument("--end", default="2024-11")
    ap.add_argument("--split", default="2013-01", help="start of the second sub-period")
    a = ap.parse_args(argv[1:])

    cols = ["permno", "date", "datadate", "ret", "prc", "shrout", "sic", "at", "ceq", "ib", "oiadp"]
    df = pd.read_parquet(a.ccm, columns=cols)
    p = add_ranks(build_panel(df))
    ff = load_ff(a.ff4)
    top = universe_top(p)
    sp = universe_sp500(p[p["px"] >= MIN_PRICE], sp500_members(pd.read_parquet(a.sp500)))
    s, e, mid = mi(a.start), mi(a.end), mi(a.split)

    out = []
    for label, lo, hi in (
        ("all", s, e),
        (f"{a.start}..{a.split}", s, mid - 1),
        (f"{a.split}..{a.end}", mid, e),
    ):
        print(f"\n=== {label}: equal-weighted quintiles ===")
        rows = evaluate(top, ff, lo, hi, "top 1000 by size") + evaluate(sp, ff, lo, hi, "S&P 500 members")
        print(table(rows))
        out += [{**r, "period": label, "weighting": "equal"} for r in rows]
    print("\n=== all: value-weighted quintiles ===")
    rows = evaluate(top, ff, s, e, "top 1000 by size", True) + evaluate(sp, ff, s, e, "S&P 500 members", True)
    print(table(rows))
    out += [{**r, "period": "all", "weighting": "value"} for r in rows]
    pd.DataFrame(out).to_csv("factor_test_results.csv", index=False)
    print("\nfirm-months:", len(top), "top-1000;", len(sp), "S&P 500; written factor_test_results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
