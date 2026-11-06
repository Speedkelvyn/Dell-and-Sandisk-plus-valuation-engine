"""
cycle_dcf.py -- cycle-based FCFF DCF with copula Monte Carlo.

For businesses whose economics move in cycles (memory, semis equipment, commodities), where one fade from
today's margin to a long-run margin hides the part that matters: how long the boom lasts, how deep the
downturn goes and what a normal year looks like afterwards.

What it adds to valuation_v2.py:
  * revenue growth in cycles: each cycle has a length, a start rate and an end rate
  * margin path through any number of turning points (peak, trough, mid-cycle)
  * discount rate that moves with the business: beta and cost of debt glide to a mature level, and the
    discount factor compounds the yearly rates
  * reinvestment from sales-to-capital, invested capital and ROIC tracked every year; terminal reinvestment
    from the spread of terminal ROIC over WACC
  * Monte Carlo with one distribution per input, tied together by a Gaussian copula so related inputs
    (a deep downturn and a low trough margin) move together

The method follows Damodaran's FCFF framework. The year-by-year glides and the per-input distributions
with a copula follow a public Colab DCF notebook; the code here is a separate implementation.

Units are whatever the caller uses (millions for SNDK); only ratios and per-share values are unit-free.
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import NormalDist

import numpy as np

_ND = NormalDist()
_PHI = np.vectorize(_ND.cdf)
_PPF = np.vectorize(_ND.inv_cdf)


def glide(start: float, end: float, n: int, hold: int = 1) -> np.ndarray:
    """n values: `start` held for hold-1 years, then a straight line from start to end ending in year n."""
    n = int(n)
    hold = max(1, min(int(hold), n))
    return np.concatenate([np.full(hold - 1, float(start)), np.linspace(start, end, n - hold + 1)])


def knot_path(knots: list[tuple[float, float]], n: int) -> np.ndarray:
    """Straight lines through (year, value) turning points; flat before the first and after the last."""
    xs, ys = zip(*sorted(knots), strict=True)
    return np.interp(np.arange(1, n + 1), xs, ys)


def _path(p: dict, n: int) -> np.ndarray:
    """A yearly path: {"knots": [(year, value), ...]} or {"start", "end", "hold"} for a glide."""
    return knot_path(p["knots"], n) if "knots" in p else glide(p["start"], p["end"], n, p.get("hold", 1))


def growth_path(cycles: list[dict]) -> np.ndarray:
    return np.concatenate([glide(c["g0"], c["g1"], c["length"], c.get("hold", 1)) for c in cycles])


def value(spec: dict) -> dict:
    """Value one scenario. `spec` keys are listed in the docstring of `example_spec`."""
    g = growth_path(spec["cycles"])
    n = len(g)
    rev = spec["base_revenue"] * np.cumprod(1 + g)
    margin = _path(spec["margin"], n)
    mtr = spec["mtr"]
    tax = _path(spec["tax"], n)
    s2c = _path(spec["sales_to_capital"], n)

    ebit = rev * margin
    nopat = ebit * (1 - tax)
    d_rev = np.diff(np.concatenate([[spec["base_revenue"]], rev]))
    reinv = d_rev / s2c
    reinv = np.where(reinv > 0, reinv, reinv * spec.get("liquidation", 0.0))
    fcff = nopat - reinv
    ic = spec["invested_capital"] + np.cumsum(reinv)
    roic = nopat / ic

    # discount rate by year: unlevered beta relevered at today's market weights
    E, D = spec["equity"], spec["debt"]
    we, wd = E / (E + D), D / (E + D)
    b = spec["beta"]
    beta = _path(b, n) * (1 + (1 - mtr) * D / E)
    kd = _path(spec["kd"], n) * (1 - mtr)
    ke = spec["rf"] + beta * spec["erp"]
    w = we * ke + wd * kd

    tm = spec.get("timing", {})
    fp, stub, mid = tm.get("first_period", 1.0), tm.get("stub", 1.0), tm.get("mid_year", False)
    # period j covers (fp + j - 1, fp + j]; the first covers (0, fp]. Discount factor compounds each period's rate.
    ends = fp + np.arange(n)
    starts = np.concatenate([[0.0], ends[:-1]])
    lw = np.log1p(w)

    def df(time: float) -> float:
        return float(np.exp(-np.sum(lw * np.clip(time - starts, 0.0, ends - starts))))

    frac = np.where(np.arange(n) == 0, stub, 1.0)
    times = np.maximum(ends - 0.5 * frac if mid else ends, 0.0)
    dfs = np.array([df(x) for x in times])
    pv = fcff * frac * dfs

    gT = spec["cycles"][-1]["g1"]
    wT = float(w[-1])
    if gT >= wT:
        raise ValueError("terminal growth must be below the terminal discount rate")
    roic_T = wT + spec["excess"]
    rr_T = 0.0 if gT < 0 else gT / roic_T
    tax_T = spec.get("terminal_tax", mtr)
    nopat_T = rev[-1] * (1 + gT) * margin[-1] * (1 - tax_T)
    tv = nopat_T * (1 - rr_T) / (wT - gT)
    t_n = fp + n - 1
    pv_tv = tv * df(t_n - 0.5 if mid else t_n)

    ev = float(pv.sum() + pv_tv)
    equity = ev + spec["cash"] - spec["debt"] - spec.get("other_claims", 0.0)
    return {
        "n": n,
        "revenue": rev,
        "growth": g,
        "margin": margin,
        "ebit": ebit,
        "tax": tax,
        "nopat": nopat,
        "reinvestment": reinv,
        "fcff": fcff,
        "invested_capital": ic,
        "roic": roic,
        "beta": beta,
        "wacc": w,
        "df": dfs,
        "pv": pv,
        "pv_explicit": float(pv.sum()),
        "pv_terminal": float(pv_tv),
        "terminal_value": float(tv),
        "terminal_roic": roic_T,
        "ev": ev,
        "equity": equity,
        "per_share": equity / spec["shares"] if spec.get("shares") else None,
        "tv_share": float(pv_tv) / ev if ev else float("nan"),
    }


# ------------------------------------------------------------------ Monte Carlo
def _ppf(spec: tuple, u: np.ndarray) -> np.ndarray:
    kind = spec[0]
    if kind == "uni":
        return spec[1] + (spec[2] - spec[1]) * u
    if kind == "norm":
        return spec[1] + spec[2] * _PPF(np.clip(u, 1e-12, 1 - 1e-12))
    if kind == "tri":
        lo, mode, hi = spec[1:]
        c = (mode - lo) / (hi - lo)
        return np.where(
            u < c, lo + np.sqrt(u * (hi - lo) * (mode - lo)), hi - np.sqrt((1 - u) * (hi - lo) * (hi - mode))
        )
    raise ValueError(f"unknown distribution {kind!r}")


def _psd(R: np.ndarray) -> np.ndarray:
    """Nearest valid correlation matrix: clip negative eigenvalues, rescale the diagonal back to 1."""
    vals, vecs = np.linalg.eigh(R)
    if vals.min() > 1e-8:
        return R
    R2 = vecs @ np.diag(np.clip(vals, 1e-6, None)) @ vecs.T
    d = np.sqrt(np.diag(R2))
    return R2 / np.outer(d, d)


def draw(
    dists: dict[str, tuple], corr: list[tuple[str, str, float]], size: int, seed: int | None = None
) -> dict:
    """Sample every input from its own distribution, tied by a Gaussian copula.
    dists: name -> ("tri", lo, mode, hi) | ("uni", lo, hi) | ("norm", mean, sd)
    corr: (name_a, name_b, correlation of the underlying normals)."""
    names = list(dists)
    idx = {k: i for i, k in enumerate(names)}
    R = np.eye(len(names))
    for a, b, r in corr:
        R[idx[a], idx[b]] = R[idx[b], idx[a]] = r
    L = np.linalg.cholesky(_psd(R))
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((size, len(names))) @ L.T
    u = _PHI(z)
    return {k: _ppf(dists[k], u[:, i]) for k, i in idx.items()}


def monte_carlo(
    build: Callable[[dict], dict],
    dists: dict[str, tuple],
    corr: list[tuple[str, str, float]],
    size: int = 5000,
    seed: int | None = 1,
) -> dict:
    """Value `size` draws. `build` turns one draw (name -> float) into a spec for `value`.
    Draws that cannot be valued (terminal growth at or above the discount rate) are counted and dropped."""
    s = draw(dists, corr, size, seed)
    eq, ps, tvs, bad = [], [], [], 0
    keep = np.zeros(size, dtype=bool)
    for i in range(size):
        try:
            v = value(build({k: float(a[i]) for k, a in s.items()}))
        except ValueError:
            bad += 1
            continue
        keep[i] = True
        eq.append(v["equity"])
        ps.append(v["per_share"])
        tvs.append(v["tv_share"])
    return {
        "inputs": {k: a[keep] for k, a in s.items()},
        "equity": np.array(eq),
        "per_share": np.array(ps),
        "tv_share": np.array(tvs),
        "dropped": bad,
    }


def percentile_table(equity: np.ndarray, market_cap: float, shares: float) -> list[dict]:
    rows = []
    for p in range(0, 101, 10):
        e = float(np.percentile(equity, p))
        rows.append(
            {
                "pct": p,
                "equity": e,
                "per_share": e / shares,
                "price_to_value": market_cap / e if e > 0 else float("inf"),
                "upside": e / market_cap - 1,
            }
        )
    return rows


def _rank(a: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(a)).astype(float)


def drivers(inputs: dict[str, np.ndarray], equity: np.ndarray) -> list[tuple[str, float]]:
    """Rank correlation of each varied input with equity value, largest magnitude first."""
    re = _rank(equity)
    out = []
    for k, a in inputs.items():
        if np.ptp(a) == 0:
            continue
        out.append((k, float(np.corrcoef(_rank(a), re)[0, 1])))
    return sorted(out, key=lambda x: -abs(x[1]))


def example_spec() -> dict:
    """The keys `value` reads (optional ones marked)."""
    return {
        "base_revenue": 100.0,
        "cycles": [{"length": 5, "g0": 0.10, "g1": 0.05, "hold": 1}, {"length": 5, "g0": 0.04, "g1": 0.03}],
        "margin": {"start": 0.15, "end": 0.20, "hold": 1},  # or {"knots": [(year, margin), ...]}
        "tax": {"start": 0.2, "end": 0.25},
        "mtr": 0.25,  # marginal tax: beta relevering, after-tax cost of debt, terminal tax unless terminal_tax
        "sales_to_capital": {"start": 2.0, "end": 1.5},
        "liquidation": 0.0,  # optional: share of reinvestment released when revenue shrinks
        "invested_capital": 50.0,
        "equity": 1000.0,
        "debt": 100.0,  # market weights and the bridge
        "beta": {"start": 1.2, "end": 1.0, "hold": 1},  # unlevered
        "kd": {"start": 0.06, "end": 0.05},  # pre-tax
        "rf": 0.04,
        "erp": 0.05,
        "excess": 0.02,  # terminal ROIC minus terminal WACC
        "cash": 20.0,
        "shares": 10.0,
        "timing": {"first_period": 1.0, "stub": 1.0, "mid_year": False},  # optional
        "other_claims": 0.0,  # optional: minorities etc.
        # optional "terminal_tax": rate on terminal NOPAT (default mtr)
    }


__all__ = [
    "draw",
    "drivers",
    "glide",
    "growth_path",
    "knot_path",
    "monte_carlo",
    "percentile_table",
    "value",
]
