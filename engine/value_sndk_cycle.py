"""
value_sndk_cycle.py -- SNDK through the cycle-based DCF (cycle_dcf.py).

Market data, balance-sheet bridge, discount-rate inputs and consensus for the first two years come from the
engine's own SNDK inputs file, so the two valuations share facts and differ only in method. The cycle
assumptions below are judgements, written out so they can be argued with.

    python value_sndk_cycle.py ../sndk/SNDK_inputs.json [out_dir]
    python value_sndk_cycle.py --attach ../sndk/SNDK_inputs.json   # put the assumptions in the inputs file for the page
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

import cycle_dcf as cd

# Net PP&E at FY26 year end ($M), added to opening net working capital as the invested capital to start from.
PPE = 674.0

# Engine Base DCF per share for the same date (SNDK_report 2026-11-05), shown on the chart for reference.
ENGINE_BASE_DCF = 1769.23


def _sc(**kw):
    return kw


# ---------------------------------------------------------------- cycle assumptions
# Years are fiscal years from FY27 (year 1). Phase A is the consensus boom (two years, fixed by the Street);
# phase B is the downturn and recovery; phase C is the mature business that the terminal value continues.
SCENARIOS = {
    "Bear": _sc(
        gA0=1.20,
        gA1=0.00,
        extA=0,
        gP=0.0,
        LB=5,
        gB0=-0.35,
        gB1=0.04,
        gC0=0.05,
        gT=0.025,
        m27=0.76,
        m28=0.65,
        trough=-0.02,
        mid=0.15,
        s2c0=4.0,
        s2cT=2.5,
        beta0=2.5,
        betaT=1.9,
        excess=0.0,
    ),
    "Base": _sc(
        gA0=1.448,
        gA1=0.223,
        extA=0,
        gP=0.05,
        LB=4,
        gB0=-0.20,
        gB1=0.08,
        gC0=0.09,
        gT=0.03,
        m27=0.80,
        m28=0.82,
        trough=0.08,  # above Micron's median downturn low (1%): long-term contracts with minimum revenue
        mid=0.22,  # above Micron's FY09-FY25 median (18.5%): AI storage demand; below its 75th percentile (26%)
        s2c0=5.0,
        s2cT=3.0,
        beta0=2.3,
        betaT=1.6,
        excess=0.03,
    ),
    "Bull": _sc(
        gA0=1.55,
        gA1=0.35,
        extA=2,
        gP=0.10,
        LB=3,
        gB0=-0.05,
        gB1=0.12,
        gC0=0.12,
        gT=0.035,
        m27=0.82,
        m28=0.84,
        trough=0.35,
        mid=0.45,
        s2c0=6.0,
        s2cT=3.5,
        beta0=2.2,
        betaT=1.3,
        excess=0.05,
    ),
}

# Error in the model itself, added in quadrature to the Monte Carlo spread (log scale). The same 0.5 as MC_MODEL_ERR in
# valuation_v2.py, which the point-in-time backtest calibrated on 587 valuations of large companies. Nothing here has
# been calibrated on memory makers; the backtest found even the main model's corrected band about 1.2 times too
# narrow, so 0.5 is a floor, not a conservative figure.
MODEL_ERROR = 0.5

# One distribution per input (centre = Base). The copula ties related inputs together.
DISTS = {
    "gA0": ("tri", 1.15, 1.45, 1.65),
    "gA1": ("tri", -0.05, 0.223, 0.40),
    "extA": ("tri", 0.0, 0.0, 2.0),  # years the boom runs past the Street's two (rounded)
    "gP": ("tri", 0.0, 0.05, 0.12),
    "LB": ("tri", 3.0, 4.0, 6.0),
    "gB0": ("tri", -0.45, -0.20, -0.02),
    "gB1": ("tri", 0.0, 0.08, 0.18),
    "gC0": ("tri", 0.04, 0.09, 0.14),
    "gT": ("tri", 0.02, 0.03, 0.04),
    "m27": ("tri", 0.72, 0.80, 0.84),
    "m28": ("tri", 0.60, 0.82, 0.86),
    "trough": ("tri", -0.20, 0.08, 0.35),  # Micron's downturn lows ran from -37% to 14%
    "mid": ("tri", 0.10, 0.22, 0.42),  # Micron mean 12%, median 18.5%, 75th percentile 26%
    "s2c0": ("tri", 3.5, 5.0, 7.0),
    "s2cT": ("tri", 2.0, 3.0, 4.5),
    "beta0": ("norm", 2.3, 0.2),
    "betaT": ("tri", 1.2, 1.6, 2.1),
    "rf": ("norm", 0.04871, 0.004),
    "erp": ("norm", 0.0423, 0.004),
    "excess": ("tri", 0.0, 0.03, 0.06),
}
CORR = [
    ("gB0", "trough", 0.6),  # a deep revenue fall comes with a deep margin fall
    ("trough", "mid", 0.5),
    ("mid", "excess", 0.5),  # a business that earns more through the cycle also earns more on new capital
    ("gA1", "m28", 0.4),
    (
        "extA",
        "gB0",
        -0.4,
    ),  # the longer the boom, the harder the fall  # a stronger boom runs at a higher peak margin
    ("m28", "gB0", -0.3),  # the higher the peak, the harder the fall
    ("gT", "rf", 0.6),  # growth capped by the rate environment
    ("betaT", "beta0", 0.3),
]


def cycle_block() -> dict:
    """The assumptions as they are stored in the inputs file, where the page reads them from."""
    return {
        "scenarios": SCENARIOS,
        "dists": {k: list(v) for k, v in DISTS.items()},
        "corr": [list(c) for c in CORR],
        "model_error": MODEL_ERROR,
        "ppe": PPE,
    }


def build(x: dict, inp: dict) -> dict:
    """Turn one set of assumptions `x` (a SCENARIOS entry or a Monte Carlo draw) into a cycle_dcf spec."""
    mk, br, cap, tm = inp["market"], inp["bridge"], inp["capital"], inp["timing"]
    LB, XA = round(x["LB"]), round(x["extA"])
    peak_end = 2 + XA
    trough_year, mid_year = peak_end + LB - 1, peak_end + LB + 3
    shares = mk["shares_basic"] + mk["dilution"]
    n = peak_end + LB + 8
    return {
        "base_revenue": inp["rev_y0"],
        "cycles": [
            {"length": 2, "g0": x["gA0"], "g1": x["gA1"]},
            *([{"length": XA, "g0": x["gP"], "g1": x["gP"]}] if XA else []),
            {"length": LB, "g0": x["gB0"], "g1": x["gB1"]},
            {"length": n - peak_end - LB, "g0": x["gC0"], "g1": x["gT"]},
        ],
        "margin": {
            "knots": [
                (1, x["m27"]),
                (2, x["m28"]),
                (peak_end, x["m28"]),
                (trough_year, x["trough"]),
                (mid_year, x["mid"]),
            ]
        },
        "tax": {"knots": [(1, inp["operating"]["tax0"]), (5, inp["operating"]["tax_lr"])]},
        "mtr": cap["tax_marginal"],
        "terminal_tax": inp["operating"]["tax_lr"],
        "sales_to_capital": {"start": x["s2c0"], "end": x["s2cT"]},
        "liquidation": 0.0,
        "invested_capital": inp["operating"]["nwc0"]
        + inp.get("cycle", {}).get("ppe", PPE),  # opening NWC plus net PP&E
        "equity": mk["price"] * shares,
        "debt": br["core_debt"] + br["leases"],
        "beta": {"knots": [(1, x["beta0"]), (3, x["beta0"]), (10, x["betaT"])]},
        "kd": {"start": cap["kd_pretax"], "end": cap["kd_pretax"]},
        "rf": x.get("rf", cap["rf"]),
        "erp": x.get("erp", cap["erp"]),
        "excess": x["excess"],
        "cash": br["cash"],
        "other_claims": br["nci"],
        "shares": shares,
        "timing": tm,
    }


def implied_mid_cycle_margin(inp: dict, base: dict, target_per_share: float) -> float | None:
    """Mid-cycle margin at which Base value per share equals `target_per_share` (bisection)."""
    lo, hi = -0.2, 0.9

    def f(m):
        return cd.value(build({**base, "mid": m}, inp))["per_share"] - target_per_share

    if f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def run(inp: dict, size: int = 20000, seed: int = 1) -> dict:
    price = inp["market"]["price"]
    shares = inp["market"]["shares_basic"] + inp["market"]["dilution"]
    cap = price * shares
    scen = {k: cd.value(build(v, inp)) for k, v in SCENARIOS.items()}
    base = SCENARIOS["Base"]
    mc = cd.monte_carlo(lambda x: build(x, inp), DISTS, CORR, size=size, seed=seed, model_error=MODEL_ERROR)
    ps = mc["per_share"]
    sens_mid = {
        f"{m:.0%}": cd.value(build({**base, "mid": m}, inp))["per_share"]
        for m in (0.10, 0.15, 0.185, 0.22, 0.28, 0.35, 0.45)
    }
    sens_beta = {
        f"{b:.1f}": cd.value(build({**base, "betaT": b}, inp))["per_share"] for b in (1.2, 1.4, 1.6, 1.9, 2.3)
    }
    sens_len = {str(xa): cd.value(build({**base, "extA": xa}, inp))["per_share"] for xa in (0, 1, 2, 3)}
    return {
        "price": price,
        "shares": shares,
        "market_cap": cap,
        "consensus_eps": [inp["consensus"]["eps"][k] for k in ("FY27", "FY28")],
        "scenarios": scen,
        "mc": mc,
        "percentiles": cd.percentile_table(mc["equity"], cap, shares),
        "p_above_price": float((ps > price).mean()),
        "mean_per_share": float(ps.mean()),
        "drivers": cd.drivers(mc["inputs"], mc["equity"]),
        "implied_mid_margin": implied_mid_cycle_margin(inp, base, price),
        "sens_mid": sens_mid,
        "sens_beta": sens_beta,
        "sens_cycle_length": sens_len,
    }


def summarise(res: dict) -> str:
    L = []
    L.append(f"SNDK cycle DCF   price ${res['price']:,.2f}   market cap ${res['market_cap'] / 1000:,.1f}B\n")
    for k, v in res["scenarios"].items():
        r = v["revenue"]
        L.append(
            f"{k:<5} value/share ${v['per_share']:>8,.0f}   upside {v['per_share'] / res['price'] - 1:+.0%}   "
            f"terminal value {v['tv_share']:.0%} of EV   FY28 rev {r[1] / 1000:,.1f}B  trough rev {r.min() / 1000:,.1f}B"
            f"  WACC {v['wacc'][0]:.1%}->{v['wacc'][-1]:.1%}"
        )
    b = res["scenarios"]["Base"]
    sh = res["shares"]
    ce = res["consensus_eps"]
    L.append(
        "Base tie-out to the Street (NOPAT per share, before interest income): "
        + ", ".join(
            f"FY{27 + i} ${b['nopat'][i] / sh:,.0f} vs consensus EPS ${e:,.0f}" for i, e in enumerate(ce)
        )
    )
    L.append("")
    p = res["percentiles"]
    L.append(
        f"Monte Carlo ({len(res['mc']['equity'])} draws, {res['mc']['dropped']} dropped; "
        f"spread widened x{res['mc']['widen']:.2f} for model error {MODEL_ERROR})"
    )
    L.append("  pct   per share   vs price")
    for r in p:
        L.append(f"  {r['pct']:>3}   ${r['per_share']:>8,.0f}   {r['per_share'] / res['price'] - 1:+.0%}")
    L.append(f"  P(value > price) {res['p_above_price']:.0%}   mean ${res['mean_per_share']:,.0f}")
    L.append("  rank correlation with value: " + ", ".join(f"{k} {v:+.2f}" for k, v in res["drivers"][:8]))
    im = res["implied_mid_margin"]
    L.append(
        f"\nMid-cycle EBIT margin that the ${res['price']:,.0f} price implies (Base otherwise): "
        + (f"{im:.1%}" if im is not None else "outside -20%..90%")
    )
    L.append(
        "Base value/share by mid-cycle margin: "
        + ", ".join(f"{k} ${v:,.0f}" for k, v in res["sens_mid"].items())
    )
    L.append(
        "Base value/share by terminal beta:    "
        + ", ".join(f"{k} ${v:,.0f}" for k, v in res["sens_beta"].items())
    )
    L.append(
        "Base value/share by extra boom years: "
        + ", ".join(f"+{k}y ${v:,.0f}" for k, v in res["sens_cycle_length"].items())
    )
    return "\n".join(L)


def to_json(res: dict) -> dict:
    def row(v):
        return {
            k: (v[k].tolist() if isinstance(v[k], np.ndarray) else v[k])
            for k in ("revenue", "growth", "margin", "ebit", "nopat", "reinvestment", "fcff", "invested_capital",
                      "roic", "beta", "wacc", "pv")
        } | {k: v[k] for k in ("ev", "equity", "per_share", "tv_share", "pv_explicit", "pv_terminal")}  # fmt: skip

    return {
        "price": res["price"],
        "shares": res["shares"],
        "consensus_eps": res["consensus_eps"],
        "scenarios": {k: row(v) for k, v in res["scenarios"].items()},
        "percentiles": res["percentiles"],
        "p_above_price": res["p_above_price"],
        "model_error": MODEL_ERROR,
        "widen": res["mc"]["widen"],
        "drivers": res["drivers"],
        "implied_mid_margin": res["implied_mid_margin"],
        "sensitivity": {
            "mid": res["sens_mid"],
            "terminal_beta": res["sens_beta"],
            "cycle_length": res["sens_cycle_length"],
        },
        "assumptions": {"scenarios": SCENARIOS, "distributions": DISTS, "correlations": CORR},
    }


def plot(res: dict, path: pathlib.Path, engine_values: dict | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"Bear": "#b3402a", "Base": "#2f5d8a", "Bull": "#2f7d4f"}
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2), dpi=150)
    for k, v in res["scenarios"].items():
        yrs = np.arange(27, 27 + v["n"])
        ax[0].plot(yrs, v["revenue"] / 1000, color=colors[k], label=k)
        ax[1].plot(yrs, v["margin"] * 100, color=colors[k], label=k)
    ax[0].set(title="Revenue ($B)")
    ax[1].set(title="EBIT margin (%)")
    for a in ax[:2]:
        a.set_xticks(range(27, 43, 3), [f"FY{y}" for y in range(27, 43, 3)])
        a.legend(frameon=False)
        a.grid(alpha=0.25)
    ps = res["mc"]["per_share"]
    hi = float(np.percentile(ps, 99.5))
    ax[2].hist(ps, bins=np.linspace(0, hi, 80), color="#9db7d1")
    ax[2].set_xlim(0, max(hi, (engine_values or {}).get("dcf", 0) * 1.03))
    ax[2].axvline(res["price"], color="black", ls="--", label=f"Price ${res['price']:,.0f}")
    ax[2].axvline(float(np.median(ps)), color="#2f5d8a", label=f"Median ${np.median(ps):,.0f}")
    if engine_values:
        ax[2].axvline(
            engine_values["dcf"],
            color="#b3402a",
            ls=":",
            label=f"Engine Base DCF ${engine_values['dcf']:,.0f}",
        )
    ax[2].set(title=f"Value per share, {len(ps):,} draws")
    ax[2].legend(frameon=False, fontsize=8)
    for a in ax:
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def attach(src: pathlib.Path) -> None:
    """Write the cycle assumptions into an inputs file; the page shows its Cycle tab when they are there."""
    inp = json.loads(src.read_text())
    inp["cycle"] = cycle_block()
    src.write_text(json.dumps(inp, indent=1))


def main(argv: list[str]) -> None:
    if argv[1:2] == ["--attach"]:
        attach(pathlib.Path(argv[2]))
        print(f"cycle assumptions written to {argv[2]}")
        return
    src = pathlib.Path(argv[1] if len(argv) > 1 else "../sndk/SNDK_inputs.json")
    inp = json.loads(src.read_text())
    res = run(inp)
    print(summarise(res))
    out = pathlib.Path(argv[2]) if len(argv) > 2 else src.parent
    (out / "SNDK_cycle_dcf.json").write_text(json.dumps(to_json(res), indent=1))
    print(f"\nWritten: {out / 'SNDK_cycle_dcf.json'}")
    try:
        plot(res, out / "SNDK_cycle_dcf.png", {"dcf": ENGINE_BASE_DCF})
        print(f"Written: {out / 'SNDK_cycle_dcf.png'}")
    except ImportError:
        pass


if __name__ == "__main__":
    main(sys.argv)
