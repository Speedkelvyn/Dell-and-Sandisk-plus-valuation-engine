"""backtest.py: run the valuation as it would have looked on past dates, then compare it with what the stock did next.

For each company and each as-of date the engine sees only what existed that day:
    * SEC filings with a filing date on or before the as-of date (a later restatement is ignored),
    * the closing price and the price history up to that day, and the 10-year Treasury yield that day,
    * no analyst consensus, no peer list and no news (Yahoo only serves today's), so the model runs on trailing
      figures only. That is a harder test than the live tool, which also uses consensus.
It then looks forward 6 and 12 months and records the stock's return and its return over the S&P 500.

    python backtest.py DATA_DIR --out backtest_report.md        # DATA_DIR holds <T>_edgar.json.gz and <T>_px.json.gz
    python backtest.py DATA_DIR --from 2020-06-30 --to 2025-09-30 --mc 300

Reading the output: the rank correlation (information coefficient) between "upside to fair value" and the next
12-month excess return; average excess return by quintile of upside; how often "undervalued" beat the market; and
whether the Monte Carlo probability "P(value > price)" was calibrated. Quarterly dates with 12-month windows
overlap, so the effective sample is far smaller than the row count; the report says so. A small, one-period
sample of large US companies is evidence about the method, not a trading result.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import gzip
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import edgar  # noqa: E402

import research as RP  # noqa: E402
import valuation_v2 as v  # noqa: E402

# Statement model by company type. Yahoo's industry label is not point-in-time, so the universe file names it.
FINANCIAL = {"JPM", "BAC", "TRV", "ALL"}
REIT = {"O", "PLD", "SPG"}
SINCE = "2016-12-31"


def model_of(t: str) -> str:
    return "excess_return" if t in FINANCIAL else "ddm" if t in REIT else "fcff"


# ------------------------------------------------------------------------------------------------ loading
def _read(path):
    return json.loads(gzip.decompress(pathlib.Path(path).read_bytes()))


class Prices:
    """Daily history with split-adjusted close, total-return close and the price as quoted on the day."""

    def __init__(self, blob):
        rows = blob["rows"]
        self.dates = [r[0] for r in rows]
        self.close = [r[1] for r in rows]  # split-adjusted (Yahoo's chart)
        self.adj = [r[2] if r[2] is not None else r[1] for r in rows]  # split + dividend adjusted
        self.splits = sorted(blob.get("splits") or [])

    def idx(self, d: str) -> int | None:
        """Last trading day on or before d."""
        i = bisect.bisect_right(self.dates, d) - 1
        return i if i >= 0 else None

    def quoted(self, d: str) -> float | None:
        """The price a screen showed on d: undo every split that happened after d."""
        i = self.idx(d)
        if i is None:
            return None
        ratio = 1.0
        for sd, num, den in self.splits:
            if sd > self.dates[i]:
                ratio *= num / den
        return self.close[i] * ratio

    def upto(self, d: str, field="adj"):
        i = self.idx(d)
        arr = self.adj if field == "adj" else self.close
        return [[self.dates[j], arr[j]] for j in range(i + 1)] if i is not None else []

    def forward_return(self, d: str, months: int) -> float | None:
        i = self.idx(d)
        end = (dt.date.fromisoformat(d) + dt.timedelta(days=round(365.25 * months / 12))).isoformat()
        if i is None or end > self.dates[-1]:
            return None
        j = self.idx(end)
        return self.adj[j] / self.adj[i] - 1


def point_in_time(facts: dict, asof: str) -> dict:
    """The companyfacts as they stood on asof: only rows filed on or before that day."""
    out = {k: v_ for k, v_ in facts.items() if k not in ("facts", "dei")}
    out["facts"] = {}
    for tag, units in (facts.get("facts") or {}).items():
        for unit, rows in units.items():
            keep = [r for r in rows if r[5] and r[5] <= asof]
            if keep:
                out["facts"].setdefault(tag, {})[unit] = keep
    dei = (facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if dei:
        keep = [r for r in dei if r[3] and r[3] <= asof]
        if keep:
            out["dei"] = {"EntityCommonStockSharesOutstanding": keep}
    return out


def feeds_at(ticker, facts, px: Prices, idx: Prices, tnx: Prices, asof: str, name=None):
    """The dict research.build() takes, as of asof. Returns None when the filings are too thin to build from."""
    pit = point_in_time(facts, asof)
    parsed = edgar.parse(pit, since=SINCE)
    if not parsed["annual"].get("TotalRevenue") and not parsed["annual"].get("NetIncome"):
        return None
    price = px.quoted(asof)
    shares = None
    dei = (pit.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if dei:
        shares = dei[-1][1]
    if not shares:
        ordn = parsed["quarterly"].get("OrdinarySharesNumber") or parsed["annual"].get("OrdinarySharesNumber")
        shares = ordn[-1][1] if ordn else None
    if not shares:
        dil = parsed["quarterly"].get("DilutedAverageShares") or parsed["annual"].get("DilutedAverageShares")
        shares = dil[-1][1] if dil else None
    if price is None or not shares:
        return None
    ti = tnx.idx(asof)
    # Dividend per share paid over the last four quarters (Yahoo's dividendRate is only available today)
    dv = parsed["quarterly"].get("CashDividendsPaid") or []
    ttm = sum(abs(r[1]) for r in dv[-4:]) if len(dv) >= 4 else None
    if ttm is None and parsed["annual"].get("CashDividendsPaid"):
        ttm = abs(parsed["annual"]["CashDividendsPaid"][-1][1])
    div_rate = ttm / shares if ttm else None
    return {
        "ticker": ticker,
        "today": asof,
        "tnx": tnx.close[ti] if ti is not None else None,
        "model": model_of(ticker),
        "summary": {
            "price": {
                "regularMarketPrice": price,
                "marketCap": price * shares,
                "currency": "USD",
                "longName": name or parsed.get("name") or ticker,
            },
            "summaryDetail": {"dividendRate": div_rate},
            "defaultKeyStatistics": {"sharesOutstanding": shares},
            "financialData": {"financialCurrency": "USD"},
        },
        "annual": parsed["annual"],
        "quarterly": parsed["quarterly"],
        "stock": px.upto(asof),
        "index": idx.upto(asof),
        "peers": {},
        "news": [],
    }


# ------------------------------------------------------------------------------------------------ one valuation
def value_at(ticker, facts, px, idx, tnx, asof, mc_n=300):
    """One record: what the engine said on asof and what happened next. 'error' is set when it could not value."""
    rec = {"ticker": ticker, "asof": asof, "model": model_of(ticker)}
    try:
        f = feeds_at(ticker, facts, px, idx, tnx, asof)
        if f is None:
            return {**rec, "error": "filings too thin"}
        inp = RP.build(f)
        R = v.run_all(inp, "Base", mc_n=mc_n)
    except (ValueError, KeyError, ZeroDivisionError, TypeError, IndexError) as e:
        return {**rec, "error": f"{type(e).__name__}: {e}"}
    if not R["checks"]["ok"]:
        blocking = [b["msg"] if isinstance(b, dict) else str(b) for b in R["checks"].get("blocking", [])]
        return {**rec, "error": "; ".join(blocking)[:120] or "not valued"}
    vd = R["verdict"]
    if vd["call"] == "NOT VALUED":  # the engine's own gate declined to value it
        return {**rec, "error": "engine declined to value (data-quality gate)"}
    price = inp["market"]["price"]
    fair = float(vd["fair"])
    mc = R.get("mc") or {}
    prob = mc.get("probAbove")
    rec.update(
        price=price,
        fair=fair,
        upside=fair / price - 1,
        call=vd["call"],
        prob_above=None if prob is None else float(prob),
        ke=float(R["W"]["ke"]),
        mc_p10=float(mc["p10"]) if mc.get("p10") is not None else None,
        mc_p90=float(mc["p90"]) if mc.get("p90") is not None else None,
    )
    rec.update(baselines(f, price, px, asof))
    for m in (6, 12):
        s, b = px.forward_return(asof, m), idx.forward_return(asof, m)
        rec[f"ret{m}"] = s
        rec[f"excess{m}"] = None if s is None or b is None else s - b
        rec[f"later_price{m}"] = None if s is None else price * (1 + s)  # price plus reinvested dividends
    return rec


def baselines(f, price, px, asof):
    """Simple signals computed from the same point-in-time data, to show what the valuation adds beyond them:
    earnings yield, book-to-price, and 12-1 month price momentum."""
    cap = f["summary"]["price"]["marketCap"]
    q = f["quarterly"].get("NetIncomeCommonStockholders") or f["quarterly"].get("NetIncome") or []
    a = f["annual"].get("NetIncomeCommonStockholders") or f["annual"].get("NetIncome") or []
    ni = sum(r[1] for r in q[-4:]) if len(q) >= 4 else (a[-1][1] if a else None)
    eq = (f["quarterly"].get("CommonStockEquity") or f["annual"].get("CommonStockEquity") or [[None, None]])[
        -1
    ][1]
    i = px.idx(asof)
    j = px.idx((dt.date.fromisoformat(asof) - dt.timedelta(days=365)).isoformat())
    k = px.idx((dt.date.fromisoformat(asof) - dt.timedelta(days=30)).isoformat())
    mom = px.adj[k] / px.adj[j] - 1 if None not in (i, j, k) and j < k else None
    return {
        "base_ey": None if ni is None else ni / cap,
        "base_bp": None if eq is None else eq / cap,
        "base_mom": mom,
    }


def quarter_ends(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out = []
    y, m = d0.year, ((d0.month - 1) // 3 + 1) * 3
    while True:
        last = (
            (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)) if m != 12 else dt.date(y, 12, 31)
        )
        if last > d1:
            break
        if last >= d0:
            out.append(last.isoformat())
        m += 3
        if m > 12:
            m, y = 3, y + 1
    return out


def run(data_dir, start="2020-06-30", end="2025-09-30", mc_n=300, tickers=None, progress=None):
    d = pathlib.Path(data_dir)
    idx, tnx = Prices(_read(d / "GSPC_px.json.gz")), Prices(_read(d / "TNX_px.json.gz"))
    names = tickers or sorted(p.name.split("_")[0] for p in d.glob("*_edgar.json.gz"))
    recs = []
    for t in names:
        facts, px = _read(d / f"{t}_edgar.json.gz"), Prices(_read(d / f"{t}_px.json.gz"))
        for asof in quarter_ends(start, end):
            recs.append(value_at(t, facts, px, idx, tnx, asof, mc_n))
        if progress:
            progress(t, recs)
    return recs


# ------------------------------------------------------------------------------------------------ statistics
def rank(a):
    a = np.asarray(a, float)
    order = a.argsort(kind="mergesort")
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    for val in np.unique(a):  # average ranks for ties
        m = a == val
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(x, y):
    if len(x) < 3:
        return float("nan")
    rx, ry = rank(x), rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def summarize(recs, horizon=12):
    """Statistics over the records that valued and have a full forward window."""
    key = f"excess{horizon}"
    ok = [r for r in recs if "error" not in r]
    use = [r for r in ok if r.get(key) is not None]
    out = {
        "n_attempted": len(recs),
        "n_valued": len(ok),
        "n_scored": len(use),
        "errors": {},
        "horizon": horizon,
    }
    for r in recs:
        if "error" in r:
            k = r["error"].split(":")[0][:60]
            out["errors"][k] = out["errors"].get(k, 0) + 1
    if len(use) < 10:
        return out
    up = np.array([r["upside"] for r in use])
    ex = np.array([r[key] for r in use])
    out["ic_pooled"] = spearman(up, ex)
    by_date = {}
    for r in use:
        by_date.setdefault(r["asof"], []).append(r)
    ics = [spearman([r["upside"] for r in g], [r[key] for r in g]) for g in by_date.values() if len(g) >= 8]
    ics = [x for x in ics if not math.isnan(x)]
    out["ic_by_date_mean"] = float(np.mean(ics)) if ics else float("nan")
    out["ic_by_date_pos"] = f"{sum(x > 0 for x in ics)}/{len(ics)}"
    out["ic_n_dates"] = len(ics)
    # quintiles of upside, ranked within each date so a market-wide rally does not hide the signal
    q_ex = {i: [] for i in range(5)}
    for g in by_date.values():
        if len(g) < 10:
            continue
        rk = rank([r["upside"] for r in g]) / (len(g) - 1 or 1)
        for r, p in zip(g, rk, strict=True):
            q_ex[min(4, int(p * 5))].append(r[key])
    out["quintiles"] = [
        {"q": i + 1, "n": len(x), "mean": float(np.mean(x)), "hit": float(np.mean(np.asarray(x) > 0))}
        for i, x in q_ex.items()
        if x
    ]
    out["calls"] = {}
    for c in sorted({r["call"] for r in use}):
        x = np.array([r[key] for r in use if r["call"] == c])
        out["calls"][c] = {"n": len(x), "mean": float(x.mean()), "hit": float((x > 0).mean())}
    pr = [(r["prob_above"], r[key] > 0) for r in use if r.get("prob_above") is not None]
    if pr:
        p = np.array([a for a, _ in pr])
        o = np.array([b for _, b in pr], float)
        bins = []
        for lo, hi in ((0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001)):
            m = (p >= lo) & (p < hi)
            if m.any():
                bins.append(
                    {
                        "lo": lo,
                        "hi": min(hi, 1.0),
                        "n": int(m.sum()),
                        "pred": float(p[m].mean()),
                        "obs": float(o[m].mean()),
                    }
                )
        out["calibration"] = bins
        out["brier"] = float(np.mean((p - o) ** 2))
        out["brier_base"] = float(np.mean((o.mean() - o) ** 2))
    by_model = {}
    for r in use:
        by_model.setdefault(r["model"], []).append(r)
    out["by_model"] = {
        m: {"n": len(g), "ic": spearman([r["upside"] for r in g], [r[key] for r in g])}
        for m, g in by_model.items()
    }
    out["baselines"] = {}
    for nm, k2 in (
        ("Earnings yield", "base_ey"),
        ("Book to price", "base_bp"),
        ("12-1 month momentum", "base_mom"),
    ):
        sub = [r for r in use if r.get(k2) is not None]
        dates = sorted({r["asof"] for r in sub})
        groups = [[r for r in sub if r["asof"] == a] for a in dates]
        per = [spearman([r[k2] for r in g], [r[key] for r in g]) for g in groups if len(g) >= 8]
        per = [x for x in per if not math.isnan(x)]
        out["baselines"][nm] = {
            "n": len(sub),
            "ic_pooled": spearman([r[k2] for r in sub], [r[key] for r in sub]) if sub else float("nan"),
            "ic_by_date_mean": float(np.mean(per)) if per else float("nan"),
        }
    band = [
        r
        for r in use
        if r.get("mc_p10") is not None and r.get("mc_p90") is not None and r.get(f"later_price{horizon}")
    ]
    if band:
        lp = np.array([r[f"later_price{horizon}"] for r in band])
        lo, hi = np.array([r["mc_p10"] for r in band]), np.array([r["mc_p90"] for r in band])
        fair = np.array([r["fair"] for r in band])
        pos = (lo > 0) & (hi > 0) & (fair > 0) & (lp > 0)
        gap = np.log(lp[pos] / fair[pos])
        mad_sd = float(1.4826 * np.median(np.abs(gap - np.median(gap))))  # robust to a few extreme names
        out["band"] = {
            "n": len(band),
            "inside": float(np.mean((lp >= lo) & (lp <= hi))),
            "below": float(np.mean(lp < lo)),
            "above": float(np.mean(lp > hi)),
            "median_log_gap": float(np.median(gap)),
            "sd_log_gap": mad_sd,
            "mc_log_width": float(np.median(np.log(hi[pos] / lo[pos])) / 2.563),  # p10 to p90 spans 2.563 sd
        }
    out["median_abs_upside"] = float(np.median(np.abs(up)))
    out["upside_vs_ret"] = float(np.corrcoef(up, np.array([r[f"ret{horizon}"] for r in use]))[0, 1])
    return out


def report(recs, stats6, stats12, start, end) -> str:
    L = ["# Point-in-time backtest", ""]
    s = stats12
    tickers = sorted({r["ticker"] for r in recs})
    L += [
        f"{len(tickers)} large US companies, quarter-end dates from {start} to {end}. On each date the engine saw only "
        "SEC filings already filed, the price and the 10-year yield that day, and no analyst consensus (Yahoo only "
        "serves today's), then the stock's return over the next 6 and 12 months was compared with the S&P 500.",
        "",
        f"Valuations attempted {s['n_attempted']}, valued {s['n_valued']}, with a full 12-month outcome {s['n_scored']}.",
    ]
    if s["errors"]:
        L.append(
            "Could not value: "
            + "; ".join(f"{k} ({n})" for k, n in sorted(s["errors"].items(), key=lambda x: -x[1]))
            + "."
        )
    L.append("")
    for st in (stats12, stats6):
        h = st["horizon"]
        if "ic_pooled" not in st:
            L += [f"## {h}-month outcomes", "", "Too few scored valuations.", ""]
            continue
        L += [
            f"## {h}-month excess return versus the S&P 500",
            "",
            f"Rank correlation of upside to fair value with the later excess return: {st['ic_pooled']:+.3f} pooled; "
            f"{st['ic_by_date_mean']:+.3f} on average across dates, positive on {st['ic_by_date_pos']} dates. "
            "Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.",
            "",
            "| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |",
            "|---|---|---|---|",
        ]
        L += [f"| {q['q']} | {q['n']} | {q['mean']:+.1%} | {q['hit']:.0%} |" for q in st["quintiles"]]
        L += ["", "| Verdict | Valuations | Mean excess return | Beat the market |", "|---|---|---|---|"]
        L += [f"| {c} | {x['n']} | {x['mean']:+.1%} | {x['hit']:.0%} |" for c, x in st["calls"].items()]
        L.append("")
        if "calibration" in st:
            L += [
                "Monte Carlo P(value > price) against how often the stock beat the market:",
                "",
                "| Predicted band | Valuations | Mean predicted | Observed |",
                "|---|---|---|---|",
            ]
            L += [
                f"| {b['lo']:.0%} to {b['hi']:.0%} | {b['n']} | {b['pred']:.0%} | {b['obs']:.0%} |"
                for b in st["calibration"]
            ]
            L += [
                "",
                f"Brier score {st['brier']:.3f} against {st['brier_base']:.3f} for always forecasting the base rate (lower is better).",
                "",
            ]
        if "band" in st:
            b = st["band"]
            L += [
                f"The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. "
                f"The price {h} months later (dividends reinvested) landed inside it {b['inside']:.0%} of the time, "
                f"below it {b['below']:.0%} and above it {b['above']:.0%} ({b['n']} valuations). "
                f"The model's band implies a standard deviation of about {b['mc_log_width']:.0%} in log value; the realised gap "
                f"between the later price and fair value (robust estimate) had {b['sd_log_gap']:.0%}, so the band would need to be about "
                f"{b['sd_log_gap'] / b['mc_log_width']:.1f} times wider to be calibrated.",
                "",
            ]
        L += [
            "Same test for simple signals computed from the same data:",
            "",
            "| Signal | Valuations | Rank correlation, pooled | Average across dates |",
            "|---|---|---|---|",
            f"| Upside to fair value (this engine) | {st['n_scored']} | {st['ic_pooled']:+.3f} | {st['ic_by_date_mean']:+.3f} |",
        ]
        L += [
            f"| {nm} | {b['n']} | {b['ic_pooled']:+.3f} | {b['ic_by_date_mean']:+.3f} |"
            for nm, b in st["baselines"].items()
        ]
        L.append("")
        L += [
            "By model: "
            + "; ".join(f"{m} n={x['n']}, IC {x['ic']:+.3f}" for m, x in st["by_model"].items())
            + ".",
            "",
        ]
    L += [
        "## Read this before relying on it",
        "",
        "* Quarterly dates with 12-month windows overlap, so the independent sample is a fraction of the row count.",
        "* The universe is large, liquid US companies that exist today, so it carries survivorship bias and covers one market regime.",
        "* No consensus, peer comparables or news were available point-in-time. The live tool uses them, so this tests the model on trailing facts alone.",
        "* Returns include dividends; the S&P 500 benchmark is the price index, which understates the benchmark by its dividend yield.",
        "* Industry class (bank, insurer, REIT) is fixed by hand, not point-in-time.",
        "",
        "Valuations are illustrations built on public data, not investment advice.",
    ]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=pathlib.Path)
    ap.add_argument("--from", dest="start", default="2020-06-30")
    ap.add_argument("--to", dest="end", default="2025-09-30")
    ap.add_argument("--mc", type=int, default=300)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("backtest_report.md"))
    ap.add_argument("--records", type=pathlib.Path, help="also write every valuation as JSON")
    a = ap.parse_args(argv)
    recs = run(
        a.data_dir,
        a.start,
        a.end,
        a.mc,
        a.tickers,
        progress=lambda t, r: print(f"{t:<6} {len(r)} valuations so far", flush=True),
    )
    if a.records:
        a.records.write_text(json.dumps(recs, indent=0), encoding="utf-8")
    a.out.write_text(report(recs, summarize(recs, 6), summarize(recs, 12), a.start, a.end), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
