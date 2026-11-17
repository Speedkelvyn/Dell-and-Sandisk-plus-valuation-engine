# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
credit_ceiling.py -- is the fixed-weight rating leaving accuracy on the table, and would early-warning rules help?

Two checks on the rating in credit_score.py, both with the failures defined in credit_backtest.py:

1. Ceiling. A logistic regression is fitted on statements dated up to 2012 and scored on 2013 and later, using the same
   inputs as the scorecard (and, in other rows, trend and market inputs). If the fixed weights are close to the fitted
   model on the test years, re-weighting would not help.
2. Overlays. Rules fixed in advance that move a grade down when the stock has fallen sharply or the statements are
   deteriorating. They are kept only if they raise the catch rate without a matching rise in the share flagged.

    python credit_ceiling.py /path/to/CCM_slim.parquet        (needs scikit-learn)
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

import credit_backtest as cb
import credit_score as cs

BASE = ["debt_assets", "coverage", "debt_ebitda", "current_ratio", "roa", "retained_assets", "log_assets"]
TREND = ["d_roa", "d_debt", "d_margin", "growth"]
MARKET = ["mret", "lvol", "lmcap_debt"]


def add_features(p: pd.DataFrame) -> pd.DataFrame:
    """Spread ratios, one-year changes (only where the prior statement is a year earlier) and market variables."""
    p = p.sort_values(["gvkey", "datadate"]).copy()
    cols = ["at", "lt", "sale", "ni", "oibdp", "oiadp", "xint", "act", "lct", "dlc", "dltt", "re", "ceq"]
    sp = pd.DataFrame(
        [cs.spread(dict(zip(cols, r, strict=True))) for r in p[cols].itertuples(index=False)], index=p.index
    )
    for c in [*BASE, "ebitda_margin"]:
        if c not in p:
            p[c] = sp[c]
    g = p.groupby("gvkey")
    ok = (p["datadate"] - g["datadate"].shift(1)).dt.days.between(330, 400)
    p["d_roa"] = np.where(ok, p["roa"] - g["roa"].shift(1), np.nan)
    p["d_debt"] = np.where(ok, p["debt_assets"] - g["debt_assets"].shift(1), np.nan)
    p["d_margin"] = np.where(ok, p["ebitda_margin"] - g["ebitda_margin"].shift(1), np.nan)
    p["growth"] = np.where(ok, p["sale"] / g["sale"].shift(1) - 1, np.nan)
    p["mret"] = p["ret12"]
    p["lvol"] = np.log(p["vol12"].clip(lower=0.05))
    debt = p["dlc"].fillna(0) + p["dltt"].fillna(0)
    p["lmcap_debt"] = np.log((p["mcap"] / (debt + p["mcap"])).clip(lower=1e-6))
    p["yr"] = p["datadate"].dt.year
    return p


def fitted_auc(p: pd.DataFrame, cols: list[str], label: str = "y12", c: float = 0.3) -> tuple[float, float]:
    """Logistic regression fitted on statements to SPLIT_YEAR, AUC on the later years and on the fitting years."""
    tr, te = p[p["yr"] <= cb.SPLIT_YEAR], p[p["yr"] > cb.SPLIT_YEAR]
    lo, hi = tr[cols].quantile(0.01), tr[cols].quantile(0.99)
    med = tr[cols].clip(lo, hi, axis=1).median()
    miss = [k for k in cols if tr[k].isna().any()]
    mu = tr[cols].clip(lo, hi, axis=1).fillna(med).mean()
    sd = tr[cols].clip(lo, hi, axis=1).fillna(med).std().replace(0, 1)

    def mat(d: pd.DataFrame) -> np.ndarray:
        z = (d[cols].clip(lo, hi, axis=1).fillna(med) - mu) / sd
        return pd.concat([z, d[miss].isna().astype(float).add_suffix("_na")], axis=1).to_numpy()

    m = LogisticRegression(C=c, max_iter=2000).fit(mat(tr), tr[label])
    return cb.auc(te[label].to_numpy(), m.decision_function(mat(te))), cb.auc(
        tr[label].to_numpy(), m.decision_function(mat(tr))
    )


def notches(d: pd.DataFrame, market: bool = True, trend: bool = True) -> pd.Series:
    """Grades to subtract: stock down 30% (1) or 50% (2) in a year; ROA down 10 points or sales down 25% (1); at most 2."""
    n = pd.Series(0, index=d.index)
    if market:
        n += np.where(d["ret12"] <= -0.5, 2, np.where(d["ret12"] <= -0.3, 1, 0))
    if trend:
        n += np.where((d["d_roa"] <= -0.10) | (d["growth"] <= -0.25), 1, 0)
    return n.clip(upper=2)


def report(p: pd.DataFrame) -> None:
    te = p[p["yr"] > cb.SPLIT_YEAR]
    y = te["y12"].to_numpy()
    print(
        f"test years, 12-month AUC.  Fixed-weight rating {cb.auc(y, -te['score']):.3f}   statements only {cb.auc(y, -te['score_fund']):.3f}"
    )
    rows = {
        "logistic, statements only": BASE,
        "logistic, statements + trend": BASE + TREND,
        "logistic, statements + distance to default": [*BASE, "dd"],
        "logistic, statements + distance to default + trend": [*BASE, "dd", *TREND],
        "logistic, statements + stock return, volatility, market leverage": BASE + MARKET,
    }
    for name, cols in rows.items():
        a, b = fitted_auc(p, cols)
        print(f"  {name:66s} test {a:.3f}  (fitting years {b:.3f})")
    print("\noverlays on the grade (12 months, flagged = grade 7-9)")
    for name, sub in (("fitting years", p[p["yr"] <= cb.SPLIT_YEAR]), ("test years", te)):
        yy = sub["y12"].to_numpy()
        for lab, kw in (
            ("none", None),
            ("stock fall", {"trend": False}),
            ("trend", {"market": False}),
            ("both", {}),
        ):
            g = sub["grade"] if kw is None else (sub["grade"] + notches(sub, **kw)).clip(upper=9)
            f = g >= 7
            print(
                f"  {name:14s} {lab:11s} AUC {cb.auc(yy, g.to_numpy()):.3f}  catch {(f & (yy == 1)).sum() / yy.sum():.1%}  flagged {f.mean():.1%}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ccm")
    df = cb.load(ap.parse_args().ccm)
    p = cb.score_panel(cb.attach(cb.panel(df), cb.failures(df))).dropna(subset=["score"])
    report(add_features(p))


if __name__ == "__main__":
    main()
