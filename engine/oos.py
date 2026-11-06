"""oos.py: does a change to the model improve it on data it was not tuned on?

    python oos.py DATA_DIR --out ../backtest/OOS_REPORT.md [--workers 2] [--mc 100]

The dates are split in advance and never moved after looking at results:
    train   as-of 2020-06-30 .. 2021-12-31   (their 12-month outcomes end by 2022-12)
    embargo as-of 2022-03-31 .. 2022-12-31   (dropped, so no train outcome overlaps a test date)
    test    as-of 2023-03-31 .. 2025-09-30
Candidate changes, fixed before the run:
    1. shrink growth: pull every company's explicit growth path toward the cross-sectional median at that date,
       weight lam in {0.25, 0.5, 0.75} on the company's own path (baseline is lam = 1)
    2. quality filter: score only companies whose earnings-quality grade is in a set (A; A or B; A to C)
    3. split by model: banks and insurers (excess return), REITs (dividend model) and the rest, scored separately
Each choice (lam, grade set) is made on the train dates by rank correlation, then scored once on the test dates.
Every candidate that was tried is in the report, whether or not it helped.
Statistic: per-date rank correlation of upside to fair value with the next 12-month excess return over the S&P 500,
averaged over dates, with a Newey-West t-statistic (lag 3, because quarterly dates with 12-month windows overlap)
and a block-bootstrap interval.
"""

from __future__ import annotations

import argparse
import copy
import multiprocessing as mp
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest as B  # noqa: E402
import research as RP  # noqa: E402

TRAIN = ("2020-06-30", "2021-12-31")
TEST = ("2023-03-31", "2025-09-30")
LAMS = (0.25, 0.5, 0.75)
GRADE_SETS = {"A": {"A"}, "A-B": {"A", "B"}, "A-C": {"A", "B", "C"}}
HORIZON = 12
MIN_NAMES = 12  # a date needs this many scored names to give a rank correlation

_U: dict = {}  # loaded once, shared with forked workers


def load_universe(data_dir, tickers=None):
    d = pathlib.Path(data_dir)
    names = tickers or sorted(p.name.split("_")[0] for p in d.glob("*_edgar.json.gz"))
    names = [t for t in names if t not in ("TE",) and (d / f"{t}_px.json.gz").exists()]
    _U["idx"], _U["tnx"] = B.Prices(B._read(d / "GSPC_px.json.gz")), B.Prices(B._read(d / "TNX_px.json.gz"))
    _U["facts"] = {t: B._read(d / f"{t}_edgar.json.gz") for t in names}
    _U["px"] = {t: B.Prices(B._read(d / f"{t}_px.json.gz")) for t in names}
    return names


# ------------------------------------------------------------------------------------------------ the change under test
def shrink_growth(inps: dict, lam: float):
    """Pull each cash-flow company's explicit growth path toward the cross-sectional median at the same date.

    median_i is taken over every company with a 'total' growth path in the same scenario; own weight is lam."""
    for scen in ("Bear", "Base", "Bull"):
        paths = [
            i["scenarios"][scen]["growth"]["total"]
            for i in inps.values()
            if i.get("model", "fcff") == "fcff"
            and "total" in ((i["scenarios"].get(scen) or {}).get("growth") or {})
        ]
        if len(paths) < 5:
            continue
        n = min(len(p) for p in paths)
        med = [float(np.median([p[k] for p in paths])) for k in range(n)]
        for i in inps.values():
            g = ((i["scenarios"].get(scen) or {}).get("growth") or {}).get("total")
            if i.get("model", "fcff") == "fcff" and g is not None:
                for k in range(min(n, len(g))):
                    g[k] = lam * g[k] + (1 - lam) * med[k]


def _one_date(args):
    asof, lams, mc_n = args
    built = {}
    for t, facts in _U["facts"].items():
        try:
            f = B.feeds_at(t, facts, _U["px"][t], _U["idx"], _U["tnx"], asof)
            if f is None:
                continue
            built[t] = (f, RP.build(f))
        except (ValueError, KeyError, ZeroDivisionError, TypeError, IndexError):
            continue
    out = []
    for lam in (1.0, *lams):
        inps = {t: copy.deepcopy(i) for t, (_, i) in built.items()}
        if lam < 1:
            shrink_growth(inps, lam)
        for t, inp in inps.items():
            rec = {"ticker": t, "asof": asof, "model": B.model_of(t), "lam": lam}
            out.append(B.value_inputs(rec, built[t][0], inp, _U["px"][t], _U["idx"], asof, mc_n))
    return out


