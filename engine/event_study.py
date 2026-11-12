# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
event_study.py -- does the market react to results that beat or miss our forecast, and does the move continue?

For every earnings event the engine's forecast rule is applied to the quarter being reported, using only quarters that
ended before it, and compared with what the company reported:

    revenue forecast = revenue of the same quarter a year earlier x (1 + growth)
    growth           = 6.5% + 0.25 x (last quarter's year-on-year growth - 6.5%)     (the rule in research.py)
    operating income forecast = revenue forecast x the operating margin of the same quarter a year earlier
    revenue surprise = reported / forecast - 1
    income surprise  = (reported operating income - forecast) / revenue forecast      (in points of revenue)

Reaction = abnormal return (stock minus the S&P 500, total-return closes) over the first two trading days that can
trade on the news: day 0 is the filing day, or the next trading day when the filing was accepted after 4pm New York.
Drift = abnormal return from day 2 to day 60.

    python event_study.py ../backtest/data_wide                 # events dated by 8-K Item 2.02 if earnings_8k.json.gz exists
    python event_study.py ../backtest/data_wide --source filing # dated by the 10-Q/10-K filing day (the later, quieter date)

Limits: no analyst consensus, so a surprise is against a mechanical forecast, and the market reacts to its own
expectation; today's index members only (survivorship); the reported figures are the latest filed, so a restated
quarter uses its restated value. Fixed before the results: two surprise measures, two windows, no tuning.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

import edgar
import numpy as np

import backtest as bt

CENTRE, WEIGHT = 0.065, 0.25
NY = ZoneInfo("America/New_York")


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def match_year_ago(dates: list[str], i: int, tol: int = 12) -> int | None:
    """Index of the quarter that ended a year before dates[i] (52/53-week years drift a few days)."""
    target = _d(dates[i]) - dt.timedelta(days=365)
    best = min(range(i), key=lambda j: abs((_d(dates[j]) - target).days), default=None)
    return best if best is not None and abs((_d(dates[best]) - target).days) <= tol else None


def surprise(rev: dict[str, float], oi: dict[str, float], e: str) -> dict | None:
    """Forecast and surprise for the quarter ending e, from quarters that ended before it."""
    dates = sorted(rev)
    if e not in rev:
        return None
    i = dates.index(e)
    ya = match_year_ago(dates, i)
    prev = i - 1 if i > 0 and 70 <= (_d(e) - _d(dates[i - 1])).days <= 110 else None
    if ya is None or prev is None:
        return None
    ya_prev = match_year_ago(dates, prev)
    if ya_prev is None:
        return None
    r_ya, r_prev, r_prev_ya = rev[dates[ya]], rev[dates[prev]], rev[dates[ya_prev]]
    if min(r_ya, r_prev, r_prev_ya, rev[e]) <= 0:
        return None
    g = r_prev / r_prev_ya - 1
    f_rev = r_ya * (1 + CENTRE + WEIGHT * (g - CENTRE))
    out = {"quarter": e, "rev_surprise": rev[e] / f_rev - 1}
    if dates[ya] in oi and e in oi:
        f_oi = f_rev * (oi[dates[ya]] / r_ya)
        out["oi_surprise"] = (oi[e] - f_oi) / f_rev
    return out


def reported_quarter(qdates: list[str], event: str) -> str | None:
    """The quarter an event on this day reports: the latest quarter that ended 5 to 110 days earlier."""
    ev = _d(event)
    ok = [q for q in qdates if 5 <= (ev - _d(q)).days <= 110]
    return max(ok) if ok else None


def event_day(accepted_utc: str, filing_date: str) -> tuple[str, bool]:
    """(date, after_close). After 4pm New York the first trading session that can react is the next one."""
    if accepted_utc:
        t = dt.datetime.fromisoformat(accepted_utc.replace("Z", "+00:00")).astimezone(NY)
        return t.date().isoformat(), t.hour >= 16
    return filing_date, False


def filing_events(facts: dict) -> list[tuple[str, str, bool]]:
    """(event date, quarter end, after_close) from the dates 10-Qs and 10-Ks were filed."""
    by_filed: dict[str, str] = {}
    for units in (facts.get("facts") or {}).values():
        for rows in units.values():
            for r in rows:
                form, filed, end = r[4], r[5], r[1]
                if form in ("10-Q", "10-K") and filed and end:
                    by_filed[filed] = max(by_filed.get(filed, ""), end)
    return [(f, e, False) for f, e in sorted(by_filed.items())]


def abnormal(px: bt.Prices, mk: bt.Prices, i0: int, a: int, b: int) -> float | None:
    """Log abnormal return from the close before trading day i0+a to the close of day i0+b, percent."""
    lo, hi = i0 + a - 1, i0 + b
    if lo < 0 or hi >= len(px.dates):
        return None
    ml, mh = mk.idx(px.dates[lo]), mk.idx(px.dates[hi])
    if ml is None or mh is None:
        return None
    return 100 * (np.log(px.adj[hi] / px.adj[lo]) - np.log(mk.adj[mh] / mk.adj[ml]))


def events_for(ticker, facts, px, mk, source="8k", eights=None, since="2012-01-01") -> list[dict]:
    q = edgar.parse(facts, since="2009-12-31")["quarterly"]
    rev, oi = dict(q.get("TotalRevenue", [])), dict(q.get("OperatingIncome", []))
    if source == "filing":
        raw = filing_events(facts)
    else:
        raw = []
        for fd, acc, _items in eights or []:
            d, after = event_day(acc, fd)
            raw.append((d, None, after))
    out, seen = [], set()
    for d, quarter, after in raw:
        quarter = quarter or reported_quarter(sorted(rev), d)
        if quarter is None or quarter in seen or d < since or not 5 <= (_d(d) - _d(quarter)).days <= 120:
            continue
        s = surprise(rev, oi, quarter)
        if s is None:
            continue
        i = px.idx(d)
        if i is None:
            continue
        i0 = (
            i + 1 if (px.dates[i] < d or after) else i
        )  # next session when the day is not a session or after the close
        if px.dates[i] < d:
            i0 = i + 1
        seen.add(quarter)
        out.append(
            {
                "ticker": ticker,
                "date": px.dates[i0] if i0 < len(px.dates) else d,
                **s,
                "pre": abnormal(px, mk, i0, -5, -2),
                "car01": abnormal(px, mk, i0, 0, 1),
                "drift": abnormal(px, mk, i0, 2, 60),
            }
        )
    return out


def nw_se(x, lags=3):
    x = np.asarray(x, float)
    n, e = len(x), x - x.mean()
    v = float(e @ e) / n
    for k in range(1, lags + 1):
        v += 2 * (1 - k / (lags + 1)) * float(e[k:] @ e[:-k]) / n
    return float(np.sqrt(v / n))


def summarize(rows: list[dict], key: str, outcome: str, min_n: int = 30) -> dict:
    """By calendar month: rank correlation of surprise with outcome, and top-third minus bottom-third outcome."""
    import collections

    by = collections.defaultdict(list)
    for r in rows:
        if r.get(key) is not None and r.get(outcome) is not None and np.isfinite(r[outcome]):
            by[r["date"][:7]].append((r[key], r[outcome]))
    ics, spreads, beat, miss = [], [], [], []
    for m in sorted(by):
        g = by[m]
        if len(g) < min_n:
            continue
        s, o = (
            np.array([x[0] for x in g]),
            np.clip(np.array([x[1] for x in g]), *np.percentile([x[1] for x in g], [1, 99])),
        )
        ics.append(bt.spearman(list(s), list(o)))
        k = len(g) // 3
        order = np.argsort(s)
        spreads.append(o[order[-k:]].mean() - o[order[:k]].mean())
        beat += list(o[s > 0])
        miss += list(o[s < 0])
    ics, spreads = np.array(ics), np.array(spreads)
    return {
        "months": len(ics),
        "events": sum(len(by[m]) for m in by if len(by[m]) >= min_n),
        "ic": float(ics.mean()),
        "ic_t": float(ics.mean() / nw_se(ics)),
        "spread": float(spreads.mean()),
        "spread_t": float(spreads.mean() / nw_se(spreads)),
        "beat": float(np.mean(beat)),
        "miss": float(np.mean(miss)),
        "n_beat": len(beat),
        "n_miss": len(miss),
    }


def collect(data_dir: pathlib.Path, source: str, since: str) -> list[dict]:
    mk = bt.Prices(bt._read(data_dir / "GSPC_px.json.gz"))
    p8 = data_dir / "earnings_8k.json.gz"
    eights = {}
    if p8.exists():
        with gzip.open(p8, "rt") as f:
            eights = json.load(f)
    rows = []
    for p in sorted(data_dir.glob("*_edgar.json.gz")):
        t = p.name.split("_")[0]
        try:
            facts = bt._read(p)
            px = bt.Prices(bt._read(data_dir / f"{t}_px.json.gz"))
            rows += events_for(t, facts, px, mk, source, eights.get(t), since)
        except (FileNotFoundError, ValueError, KeyError):
            continue
    return rows


def shuffled(rows: list[dict], seed: int = 0) -> list[dict]:
    """Placebo: surprises reassigned at random among the events of the same month."""
    import collections

    rng = np.random.default_rng(seed)
    by = collections.defaultdict(list)
    for i, r in enumerate(rows):
        by[r["date"][:7]].append(i)
    out = [dict(r) for r in rows]
    for idx in by.values():
        for key in ("rev_surprise", "oi_surprise"):
            vals = [rows[i].get(key) for i in idx]
            for i, v in zip(idx, rng.permutation(np.array(vals, dtype=object)), strict=True):
                out[i][key] = v
    return out


def report(rows: list[dict], label: str) -> str:
    L = [f"{label}: {len(rows)} events, {len({r['ticker'] for r in rows})} firms"]
    L.append(
        f"{'surprise':12s}{'window':16s}{'months':>7s}{'events':>8s}{'IC':>8s}{'t':>6s}{'top-bottom %':>14s}{'t':>6s}{'beat %':>8s}{'miss %':>8s}"
    )
    for key, kn in (("rev_surprise", "revenue"), ("oi_surprise", "op. income")):
        for out, on in (("pre", "pre -5..-2"), ("car01", "reaction 0..1"), ("drift", "drift 2..60")):
            s = summarize(rows, key, out)
            L.append(
                f"{kn:12s}{on:16s}{s['months']:7d}{s['events']:8d}{s['ic']:8.3f}{s['ic_t']:6.1f}{s['spread']:14.2f}{s['spread_t']:6.1f}"
                f"{s['beat']:8.2f}{s['miss']:8.2f}"
            )
    return "\n".join(L)


def report_all(rows: list[dict], label: str) -> str:
    parts = [report(rows, label)]
    for name, lo, hi in (("2012-2018", "2012", "2018"), ("2019-2025", "2019", "2025")):
        sub = [r for r in rows if lo <= r["date"][:4] <= hi]
        parts.append("\n" + report(sub, f"{label}, {name}"))
    parts.append("\n" + report(shuffled(rows), f"{label}, placebo (surprises shuffled within month)"))
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--source", choices=["8k", "filing"], default=None)
    ap.add_argument("--since", default="2012-01-01")
    ap.add_argument("--out", default=None, help="write the events as JSON")
    a = ap.parse_args(argv[1:])
    d = pathlib.Path(a.data_dir)
    source = a.source or ("8k" if (d / "earnings_8k.json.gz").exists() else "filing")
    rows = collect(d, source, a.since)
    print(
        report_all(rows, f"events dated by {'8-K Item 2.02' if source == '8k' else '10-Q/10-K filing day'}")
    )
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(rows, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
