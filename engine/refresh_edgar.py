"""refresh_edgar.py: re-download the SEC filings for every ticker already in a backtest data folder.

The stored `<T>_edgar.json.gz` files keep only the XBRL tags the engine asked for at the time. When the engine learns
a new fallback tag (see edgar.py), the stored files lack it, so filers that use that tag cannot be valued. This
rebuilds each file from the SEC with the current tag list. Prices are left alone.

    SEC_USER_AGENT='Your Name you@example.com' python refresh_edgar.py backtest/data

Needs the network, so it runs in the GitHub Actions workflow `refresh-backtest.yml`; the sandbox cannot reach the SEC.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
import time

import edgar

SINCE = "2016-12-31"  # the stored files start here


def refresh(data_dir: pathlib.Path, pause: float = 0.25) -> tuple[list[str], dict[str, str]]:
    done, failed = [], {}
    for path in sorted(data_dir.glob("*_edgar.json.gz")):
        t = path.name.split("_")[0]
        try:
            blob = edgar.fetch(t, since=SINCE)
        except Exception as e:  # keep the old file; report which tickers failed
            failed[t] = f"{type(e).__name__}: {e}"
        else:
            if not blob["facts"]:
                failed[t] = "no facts returned"
            else:
                with gzip.open(path, "wt", encoding="utf-8") as f:
                    json.dump(blob, f, separators=(",", ":"))
                done.append(t)
        time.sleep(pause)  # the SEC allows 10 requests a second; two requests per ticker
    return done, failed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    done, failed = refresh(pathlib.Path(argv[1]))
    print(f"refreshed {len(done)} tickers; failed {len(failed)}")
    for t, why in failed.items():
        print(f"  {t}: {why}")
    return 0 if len(done) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