def run(data_dir, start="2020-06-30", end="2025-09-30", mc_n=100, workers=2, tickers=None, log=print):
    names = load_universe(data_dir, tickers)
    dates = B.quarter_ends(start, end)
    log(f"{len(names)} companies, {len(dates)} dates, variants lam = 1, {', '.join(map(str, LAMS))}")
    jobs = [(a, LAMS, mc_n) for a in dates]
    recs = []
    if workers > 1:
        with mp.get_context("fork").Pool(workers) as pool:
            for k, part in enumerate(pool.imap(_one_date, jobs), 1):
                recs += part
                log(f"  {k}/{len(dates)} dates done, {len(recs)} valuations")
    else:
        for k, j in enumerate(jobs, 1):
            recs += _one_date(j)
            log(f"  {k}/{len(dates)} dates done, {len(recs)} valuations")
    return recs


# ------------------------------------------------------------------------------------------------ statistics
def by_date_ic(recs, horizon=HORIZON):
    """{asof: rank correlation of upside with the later excess return} over dates with enough names."""
    key = f"excess{horizon}"
    groups: dict = {}
    for r in recs:
        if "error" not in r and r.get(key) is not None:
            groups.setdefault(r["asof"], []).append(r)
    out = {}
    for a, g in groups.items():
        if len(g) >= MIN_NAMES:
            ic = B.spearman([r["upside"] for r in g], [r[key] for r in g])
            if not np.isnan(ic):
                out[a] = ic
    return dict(sorted(out.items()))


