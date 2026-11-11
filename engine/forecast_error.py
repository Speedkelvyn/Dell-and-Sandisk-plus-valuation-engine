# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
forecast_error.py -- how well do simple rules forecast next year's revenue growth and operating margin?

Price-based tests of a valuation need years of data to detect a small edge. Fundamentals are easier to check: every
firm-year gives a forecast and an outcome. This builds a panel from the stored SEC filings (backtest/data) and scores
rules by mean absolute error, walk-forward for the regressions (fit on earlier years, score on the next).

    python forecast_error.py ../backtest/data

Findings on 937 firm-years of 128 large US companies, 2017 to 2025:
  * Revenue growth: carrying last year's growth forward misses by 15.2 points on average. Pulling it 75% of the way
    to the 6.5% median (weight 0.25 on the company's own growth) misses by 11.4. The shrunk rule beats carrying
    growth forward in 7 of 9 target years and never loses. The fitted weight is about 0.11 and unstable year to year.
  * Operating margin: last year's margin is the best simple forecast (6.0 points); a regression on two lags (6.6) and
    the cross-sectional median (11.6) are worse.
research.py uses the first result when analysts' estimates are missing.
"""

from __future__ import annotations

import collections
import pathlib
import sys

import edgar
import numpy as np

import backtest as bt

CENTRE = 0.065


def panel(data_dir: pathlib.Path) -> list[dict]:
    rows = []
    for p in sorted(data_dir.glob("*_edgar.json.gz")):
        t = p.name.split("_")[0]
        try:
            a = edgar.parse(bt._read(p), since=bt.SINCE)["annual"]
        except (ValueError, KeyError):
            continue
        rv = {r[0][:4]: r[1] for r in a.get("TotalRevenue", [])}
        oi = {r[0][:4]: r[1] for r in a.get("OperatingIncome", [])}
        for y in sorted(set(rv) & set(oi)):
            n, p0 = str(int(y) + 1), str(int(y) - 1)
            if not (n in rv and n in oi and p0 in rv and p0 in oi and min(rv[y], rv[p0], rv[n]) > 0):
                continue
            rows.append(
                {
                    "t": t,
                    "y": int(y),
                    "g": float(np.clip(rv[y] / rv[p0] - 1, -0.5, 1.5)),
                    "g1": float(np.clip(rv[n] / rv[y] - 1, -0.5, 1.5)),
                    "m": float(np.clip(oi[y] / rv[y], -1, 0.8)),
                    "m1": float(np.clip(oi[n] / rv[n], -1, 0.8)),
                    "m0": float(np.clip(oi[p0] / rv[p0], -1, 0.8)),
                }
            )
    return rows


def walk_forward(rows, target: str, feats: list[str], base: str) -> dict[str, float]:
    """Mean absolute error of persistence, the training median, a regression and a half-and-half blend."""
    err = collections.defaultdict(list)
    for y in sorted({r["y"] for r in rows})[2:]:
        tr = [r for r in rows if r["y"] < y]
        te = [r for r in rows if r["y"] == y]
        if len(tr) < 80 or len(te) < 20:
            continue
        X = np.array([[1] + [r[f] for f in feats] for r in tr])
        Y = np.array([r[target] for r in tr])
        b = np.linalg.lstsq(X, Y, rcond=None)[0]
        Xt = np.array([[1] + [r[f] for f in feats] for r in te])
        Yt = np.array([r[target] for r in te])
        pers = np.array([r[base] for r in te])
        err["persistence"].append(np.abs(pers - Yt))
        err["median"].append(np.abs(np.median(Y) - Yt))
        err["regression"].append(np.abs(Xt @ b - Yt))
        err["blend"].append(np.abs(0.5 * (Xt @ b) + 0.5 * pers - Yt))
    return {k: float(np.mean(np.concatenate(v))) for k, v in err.items()}


def shrink_error(rows, weights=(0.0, 0.11, 0.25, 0.5, 0.75, 1.0)) -> dict[float, float]:
    te = [r for r in rows if r["y"] >= 2018]
    g, g1 = np.array([r["g"] for r in te]), np.array([r["g1"] for r in te])
    return {w: float(np.mean(np.abs(CENTRE + w * (g - CENTRE) - g1))) for w in weights}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    rows = panel(pathlib.Path(argv[1]))
    print(f"{len(rows)} firm-years, {len({r['t'] for r in rows})} firms")
    print("next-year margin (feats: this year, last year):", walk_forward(rows, "m1", ["m", "m0"], "m"))
    print("next-year growth (feat: this year):", walk_forward(rows, "g1", ["g"], "g"))
    print("growth, weight on own growth -> MAE:", shrink_error(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
