# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
credit_backtest.py -- does the risk rating in credit_score.py warn before a company fails?

Data: the CRSP/Compustat merged monthly file (licensed, not in the repository). One observation is one company-year:
the annual statements, available 4 months after the fiscal year end (the 10-K deadline plus a margin), and the stock
price, 12-month volatility and 12-month return at that date.

Failure label. The file has no delisting code, so a failure is a company whose final Compustat ticker ends in Q (the
Compustat marker for a company trading under bankruptcy protection; four letters or more, to leave out real tickers
such as LKQ) and whose price history ends before mid-2024. The month of the last price is the failure date. That is
the date the stock left the exchange, which is usually within a few months of the filing and sometimes much later.
Companies whose Q ticker is still trading at the end of the file are dropped, because their filing is after the data.
Companies that left for any other reason (merger, going private) count as survivors; failures without the Q marker
count as survivors too, which understates the catch rate. The sensitivity run adds exits at a price under $1.

Sample: non-financial companies (SIC outside 6000 to 6999) with $50 million or more of assets and a price at the date.

Fixed before the results: the scorecard and the grade bands, the 12 and 24 month horizons, the split year.
Train period: statements dated up to 2012. Test period: 2013 and later. Nothing is fitted, so the split reports whether
the pattern is stable, not whether a model generalizes.

    python credit_backtest.py /path/to/CCM_slim.parquet
"""

from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

import credit_score as cs

LAG_MONTHS = 4
HORIZONS = (12, 24)
SPLIT_YEAR = 2012
END_CUTOFF = pd.Timestamp("2024-06-30")
COLS = [
    "gvkey",
    "permno",
    "tic",
    "sic",
    "fyear",
    "datadate",
    "date",
    "ret",
    "prc",
    "shrout",
    "at",
    "lt",
    "sale",
    "ni",
    "oibdp",
    "oiadp",
    "xint",
    "act",
    "lct",
    "dlc",
    "dltt",
    "re",
    "ceq",
]


def month_end(s: pd.Series) -> pd.Series:
    return s.dt.to_period("M").dt.to_timestamp("M")


def load(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=COLS)
    df["date"] = month_end(pd.to_datetime(df["date"]))
    df["datadate"] = pd.to_datetime(df["datadate"])
    df["mcap"] = df["prc"].abs() * df["shrout"] / 1000.0  # US$ millions
    df = df.sort_values(["gvkey", "date", "mcap"]).drop_duplicates(["gvkey", "date"], keep="last")
    return df.reset_index(drop=True)


def failures(df: pd.DataFrame) -> pd.DataFrame:
    """One row per firm: failure flag, date and the final price."""
    g = df.sort_values("date").groupby("gvkey")
    firm = g.agg(tic=("tic", "last"), last=("date", "max"), last_prc=("prc", lambda s: abs(s.iloc[-1])))
    q = firm["tic"].astype(str).str.upper().str.endswith("Q") & (firm["tic"].astype(str).str.len() >= 4)
    firm["fail"] = q & (firm["last"] < END_CUTOFF)
    firm["unknown"] = q & ~firm["fail"]  # Q ticker still trading at the end: filed after the data
    firm["penny_exit"] = ~q & (firm["last"] < END_CUTOFF) & (firm["last_prc"] < 1.0)
    return firm


def market_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per firm-month: 12-month volatility (annualised) and 12-month compounded return, from monthly returns."""
    r = df["ret"] / 100.0
    d = df[["gvkey", "date"]].copy()
    d["r"] = r
    grp = d.groupby("gvkey")["r"]
    d["vol12"] = grp.transform(lambda s: s.rolling(12, min_periods=9).std()) * math.sqrt(12)
    d["lr"] = np.log1p(d["r"].clip(lower=-0.999))
    d["ret12"] = np.expm1(
        d.groupby("gvkey")["lr"].transform(lambda s: s.rolling(12, min_periods=9).mean()) * 12
    )
    return d[["gvkey", "date", "vol12", "ret12"]]