def spread_by_date(recs, horizon=HORIZON):
    """Mean excess return of the top fifth by upside minus the bottom fifth, per date."""
    key = f"excess{horizon}"
    groups: dict = {}
    for r in recs:
        if "error" not in r and r.get(key) is not None:
            groups.setdefault(r["asof"], []).append(r)
    out = {}
    for a, g in sorted(groups.items()):
        if len(g) >= MIN_NAMES:
            g = sorted(g, key=lambda r: r["upside"])
            k = max(1, len(g) // 5)
            out[a] = float(np.mean([r[key] for r in g[-k:]]) - np.mean([r[key] for r in g[:k]]))
    return out


def nw_t(x, lag=3):
    """t-statistic of the mean of x with a Newey-West (Bartlett) standard error."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 4:
        return float("nan")
    e = x - x.mean()
    s = float((e @ e) / n)
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * float((e[k:] @ e[:-k]) / n)
    se = (max(s, 1e-18) / n) ** 0.5
    return float(x.mean() / se)


def block_ci(x, block=3, reps=4000, seed=11):
    x = np.asarray(x, float)
    n = len(x)
    if n < 4:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    b = min(block, n)
    starts = rng.integers(0, n - b + 1, size=(reps, -(-n // b)))
    means = np.array([np.concatenate([x[s : s + b] for s in row])[:n].mean() for row in starts])
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def summarize(recs, label=""):
    ic = by_date_ic(recs)
    v = list(ic.values())
    sp = list(spread_by_date(recs).values())
    n = len([r for r in recs if "error" not in r and r.get(f"excess{HORIZON}") is not None])
    lo, hi = block_ci(v)
    return {
        "label": label,
        "n": n,
        "dates": len(v),
        "ic": float(np.mean(v)) if v else float("nan"),
        "t": nw_t(v),
        "ci": (lo, hi),
        "spread": float(np.mean(sp)) if sp else float("nan"),
        "spread_t": nw_t(sp),
    }


def in_period(recs, period):
    return [r for r in recs if period[0] <= r["asof"] <= period[1]]


def variant(recs, lam):
    return [r for r in recs if r["lam"] == lam]


def graded(recs, grades):
    return [r for r in recs if "error" in r or r.get("grade") in grades]


def paired_diff(a, b):
    """Per-date difference in rank correlation, a minus b, over dates both have."""
    ia, ib = by_date_ic(a), by_date_ic(b)
    d = [ia[k] - ib[k] for k in sorted(set(ia) & set(ib))]
    lo, hi = block_ci(d)
    return {"mean": float(np.mean(d)) if d else float("nan"), "t": nw_t(d), "ci": (lo, hi), "dates": len(d)}


def analyse(recs):
    """Choose on train, score on test. Returns everything the report needs."""
    tr, te = in_period(recs, TRAIN), in_period(recs, TEST)
    out = {"train": {}, "test": {}, "n_attempted": len(recs)}
    # 1. growth shrinkage, all names
    for lam in (1.0, *LAMS):
        out["train"][f"lam={lam}"] = summarize(variant(tr, lam), f"growth weight {lam}")
        out["test"][f"lam={lam}"] = summarize(variant(te, lam), f"growth weight {lam}")
    best_lam = max((1.0, *LAMS), key=lambda lam: out["train"][f"lam={lam}"]["ic"])
    out["best_lam"] = best_lam
    # 2. quality filter, on the baseline model and on the chosen lam. It can only be tested if the grade was computed:
    # the grade needs cash-flow, cost-of-sales, SG&A, current-asset/liability and PP&E lines the stored filings may lack.
    out["grade_testable"] = any(r.get("grade") in ("A", "B", "C", "D") for r in recs)
    out["best_grade"] = None
    if out["grade_testable"]:
        for nm, gs in GRADE_SETS.items():
            out["train"][f"grade={nm}"] = summarize(graded(variant(tr, 1.0), gs), f"grades {nm}")
            out["test"][f"grade={nm}"] = summarize(graded(variant(te, 1.0), gs), f"grades {nm}")
        out["best_grade"] = max(GRADE_SETS, key=lambda nm: out["train"][f"grade={nm}"]["ic"])
    # 3. by model
    for m in ("fcff", "excess_return", "ddm"):
        sub_tr = [r for r in variant(tr, 1.0) if r["model"] == m]
        sub_te = [r for r in variant(te, 1.0) if r["model"] == m]
        out["train"][f"model={m}"], out["test"][f"model={m}"] = summarize(sub_tr, m), summarize(sub_te, m)
    # chosen combination, scored once on test
    out["paired_vs_baseline"] = {"lam": paired_diff(variant(te, best_lam), variant(te, 1.0))}
    if out["grade_testable"]:
        gs = GRADE_SETS[out["best_grade"]]
        combo_te = graded(variant(te, best_lam), gs)
        out["test"]["chosen"] = summarize(
            combo_te, f"growth weight {best_lam} with grades {out['best_grade']}"
        )
        out["paired_vs_baseline"]["chosen"] = paired_diff(combo_te, variant(te, 1.0))
        out["paired_vs_baseline"]["grade_vs_same_names"] = paired_diff(
            graded(variant(te, 1.0), gs), variant(te, 1.0)
        )
    else:
        out["test"]["chosen"] = out["test"][f"lam={best_lam}"]
    return out


def fmt(s):
    lo, hi = s["ci"]
    if not s["dates"]:
        return f"| {s['label']} | {s['n']} | 0 | not scored: fewer than {MIN_NAMES} names on every date | | | | |"
    return (
        f"| {s['label']} | {s['n']} | {s['dates']} | {s['ic']:+.3f} | {s['t']:+.2f} | {lo:+.3f} to {hi:+.3f} | "
        f"{s['spread']:+.1%} | {s['spread_t']:+.2f} |"
    )


HEAD = (
    "| Variant | Scored valuations | Dates | Mean rank correlation | Newey-West t | 95% interval | Top minus bottom fifth | t |\n"
    "|---|---|---|---|---|---|---|---|"
)


def report(recs, res, names):
    tr, te = res["train"], res["test"]
    L = [
        "# Out-of-sample test of model changes",
        "",
        f"{len(names)} large US companies. Dates were split before any result was seen: train as-of {TRAIN[0]} to {TRAIN[1]}, "
        f"2022 dropped so no training outcome overlaps a test date, test as-of {TEST[0]} to {TEST[1]}. "
        "The outcome is the stock's next 12-month return over the S&P 500; the signal is upside to fair value. "
        "As in `REPORT.md` the engine sees only filings already made, with no analyst consensus.",
        "",
        f"Valuations attempted {res['n_attempted']} (four growth variants per company and date).",
        "",
        "## Train dates: used to choose",
        "",
        HEAD,
        *[fmt(s) for s in tr.values()],
        "",
        f"Chosen on the train dates: growth weight {res['best_lam']}"
        + (f", quality grades {res['best_grade']}." if res["grade_testable"] else "."),
        *(
            []
            if res["grade_testable"]
            else [
                "",
                "The quality filter could not be tested. The earnings-quality grade needs operating cash flow, cost of sales, SG&A, "
                "current assets and liabilities and net PP&E, and the stored SEC filings for this run do not carry those lines, so "
                "every company grades n/a. Refetching the filings with those tags is the step that would make this test possible.",
            ]
        ),
        "",
        "## Test dates: scored once",
        "",
        HEAD,
        *[fmt(s) for k, s in te.items() if k != "chosen" or res["grade_testable"]],
        "",
        "## Does the chosen change beat the baseline on the test dates?",
        "",
        "| Comparison | Dates | Mean difference in rank correlation | Newey-West t | 95% interval |",
        "|---|---|---|---|---|",
    ]
    for k, label in (
        ("lam", f"Growth weight {res['best_lam']} against baseline, same companies"),
        ("grade_vs_same_names", f"Grades {res['best_grade']} only, against all companies"),
        ("chosen", "Both changes together against baseline"),
    ):
        if k not in res["paired_vs_baseline"]:
            continue
        p = res["paired_vs_baseline"][k]
        L.append(
            f"| {label} | {p['dates']} | {p['mean']:+.3f} | {p['t']:+.2f} | {p['ci'][0]:+.3f} to {p['ci'][1]:+.3f} |"
        )
    L += ["", "## Reading the result", ""]
    base, chosen = te["lam=1.0"], te["chosen"]
    p = res["paired_vs_baseline"].get("chosen", res["paired_vs_baseline"]["lam"])
    better = p["ci"][0] > 0
    worse = p["ci"][1] < 0
    L.append(
        f"* Baseline on the test dates: rank correlation {base['ic']:+.3f} (interval {base['ci'][0]:+.3f} to {base['ci'][1]:+.3f}). "
        + (
            "The interval excludes zero."
            if base["ci"][0] > 0 or base["ci"][1] < 0
            else "The interval includes zero: no detectable skill."
        )
    )
    L.append(
        f"* Chosen change ({chosen['label']}) on the test dates: {chosen['ic']:+.3f} (interval {chosen['ci'][0]:+.3f} to {chosen['ci'][1]:+.3f}); "
        f"difference from baseline {p['mean']:+.3f}, "
        + (
            "interval above zero: an improvement that held out of sample."
            if better
            else "interval below zero: worse than the baseline out of sample."
            if worse
            else "interval includes zero: not shown to be better than the baseline."
        )
    )
    L.append(
        f"* The top fifth by upside minus the bottom fifth returned {base['spread']:+.1%} a year on the test dates "
        f"(Newey-West t {base['spread_t']:+.2f}): the stocks the model liked most did worse than the ones it liked least."
        if base["spread"] < 0 and abs(base["spread_t"]) > 1.96
        else f"* The top fifth by upside minus the bottom fifth returned {base['spread']:+.1%} on the test dates (t {base['spread_t']:+.2f})."
    )
    L += [
        "* Eleven test dates with overlapping 12-month windows are few independent observations, so intervals are wide. "
        "A change that does not clear them is not rejected, only unproven.",
        "* Every variant tried is listed above. The three families were fixed before the run, so there are at most three comparisons "
        "against the baseline, not a search over many; the intervals are not adjusted for that.",
        "* Same caveats as `REPORT.md`: survivorship in today's large companies, no point-in-time consensus, one market regime "
        "and industry class fixed by hand.",
        "",
        "Valuations are illustrations built on public data, not investment advice.",
    ]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("OOS_REPORT.md"))
    ap.add_argument("--records", type=pathlib.Path)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--mc", type=int, default=100)
    a = ap.parse_args(argv)
    import json

    recs = run(a.data_dir, mc_n=a.mc, workers=a.workers)
    if a.records:
        a.records.write_text(json.dumps(recs, separators=(",", ":")), encoding="utf-8")
    names = sorted({r["ticker"] for r in recs})
    a.out.write_text(report(recs, analyse(recs), names), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
