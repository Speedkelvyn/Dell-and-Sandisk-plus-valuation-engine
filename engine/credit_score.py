# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
credit_score.py -- spreading, default-risk measures and a 1-to-9 risk rating from annual financial statements.

From one company-year of statements (plus the market value of equity, when there is a price) it computes

    spread ratios        leverage, interest and fixed-cost coverage, debt / EBITDA, current ratio, return on assets
    Altman Z             original (1968) Z-score for listed manufacturers and Z'' (1995) for other borrowers
    distance to default  naive Merton measure of Bharath and Shumway (2008), from equity value, equity volatility, debt
    risk rating          a scorecard of weighted points mapped to grades 1 (strong) to 9 (default imminent)

The scorecard weights and the grade bands are fixed in advance from credit practice. They are not fitted to
defaults, so the test in credit_backtest.py is a test of the idea and not of a curve fitted to the answer.

Inputs are US$ millions (any consistent unit works). Missing inputs are None or NaN; a component with a missing input is
left out and the rest are re-weighted, and a company with under 70% of the weight on offer is not rated. Without a stock
price the market component is not on offer and the rating rests on the statements alone.
"""

from __future__ import annotations

import itertools
import math

NAN = float("nan")


def _ok(*xs: float | None) -> bool:
    return all(x is not None and not math.isnan(x) for x in xs)


def _div(a: float | None, b: float | None) -> float:
    return a / b if _ok(a, b) and b != 0 else NAN


# ------------------------------------------------------------------ spreading
def spread(f: dict) -> dict:
    """Ratios from one company-year. Keys of f: at, lt, sale, ni, oibdp, oiadp, xint, act, lct, dlc, dltt, re, ceq."""
    debt = (f.get("dlc") or 0.0) + (f.get("dltt") or 0.0) if _ok(f.get("dlc"), f.get("dltt")) else NAN
    at, ebitda, xint = f.get("at"), f.get("oibdp"), f.get("xint")
    if _ok(ebitda) and ebitda <= 0:
        debt_ebitda = 99.0 if _ok(debt) and debt > 0 else 0.0
    else:
        debt_ebitda = _div(debt, ebitda)
    if _ok(debt) and debt <= 0:
        cover = 99.0  # no debt, nothing to cover
    elif _ok(xint) and xint > 0:
        cover = _div(ebitda, xint)
    else:
        cover = NAN
    return {
        "debt": debt,
        "debt_assets": _div(debt, at),
        "debt_ebitda": debt_ebitda,
        "coverage": cover,
        "current_ratio": _div(f.get("act"), f.get("lct")),
        "working_capital_assets": _div(f["act"] - f["lct"] if _ok(f.get("act"), f.get("lct")) else NAN, at),
        "retained_assets": _div(f.get("re"), at),
        "ebit_assets": _div(f.get("oiadp"), at),
        "roa": _div(f.get("ni"), at),
        "ebitda_margin": _div(ebitda, f.get("sale")),
        "asset_turnover": _div(f.get("sale"), at),
        "book_equity_liabilities": _div(f.get("ceq"), f.get("lt")),
        "log_assets": math.log(at) if _ok(at) and at > 0 else NAN,
    }


# ------------------------------------------------------------------ Altman
def altman_z(f: dict, market_equity: float | None) -> float:
    """Original Z-score: 1.2 WC/TA + 1.4 RE/TA + 3.3 EBIT/TA + 0.6 MVE/TL + 1.0 Sales/TA. Distress below 1.81, safe above 2.99."""
    s = spread(f)
    parts = (
        s["working_capital_assets"],
        s["retained_assets"],
        s["ebit_assets"],
        _div(market_equity, f.get("lt")),
        s["asset_turnover"],
    )
    if not _ok(*parts):
        return NAN
    w = (1.2, 1.4, 3.3, 0.6, 1.0)
    return sum(a * b for a, b in zip(w, parts, strict=True))


def altman_z2(f: dict) -> float:
    """Z'' for non-manufacturers and private firms: 6.56 WC/TA + 3.26 RE/TA + 6.72 EBIT/TA + 1.05 BookEq/TL.
    Distress below 1.1, safe above 2.6 (the version without the constant)."""
    s = spread(f)
    parts = (
        s["working_capital_assets"],
        s["retained_assets"],
        s["ebit_assets"],
        s["book_equity_liabilities"],
    )
    if not _ok(*parts):
        return NAN
    return sum(a * b for a, b in zip((6.56, 3.26, 6.72, 1.05), parts, strict=True))


# ------------------------------------------------------------------ distance to default
def distance_to_default(
    market_equity: float, debt_face: float, sigma_e: float, past_return: float, horizon: float = 1.0
) -> float:
    """Naive Merton distance to default (Bharath and Shumway 2008).

    F = face value of debt, E = market value of equity, sigma_e = annualised equity volatility.
    sigma_D = 0.05 + 0.25 sigma_e;  sigma_V = E/(E+F) sigma_e + F/(E+F) sigma_D;  mu = past one-year stock return.
    DD = (ln((E+F)/F) + (mu - 0.5 sigma_V^2) T) / (sigma_V sqrt(T)).   Capped to [-6, 10].
    """
    if not _ok(market_equity, debt_face, sigma_e, past_return) or market_equity <= 0 or sigma_e <= 0:
        return NAN
    if debt_face <= 0:
        return 10.0
    e, f = market_equity, debt_face
    sigma_d = 0.05 + 0.25 * sigma_e
    sigma_v = e / (e + f) * sigma_e + f / (e + f) * sigma_d
    dd = (math.log((e + f) / f) + (past_return - 0.5 * sigma_v**2) * horizon) / (sigma_v * math.sqrt(horizon))
    return max(-6.0, min(10.0, dd))


# ------------------------------------------------------------------ scorecard
# Each component: (weight, breakpoints from worst to best as (value, points)). Points are interpolated linearly and flat
# beyond the ends. Higher ratios are better, except where the breakpoints run downward (debt measures).
SCORECARD = {
    "debt_assets": (15, [(0.60, 0), (0.45, 20), (0.30, 50), (0.15, 80), (0.05, 100)]),
    "coverage": (15, [(1.0, 0), (2.0, 20), (4.0, 50), (8.0, 80), (15.0, 100)]),
    "debt_ebitda": (10, [(8.0, 0), (5.0, 20), (3.0, 50), (1.5, 80), (0.5, 100)]),
    "current_ratio": (10, [(0.6, 0), (1.0, 30), (1.5, 60), (2.2, 90), (3.0, 100)]),
    "roa": (10, [(-0.15, 0), (-0.05, 20), (0.0, 40), (0.04, 70), (0.10, 100)]),
    "retained_assets": (5, [(-0.50, 0), (-0.10, 25), (0.0, 40), (0.20, 70), (0.50, 100)]),
    "log_assets": (5, [(math.log(50), 0), (math.log(300), 40), (math.log(1500), 70), (math.log(10000), 100)]),
    "dd": (30, [(0.0, 0), (1.0, 20), (2.0, 45), (4.0, 75), (6.0, 100)]),
}
# Score floor for each grade, best first: a score at or above 85 is grade 1, and so on; below 15 is grade 9.
GRADE_FLOORS = [85, 75, 65, 55, 45, 35, 25, 15]
MIN_SHARE = 0.70  # a company needs this share of the weight on offer; the market component is on offer only with a price


def points(value: float, table: list[tuple[float, float]]) -> float:
    """Piecewise-linear points for a value; tables run worst to best and may be increasing or decreasing."""
    if not _ok(value):
        return NAN
    (x0, y0), (x1, y1) = table[0], table[-1]
    rising = x1 > x0
    if (value <= x0) if rising else (value >= x0):
        return float(y0)
    if (value >= x1) if rising else (value <= x1):
        return float(y1)
    for (xa, ya), (xb, yb) in itertools.pairwise(table):
        if (xa <= value <= xb) if rising else (xb <= value <= xa):
            return ya + (yb - ya) * (value - xa) / (xb - xa)
    return NAN


def score(features: dict) -> float:
    """Weighted points 0 to 100 from a dict holding the SCORECARD keys. The distance-to-default component is on offer only
    when it is present (a private borrower has no price); NaN if under MIN_SHARE of the weight on offer is present."""
    tot = wsum = offered = 0.0
    for k, (w, table) in SCORECARD.items():
        if k == "dd" and not _ok(features.get("dd")):
            continue
        offered += w
        p = points(features.get(k, NAN), table)
        if _ok(p):
            tot += w * p
            wsum += w
    return tot / wsum if wsum >= MIN_SHARE * offered else NAN


def grade(s: float) -> int | None:
    """1 (strongest) to 9 (weakest) from a score."""
    if not _ok(s):
        return None
    for g, floor in enumerate(GRADE_FLOORS, start=1):
        if s >= floor:
            return g
    return 9


def rate(
    f: dict,
    market_equity: float | None = None,
    sigma_e: float | None = None,
    past_return: float | None = None,
) -> dict:
    """Everything for one company-year: ratios, Z-scores, distance to default, score and grade."""
    s = spread(f)
    dd = NAN
    if _ok(market_equity, sigma_e, past_return) and _ok(s["debt"]):
        face = (f.get("dlc") or 0.0) + 0.5 * (f.get("dltt") or 0.0)
        dd = distance_to_default(market_equity, face, sigma_e, past_return)
    feats = dict(s, dd=dd)
    sc = score(feats)
    return dict(
        s, altman_z=altman_z(f, market_equity), altman_z2=altman_z2(f), dd=dd, score=sc, grade=grade(sc)
    )