def panel(df: pd.DataFrame) -> pd.DataFrame:
    """Annual statements dated and joined to the market at the information date."""
    fund = (
        df.dropna(subset=["datadate", "at"])
        .sort_values(["gvkey", "datadate", "date"])
        .drop_duplicates(["gvkey", "datadate"], keep="first")
    )
    fund = fund[
        [
            "gvkey",
            "fyear",
            "datadate",
            "sic",
            "at",
            "lt",
            "sale",
            "ni",
            "oibdp",
            "oiadp",
            "xint",
            "act",
            "lct",
            "dlc",
            "dltt",
            "re",
            "ceq",
        ]
    ].copy()
    fund["info"] = month_end(fund["datadate"] + pd.DateOffset(months=LAG_MONTHS))
    mkt = df[["gvkey", "date", "mcap", "prc"]].merge(market_features(df), on=["gvkey", "date"])
    p = fund.merge(mkt, left_on=["gvkey", "info"], right_on=["gvkey", "date"], how="inner")
    p = p[(p["at"] >= 50) & ~p["sic"].between(6000, 6999) & (p["mcap"] > 0)]
    return p.reset_index(drop=True)


def attach(p: pd.DataFrame, firm: pd.DataFrame) -> pd.DataFrame:
    p = p.merge(
        firm[["tic", "last", "fail", "unknown", "penny_exit", "last_prc"]], left_on="gvkey", right_index=True
    )
    p = p[~p["unknown"] & (p["last"] > p["info"])].copy()  # drop firms already gone at the information date
    months = (p["last"].dt.year - p["info"].dt.year) * 12 + (p["last"].dt.month - p["info"].dt.month)
    p["months_to_exit"] = months
    for h in HORIZONS:
        p[f"y{h}"] = (p["fail"] & (months <= h)).astype(int)
        p[f"b{h}"] = ((p["fail"] | p["penny_exit"]) & (months <= h)).astype(int)
    return p


