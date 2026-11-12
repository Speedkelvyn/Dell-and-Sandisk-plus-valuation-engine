# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
build_universe.py -- download filings and prices for a wide universe so the point-in-time backtest has enough names.

132 companies over five years cannot separate a real edge of a few hundredths in rank correlation from noise. This
builds a wider set: the 132 already tested plus today's S&P 500 and S&P 400 members, with filings from 2009 and daily
prices from 2009, written in the format backtest.py reads.

    SEC_USER_AGENT='Your Name you@example.com' python build_universe.py backtest/data_wide

Needs the network (Wikipedia for the member lists, the SEC for filings, Yahoo for prices), so it runs in the GitHub
Actions workflow `expand-backtest.yml`.

Two limits to keep in mind when reading results:
  * Survivorship. The lists are today's members, so firms that fell out of the index (or failed) are missing, which
    flatters value and momentum signals in the early years.
  * Financials and real estate are left out of the new names, because the engine's bank and REIT models have been
    checked on only a handful of companies.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
import time
import urllib.request
from fractions import Fraction

import edgar

SINCE = "2009-12-31"
PRICE_START = "2009-06-01"
PAGES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}
SKIP_SECTORS = {"Financials", "Real Estate"}


def clean(sym: str) -> str:
    return str(sym).strip().upper().replace(".", "-")


def members(frame, skip=SKIP_SECTORS) -> list[str]:
    """Tickers from a Wikipedia constituents table, minus the sectors the engine has no validated model for."""
    sym = next(c for c in frame.columns if str(c).lower() in ("symbol", "ticker symbol", "ticker"))
    sector = next((c for c in frame.columns if "sector" in str(c).lower()), None)
    out = []
    for _, r in frame.iterrows():
        if sector is not None and str(r[sector]) in skip:
            continue
        out.append(clean(r[sym]))
    return out


def constituents() -> list[str]:
    import io

    import pandas as pd

    out: list[str] = []
    for url in PAGES.values():
        req = urllib.request.Request(url, headers={"User-Agent": edgar_ua()})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
        for t in pd.read_html(io.StringIO(html)):
            if any(str(c).lower() in ("symbol", "ticker symbol") for c in t.columns):
                out += members(t)
                break
    return sorted(set(out))


def edgar_ua() -> str:
    import os

    return " ".join((os.environ.get("SEC_USER_AGENT") or "backtest-research").split())


def px_blob(hist, splits) -> dict:
    """The price file format backtest.Prices reads: rows of [date, split-adjusted close, total-return close]."""
    rows = []
    for d, r in hist.iterrows():
        close, adj = r.get("Close"), r.get("Adj Close")
        if close != close or close is None:  # NaN
            continue
        rows.append([d.strftime("%Y-%m-%d"), float(close), None if adj != adj or adj is None else float(adj)])
    sp = []
    for d, ratio in splits.items() if splits is not None else []:
        if ratio and ratio > 0 and ratio != 1:
            f = Fraction(float(ratio)).limit_denominator(100)  # 4.0 -> 4/1, 0.1 -> 1/10, 1.5 -> 3/2
            sp.append([d.strftime("%Y-%m-%d"), f.numerator, f.denominator])
    return {"rows": rows, "splits": sp}


def fetch_prices(symbol: str, retries: int = 3) -> dict | None:
    import yfinance as yf

    for k in range(retries):
        try:
            t = yf.Ticker(symbol)
            hist = t.history(start=PRICE_START, auto_adjust=False)
            if len(hist) < 250:
                return None
            return px_blob(hist, t.splits)
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def write(path: pathlib.Path, blob) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(blob, f, separators=(",", ":"))


def build(
    out_dir: pathlib.Path, existing: list[str], pause: float = 0.25
) -> tuple[list[str], dict[str, str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(set(existing) | set(constituents()))
    done, failed = [], {}
    for t in names:
        if (out_dir / f"{t}_edgar.json.gz").exists() and (out_dir / f"{t}_px.json.gz").exists():
            done.append(t)
            continue
        try:
            facts = edgar.fetch(t, since=SINCE)
            if not facts["facts"]:
                raise LookupError("no facts")
            px = fetch_prices(t)
            if px is None:
                raise LookupError("no prices")
        except Exception as e:
            failed[t] = f"{type(e).__name__}: {e}"
        else:
            write(out_dir / f"{t}_edgar.json.gz", facts)
            write(out_dir / f"{t}_px.json.gz", px)
            done.append(t)
        time.sleep(pause)
    for sym, name in (("^GSPC", "GSPC"), ("^TNX", "TNX")):
        px = fetch_prices(sym)
        if px is None:
            raise RuntimeError(f"could not download {sym}")
        write(out_dir / f"{name}_px.json.gz", px)
    return done, failed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    here = pathlib.Path(__file__).resolve().parents[1] / "backtest" / "data"
    existing = sorted(p.name.split("_")[0] for p in here.glob("*_edgar.json.gz"))
    done, failed = build(pathlib.Path(argv[1]), existing)
    print(f"built {len(done)} names; failed {len(failed)}")
    for t, why in list(failed.items())[:60]:
        print(f"  {t}: {why}")
    return 0 if len(done) > 100 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
