# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
fetch_filing_dates.py -- the dates and times companies announced earnings, from SEC filings.

An earnings release is filed as an 8-K with Item 2.02 ("Results of Operations and Financial Condition"). The SEC records
the day and the exact time it accepted the filing, so the study can tell a release made before the open from one made
after the close. The 10-Q and 10-K usually come days or weeks later and carry little news.

    SEC_USER_AGENT='Your Name you@example.com' python fetch_filing_dates.py backtest/data_wide

Reads the CIK from each stored `<TICKER>_edgar.json.gz` and writes `earnings_8k.json.gz` next to them:
{ticker: [[filing_date, accepted_utc, items], ...]}. Needs the network, so it runs in the GitHub Actions workflow
`fetch-filing-dates.yml`. Names already fetched are kept, so an interrupted run can be repeated.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
import time

import edgar

SUBMISSIONS = "https://data.sec.gov/submissions/"
SINCE = "2009-06-01"


def earnings_rows(block: dict, since: str = SINCE) -> list[list[str]]:
    """8-K filings with Item 2.02 from one submissions block (the `recent` block or an older file)."""
    forms, dates = block.get("form") or [], block.get("filingDate") or []
    acc, items = block.get("acceptanceDateTime") or [], block.get("items") or []
    out = []
    for i, form in enumerate(forms):
        if form != "8-K" or i >= len(dates) or dates[i] < since:
            continue
        it = items[i] if i < len(items) else ""
        if "2.02" in str(it).split(","):
            out.append([dates[i], acc[i] if i < len(acc) else "", str(it)])
    return out


def fetch_one(cik: int, ua: str | None = None, since: str = SINCE, pause: float = 0.12) -> list[list[str]]:
    j = edgar._get(f"{SUBMISSIONS}CIK{int(cik):010d}.json", ua, timeout=60)
    rows = earnings_rows((j.get("filings") or {}).get("recent") or {}, since)
    for f in (j.get("filings") or {}).get("files") or []:
        if (f.get("filingTo") or "9999") >= since:
            time.sleep(pause)
            rows += earnings_rows(edgar._get(f"{SUBMISSIONS}{f['name']}", ua, timeout=60), since)
    return sorted({tuple(r) for r in rows})  # type: ignore[arg-type]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    d = pathlib.Path(argv[1])
    out_path = d / "earnings_8k.json.gz"
    have = {}
    if out_path.exists():
        with gzip.open(out_path, "rt") as fh:
            have = json.load(fh)
    failed = {}
    for p in sorted(d.glob("*_edgar.json.gz")):
        t = p.name.split("_")[0]
        if t in have:
            continue
        try:
            with gzip.open(p, "rt") as fh:
                cik = json.load(fh)["cik"]
            have[t] = [list(r) for r in fetch_one(cik)]
        except Exception as e:
            failed[t] = f"{type(e).__name__}: {e}"
        time.sleep(0.12)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(have, f, separators=(",", ":"))
    print(f"{len(have)} names, {sum(len(v) for v in have.values())} earnings releases; failed {len(failed)}")
    for t, why in list(failed.items())[:40]:
        print(f"  {t}: {why}")
    return 0 if len(have) > 100 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
