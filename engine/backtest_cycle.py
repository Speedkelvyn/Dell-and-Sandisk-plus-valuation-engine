# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
backtest_cycle.py -- a point-in-time test of the cycle DCF on one memory maker.

At each origin date the rule below builds a cycle valuation using only what was known then: the filings filed by that
day, the price and Treasury yield that day, and the company's own annual operating margins up to its latest fiscal
year (from sndk/MEMORY_CYCLE_HISTORY.csv, which comes from the same SEC filings). The rule is fixed in advance, with no
tuning to the outcome, so this is a check on the method and not on a forecast.

The rule reads where the latest margin sits in the company's own history:
  * at or above the 75th percentile (a peak): one more year at the current margin, then a three-year downturn to the
    25th-percentile margin, then recovery to the median margin
  * at or below the 25th percentile (a trough): recovery to the median margin over three years
  * between: glide to the median margin over three years
Growth follows the same shape. Every other input is read from the filings or set once below.

    python backtest_cycle.py ../backtest/data MU ../sndk/MEMORY_CYCLE_HISTORY.csv [quarterly]

It only reads files already stored, so it runs in the sandbox. With ten origins from one company the output is a
table to read, not a statistical test.
"""

from __future__ import annotations

import csv
import pathlib
import sys

import numpy as np

import backtest as bt
import cycle_dcf as cd

ERP = 0.045  # equity risk premium
KD = 0.05  # pre-tax cost of debt
MTR = 0.21  # marginal tax rate for the debt shield
TAX = 0.15  # long-run tax on operating income
BETA0, BETA_T = 1.4, 1.0  # unlevered beta now and at maturity
EXCESS = 0.03  # terminal ROIC above WACC
YEARS = 11


def pct(xs: list[float], q: float) -> float:
    return float(np.percentile(xs, q))


def history(csv_path: pathlib.Path, company: str, asof: str) -> list[tuple[str, float, float]]:
    """(fiscal_year_end, revenue, margin) for fiscal years ended at least 60 days before asof."""
    out = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            if r["company"] != company:
                continue
            end = r["fiscal_year_end"] + "-28"
            if end <= (np.datetime64(asof) - np.timedelta64(60, "D")).astype(str):
                out.append((r["fiscal_year_end"], float(r["revenue_usd_m"]), float(r["operating_margin"])))
    return out


def spec_from_rule(hist, f, tnx_pct: float, flat: bool = False) -> dict:
    """The cycle spec the rule gives for one origin. `flat` ignores the cycle and holds the median margin (baseline)."""
    rev = [h[1] for h in hist]
    margins = [h[2] for h in hist]
    m0, med, lo = margins[-1], float(np.median(margins)), pct(margins, 25)
    g_last = rev[-1] / rev[-2] - 1 if len(rev) > 1 else 0.05
    a = f["annual"]

    def last(tag):
        rows = a.get(tag) or []
        return rows[-1][1] / 1e6 if rows else 0.0

    cash, debt, equity_book = last("CashAndCashEquivalents"), last("TotalDebt"), last("CommonStockEquity")
    ic = max(debt + equity_book - cash, 1.0)
    s2c = float(np.clip(rev[-1] / ic, 0.4, 1.5))
    price = f["summary"]["price"]["regularMarketPrice"]
    shares = f["summary"]["defaultKeyStatistics"]["sharesOutstanding"] / 1e6
    if flat:
        cycles = [{"length": 3, "g0": 0.08, "g1": 0.05}, {"length": YEARS - 3, "g0": 0.05, "g1": 0.03}]
        knots = [(1, med)]
    elif m0 >= pct(margins, 75):
        cycles = [
            {"length": 1, "g0": max(g_last, 0.0) * 0.5, "g1": max(g_last, 0.0) * 0.5},
            {"length": 3, "g0": -0.15, "g1": 0.0},
            {"length": YEARS - 4, "g0": 0.08, "g1": 0.03},
        ]
        knots = [(1, m0), (3, lo), (7, med)]
    elif m0 <= lo:
        cycles = [{"length": 3, "g0": 0.12, "g1": 0.08}, {"length": YEARS - 3, "g0": 0.07, "g1": 0.03}]
        knots = [(1, m0), (3, med)]
    else:
        cycles = [{"length": 3, "g0": 0.08, "g1": 0.05}, {"length": YEARS - 3, "g0": 0.05, "g1": 0.03}]
        knots = [(1, m0), (4, med)]
    return {
        "base_revenue": rev[-1],
        "cycles": cycles,
        "margin": {"knots": knots},
        "tax": {"knots": [(1, TAX)]},
        "mtr": MTR,
        "terminal_tax": TAX,
        "sales_to_capital": {"start": s2c, "end": s2c},
        "liquidation": 0.0,
        "invested_capital": ic,
        "equity": price * shares,
        "debt": debt,
        "beta": {"knots": [(1, BETA0), (3, BETA0), (10, BETA_T)]},
        "kd": {"start": KD, "end": KD},
        "rf": tnx_pct / 100,
        "erp": ERP,
        "excess": EXCESS,
        "cash": cash,
        "other_claims": last("MinorityInterest"),
        "shares": shares,
    }


def run(
    data_dir: str, ticker: str, csv_path: str, years=range(2017, 2025), month_day="-11-15", origins=None
) -> list[dict]:
    d = pathlib.Path(data_dir)
    facts = bt._read(d / f"{ticker}_edgar.json.gz")
    px, idx, tnx = (bt.Prices(bt._read(d / f"{n}_px.json.gz")) for n in (ticker, "GSPC", "TNX"))
    out = []
    for asof in origins or [f"{y}{month_day}" for y in years]:
        h = history(pathlib.Path(csv_path), ticker, asof)
        f = bt.feeds_at(ticker, facts, px, idx, tnx, asof)
        if f is None or len(h) < 5:
            out.append({"asof": asof, "error": "too thin"})
            continue
        row = {
            "asof": asof,
            "fy": h[-1][0],
            "margin": h[-1][2],
            "price": f["summary"]["price"]["regularMarketPrice"],
        }
        for key, flat in (("cycle", False), ("flat", True)):
            try:
                s = spec_from_rule(h, f, f["tnx"], flat=flat)
                row[key] = cd.value(s)["per_share"]
            except ValueError as e:
                row[key] = None
                row[f"{key}_err"] = str(e)
        for m in (12, 24, 36):
            row[f"ret{m}"] = px.forward_return(asof, m)
        out.append(row)
    return out


def table(rows: list[dict]) -> str:
    L = [
        f"{'origin':11s}{'FY margin':>10s}{'price':>8s}{'cycle':>8s}{'up':>7s}{'flat':>8s}{'up':>7s}{'12m':>7s}{'24m':>7s}{'36m':>7s}"
    ]

    def pc(x):
        return "   n/a" if x is None else f"{x * 100:6.0f}%"

    for r in rows:
        if "error" in r:
            L.append(f"{r['asof']:11s} {r['error']}")
            continue
        c, fl = r["cycle"], r["flat"]
        L.append(
            f"{r['asof']:11s}{r['margin'] * 100:9.0f}%{r['price']:8.1f}"
            f"{(c or 0):8.1f}{pc(None if c is None else c / r['price'] - 1)}"
            f"{(fl or 0):8.1f}{pc(None if fl is None else fl / r['price'] - 1)}"
            f"{pc(r['ret12'])}{pc(r['ret24'])}{pc(r['ret36'])}"
        )
    return "\n".join(L)


def rank_corr(rows, key, horizon) -> float | None:
    pts = [(r[key] / r["price"] - 1, r[horizon]) for r in rows if r.get(key) and r.get(horizon) is not None]
    if len(pts) < 5:
        return None
    return bt.spearman([p[0] for p in pts], [p[1] for p in pts])


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print(__doc__)
        return 2
    quarterly = len(argv) == 5 and argv[4] == "quarterly"
    rows = run(
        argv[1], argv[2], argv[3], origins=bt.quarter_ends("2017-12-31", "2025-09-30") if quarterly else None
    )
    print(table(rows))
    for key in ("cycle", "flat"):
        print(key, {h: rank_corr(rows, key, h) for h in ("ret12", "ret24", "ret36")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
