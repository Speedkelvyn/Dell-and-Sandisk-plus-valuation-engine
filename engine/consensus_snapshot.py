"""consensus_snapshot.py — save today's analyst consensus for a list of tickers.

Yahoo serves only the current consensus, never a past one, so a backtest cannot see what analysts expected on a
past date. Saving a snapshot every week builds that history from now on. Each run writes one compact file,
consensus/<date>.json.gz, holding for every ticker the earnings and revenue estimates by period, the 7/30/60/90-day
estimate trail and revisions, price targets, and the recommendation trend.

    pip install yfinance
    python consensus_snapshot.py ../consensus/universe.txt --out ../consensus
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import pathlib
import sys
import time

MODULES = "price,earningsTrend,financialData,recommendationTrend,defaultKeyStatistics"
Q1 = "https://query1.finance.yahoo.com"
KEEP = {
    "price": ("regularMarketPrice", "marketCap", "currency", "shortName"),
    "financialData": (
        "currentPrice",
        "targetHighPrice",
        "targetLowPrice",
        "targetMeanPrice",
        "targetMedianPrice",
        "recommendationMean",
        "recommendationKey",
        "numberOfAnalystOpinions",
    ),
    "defaultKeyStatistics": ("forwardEps", "trailingEps", "sharesOutstanding", "pegRatio"),
}


def compact(x):
    """Yahoo wraps numbers as {"raw": 1.2, "fmt": "1.2"}: keep the raw value, drop the text, drop empty objects."""
    if isinstance(x, dict):
        if "raw" in x:
            return x["raw"]
        out = {k: compact(v) for k, v in x.items() if k not in ("maxAge",)}
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    if isinstance(x, list):
        return [compact(v) for v in x]
    return x


def trim(summary):
    """The parts of a quoteSummary result worth keeping, as plain numbers."""
    out = {}
    for mod, keys in KEEP.items():
        d = compact(summary.get(mod) or {})
        out[mod] = {k: d[k] for k in keys if k in d}
    out["earningsTrend"] = compact((summary.get("earningsTrend") or {}).get("trend") or [])
    out["recommendationTrend"] = compact((summary.get("recommendationTrend") or {}).get("trend") or [])
    return out


def snapshot(tickers, pause=0.4):
    from yfinance.data import YfData

    yd = YfData()
    data, errors = {}, {}
    for t in tickers:
        try:
            j = yd.get_raw_json(
                f"{Q1}/v10/finance/quoteSummary/{t.replace('.', '-')}", params={"modules": MODULES}
            )
            res = (j.get("quoteSummary") or {}).get("result")
            if not res:
                raise ValueError("no result")
            data[t] = trim(res[0])
        except Exception as e:  # one bad ticker must not lose the rest
            errors[t] = f"{type(e).__name__}: {e}"[:120]
        time.sleep(pause)
    return data, errors


def write(data, errors, out_dir, today=None):
    today = today or dt.date.today().isoformat()
    p = pathlib.Path(out_dir) / f"{today}.json.gz"
    payload = {"asof": today, "source": "Yahoo Finance quoteSummary", "tickers": data, "errors": errors}
    p.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(), 9))
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("universe", help="text file with one ticker per line")
    ap.add_argument("--out", default="consensus")
    ap.add_argument(
        "--min-share", type=float, default=0.8, help="fail if fewer than this share of tickers came back"
    )
    a = ap.parse_args(argv)
    tickers = [x.strip() for x in pathlib.Path(a.universe).read_text().split() if x.strip()]
    data, errors = snapshot(tickers)
    p = write(data, errors, a.out)
    print(f"wrote {p}: {len(data)} of {len(tickers)} tickers, {len(errors)} errors")
    if len(data) < a.min_share * len(tickers):
        print("too few tickers came back; first errors:", list(errors.items())[:3], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