def score_panel(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in p.itertuples(index=False):
        f = {
            "at": r.at,
            "lt": r.lt,
            "sale": r.sale,
            "ni": r.ni,
            "oibdp": r.oibdp,
            "oiadp": r.oiadp,
            "xint": r.xint,
            "act": r.act,
            "lct": r.lct,
            "dlc": r.dlc,
            "dltt": r.dltt,
            "re": r.re,
            "ceq": r.ceq,
        }
        rated = cs.rate(f, r.mcap, r.vol12, r.ret12)
        feats = dict(cs.spread(f).items())  # no market inputs: what a private borrower gets
        rated["score_fund"] = cs.score(feats)
        rated["grade_fund"] = cs.grade(rated["score_fund"])
        rows.append(rated)
    out = pd.DataFrame(rows, index=p.index)
    return p.join(out[["altman_z", "altman_z2", "dd", "score", "grade", "score_fund", "grade_fund"]])


# ------------------------------------------------------------------ evaluation
def auc(y: np.ndarray, risk: np.ndarray) -> float:
    """Probability that a failed company has a higher risk value than a survivor (ties count half)."""
    m = ~np.isnan(risk)
    y, risk = y[m], risk[m]
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    ranks = pd.Series(risk).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def evaluate(p: pd.DataFrame, h: int = 12, label: str = "y") -> dict:
    y = p[f"{label}{h}"].to_numpy()
    out = {"n": len(p), "failures": int(y.sum()), "base_rate": float(y.mean())}
    out["auc_rating"] = auc(y, -p["score"].to_numpy())
    out["auc_z"] = auc(y, -p["altman_z"].to_numpy())
    out["auc_z2"] = auc(y, -p["altman_z2"].to_numpy())
    out["auc_dd"] = auc(y, -p["dd"].to_numpy())
    return out


def by_grade(p: pd.DataFrame, h: int = 12, label: str = "y") -> pd.DataFrame:
    d = p.dropna(subset=["grade"]).copy()
    t = d.groupby("grade").agg(
        n=("gvkey", "size"), fails=(f"{label}{h}", "sum"), rate=(f"{label}{h}", "mean")
    )
    t["share"] = t["n"] / t["n"].sum()
    return t


def flag_stats(p: pd.DataFrame, flag: pd.Series, h: int = 12, label: str = "y") -> dict:
    """Catch rate (share of failures flagged), share of the population flagged, and precision."""
    y = p[f"{label}{h}"] == 1
    flagged = flag.fillna(False)
    return {
        "catch": float((flagged & y).sum() / max(y.sum(), 1)),
        "flagged": float(flagged.mean()),
        "precision": float((flagged & y).sum() / max(flagged.sum(), 1)),
    }


def lead_time(p: pd.DataFrame, flag_col: str) -> pd.Series:
    """For each failed firm, months between the first flagged statement and the exit."""
    f = p[p["fail"] & p[flag_col]].sort_values("info").groupby("gvkey").first()
    return f["months_to_exit"]


def placebo(p: pd.DataFrame, h: int = 12, n: int = 200, seed: int = 0) -> tuple[float, float]:
    """AUC of the rating when the failure labels are shuffled among company-years of the same year: should centre on 0.5."""
    rng = np.random.default_rng(seed)
    p = p.dropna(subset=["score"])
    year = p["datadate"].dt.year.to_numpy()
    y0 = p[f"y{h}"].to_numpy()
    risk = -p["score"].to_numpy()
    idx = [np.flatnonzero(year == yr) for yr in np.unique(year)]
    vals = []
    for _ in range(n):
        y = y0.copy()
        for ix in idx:
            y[ix] = y0[rng.permutation(ix)]
        vals.append(auc(y, risk))
    return float(np.mean(vals)), float(np.std(vals))


def other_exits(p: pd.DataFrame, h: int = 12) -> float:
    """AUC of the rating for leaving the data within h months for any reason other than failure (mergers and the like):
    a rating that only picks up 'about to disappear' would score high here too."""
    p = p.dropna(subset=["score"])
    y = ((~p["fail"]) & (p["months_to_exit"] <= h) & (p["last"] < END_CUTOFF)).astype(int).to_numpy()
    return auc(y, -p["score"].to_numpy())


EARLY_PRICE = 3.0


def early_warning(p: pd.DataFrame, min_price: float = EARLY_PRICE) -> None:
    """The test that matters to a lender: companies whose stock has not already collapsed (price at or above min_price)."""
    s = p[p["prc"].abs() >= min_price]
    y12 = p[p["y12"] == 1]
    print(
        f"\nalready collapsed: {(y12['prc'].abs() < 1).mean():.0%} of the 12-month failures traded under $1 and "
        f"{(y12['prc'].abs() < min_price).mean():.0%} under ${min_price:.0f} at the information date"
    )
    for name, sub in (
        ("all", s),
        ("train", s[s["datadate"].dt.year <= SPLIT_YEAR]),
        ("test", s[s["datadate"].dt.year > SPLIT_YEAR]),
    ):
        for h in HORIZONS:
            y = sub[f"y{h}"].to_numpy()
            print(
                f"[price >= ${min_price:.0f}, {name}, {h}m] n={len(sub):,} failures={int(y.sum())}  AUC rating "
                f"{auc(y, -sub['score'].to_numpy()):.3f}  statements only {auc(y, -sub['score_fund'].to_numpy()):.3f}  "
                f"Altman Z {auc(y, -sub['altman_z'].to_numpy()):.3f}  Z'' {auc(y, -sub['altman_z2'].to_numpy()):.3f}"
            )
    for h in HORIZONS:
        for nm, flag in (
            ("grade 7-9", s["grade"] >= 7),
            ("Altman Z < 1.81", s["altman_z"] < 1.81),
            ("Z'' < 1.1", s["altman_z2"] < 1.1),
        ):
            st = flag_stats(s, flag, h)
            print(
                f"[price >= ${min_price:.0f}] {h}m {nm:>16}: catch {st['catch']:.1%}  flagged {st['flagged']:.1%}  precision {st['precision']:.1%}"
            )
    print(
        f"default rate within 12 months by grade, price >= ${min_price:.0f}:\n"
        + by_grade(s, 12).to_string(float_format=lambda x: f"{x:.3f}")
    )
    lt = lead_time(s.assign(f79=s["grade"] >= 7), "f79")
    print(
        f"lead time, price >= ${min_price:.0f}: n={len(lt)} median {lt.median():.0f} months, quartiles {lt.quantile(0.25):.0f}-{lt.quantile(0.75):.0f}"
    )


def fate_of(r) -> str:
    """What happened within 36 months of a statement: needs fail, unknown, penny_exit, last, months and END_CUTOFF."""
    if r.fail and r.months <= 36:
        return "1 failed within 36 months (Q marker)"
    if r.unknown and r.months <= 36:
        return "2 Q ticker, filed after the data ends"
    if r.penny_exit and r.months <= 36:
        return "3 left within 36 months at under $1 (probable failure)"
    if r.last < END_CUTOFF and r.months <= 36:
        return "4 left within 36 months at $1 or more (merger, buyout)"
    if r.fail or r.unknown:
        return "5 failed after 36 months"
    return "6 still listed 36 months later"


def fate_table(df: pd.DataFrame, firm: pd.DataFrame, min_grade: int = 7) -> pd.DataFrame:
    """Outcomes of every company-year graded min_grade or worse, for statements with a full 36 months of follow-up."""
    cols = ["tic", "last", "fail", "unknown", "penny_exit", "last_prc"]
    pan = panel(df).merge(firm[cols], left_on="gvkey", right_index=True)
    pan = pan[(pan["last"] > pan["info"]) & (pan["datadate"].dt.year <= 2021)].copy()
    pan["months"] = (pan["last"].dt.year - pan["info"].dt.year) * 12 + (
        pan["last"].dt.month - pan["info"].dt.month
    )
    pan = pan.assign(months_to_exit=pan["months"])
    for h in HORIZONS:
        pan[f"y{h}"] = 0
    pan = score_panel(pan).dropna(subset=["score"])
    d = pan[pan["grade"] >= min_grade].copy()
    d["fate"] = [fate_of(r) for r in d.itertuples(index=False)]
    out = d.groupby(["fate"]).size().rename("n").to_frame()
    out["share"] = out["n"] / out["n"].sum()
    return out


def missed_failures(p: pd.DataFrame) -> pd.DataFrame:
    """The last statement before the exit of every failed company, with the grade it was given."""
    return p[p["fail"]].sort_values("info").groupby("gvkey").tail(1)


CASES = {
    "ENRNQ": "Enron",
    "NRTLQ": "Nortel",
    "CCTYQ": "Circuit City",
    "BLIAQ": "Blockbuster",
    "SHLDQ": "Sears Holdings",
    "BBBYQ": "Bed Bath & Beyond",
    "WLTGQ": "Walter Energy",
}


def case_table(p: pd.DataFrame) -> pd.DataFrame:
    """Rating history of well-known failures: grade (with and without the market component) by statement date."""
    d = p[p["tic"].isin(CASES)].copy()
    d["company"] = d["tic"].map(CASES)
    return d.sort_values(["company", "info"])[
        ["company", "datadate", "months_to_exit", "altman_z", "grade", "grade_fund"]
    ]


def report(p: pd.DataFrame) -> None:
    pd.set_option("display.width", 140)
    p = p.dropna(subset=["score"])
    print(
        f"sample {len(p):,} company-years, {p['gvkey'].nunique():,} firms, {int(p['fail'].groupby(p['gvkey']).first().sum())}"
        " failures (Q-marked, exit before mid-2024)"
    )
    for name, sub in (
        ("all", p),
        (f"train (<= {SPLIT_YEAR})", p[p["datadate"].dt.year <= SPLIT_YEAR]),
        (f"test (> {SPLIT_YEAR})", p[p["datadate"].dt.year > SPLIT_YEAR]),
    ):
        for h in HORIZONS:
            e = evaluate(sub, h)
            print(
                f"\n[{name}, {h}-month horizon]  n={e['n']:,}  failures={e['failures']}  base rate={e['base_rate']:.2%}"
            )
            print(
                f"  AUC  rating {e['auc_rating']:.3f}   Altman Z {e['auc_z']:.3f}   Z'' {e['auc_z2']:.3f}   distance to default {e['auc_dd']:.3f}"
            )
            print(
                f"  accuracy ratio (2 AUC - 1)  rating {2 * e['auc_rating'] - 1:.3f}   Altman Z {2 * e['auc_z'] - 1:.3f}"
            )
        t = by_grade(sub, 12)
        print("  default rate within 12 months by grade:\n" + t.to_string(float_format=lambda x: f"{x:.3f}"))
    for h in (12, 24):
        for nm, flag in (
            ("grade 7-9", p["grade"] >= 7),
            ("grade 8-9", p["grade"] >= 8),
            ("Altman Z < 1.81", p["altman_z"] < 1.81),
            ("Z'' < 1.1", p["altman_z2"] < 1.1),
        ):
            s = flag_stats(p, flag, h)
            print(
                f"{h}m  {nm:>16}: catch {s['catch']:.1%}  population flagged {s['flagged']:.1%}  precision {s['precision']:.1%}"
            )
    p = p.assign(f79=p["grade"] >= 7)
    lt = lead_time(p, "f79")
    print(
        f"\nlead time, first grade 7-9 statement to exit, months: n={len(lt)} median {lt.median():.0f}  "
        f"quartiles {lt.quantile(0.25):.0f}-{lt.quantile(0.75):.0f}"
    )
    e = evaluate(p, 12, "b")
    print(
        f"\nsensitivity, failures widened to include exits below $1: failures={e['failures']}  AUC rating {e['auc_rating']:.3f}"
        f"  Altman Z {e['auc_z']:.3f}"
    )
    m, s = placebo(p, 12)
    print(f"placebo (failure labels shuffled within each year), AUC rating {m:.3f} +/- {s:.3f}")
    print(
        f"AUC of the rating for non-failure exits within 12 months (mergers etc.): {other_exits(p, 12):.3f}"
    )
    early_warning(p)
    last = missed_failures(p)
    print(
        f"\nfailed companies, grade on the last statement before the exit (n={len(last)}): "
        + ", ".join(f"{int(g)}: {n}" for g, n in last["grade"].value_counts().sort_index().items())
    )
    print(f"graded 1-4: {(last['grade'] <= 4).mean():.1%}   graded 7-9: {(last['grade'] >= 7).mean():.1%}")
    ct = case_table(p)
    print("\nwell-known failures, last four statements before the exit:")
    print(ct.groupby("company").tail(4).round(2).to_string(index=False))
    pf = p.dropna(subset=["score_fund"])
    for name, sub in (("all", pf), ("test", pf[pf["datadate"].dt.year > SPLIT_YEAR])):
        for h in HORIZONS:
            y = sub[f"y{h}"].to_numpy()
            print(
                f"fundamentals only (no market inputs), {name}, {h}m: AUC {auc(y, -sub['score_fund'].to_numpy()):.3f}"
                f"  Altman Z'' {auc(y, -sub['altman_z2'].to_numpy()):.3f}  n={len(sub):,}"
            )
    t = by_grade(pf.assign(grade=pf["grade_fund"]), 12)
    print("fundamentals-only grade table (12m):\n" + t.to_string(float_format=lambda x: f"{x:.3f}"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ccm")
    df = load(ap.parse_args().ccm)
    firm = failures(df)
    report(score_panel(attach(panel(df), firm)))
    print("\nwhat happened within 36 months to companies graded 7-9 (statements to 2021):")
    print(fate_table(df, firm).to_string(float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
