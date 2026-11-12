import datetime as dt
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_universe as bu

import backtest as B

DATA = pathlib.Path(__file__).resolve().parents[2] / "backtest" / "data"


def test_members_drops_financials_and_real_estate_and_fixes_share_class_symbols():
    t = pd.DataFrame(
        {
            "Symbol": ["AAPL", "BRK.B", "JPM", "PLD", "bf.b"],
            "GICS Sector": [
                "Information Technology",
                "Financials",
                "Financials",
                "Real Estate",
                "Consumer Staples",
            ],
        }
    )
    assert bu.members(t) == ["AAPL", "BF-B"]


def test_px_blob_matches_the_stored_price_format_and_turns_split_ratios_into_fractions():
    idx = pd.DatetimeIndex([dt.date(2020, 8, 28), dt.date(2020, 8, 31)])
    hist = pd.DataFrame({"Close": [499.2, 129.0], "Adj Close": [120.0, 127.0]}, index=idx)
    splits = pd.Series(
        [4.0, 0.1, 1.5],
        index=pd.DatetimeIndex([dt.date(2020, 8, 31), dt.date(2021, 1, 4), dt.date(2022, 2, 2)]),
    )
    blob = bu.px_blob(hist, splits)
    assert blob["rows"][0] == ["2020-08-28", 499.2, 120.0]
    assert blob["splits"] == [["2020-08-31", 4, 1], ["2021-01-04", 1, 10], ["2022-02-02", 3, 2]]
    assert B.Prices(blob).idx("2020-08-31") == 1  # the backtest can read it


def test_parallel_backtest_gives_the_same_records_as_the_serial_one():
    if not (DATA / "AAPL_edgar.json.gz").exists():
        return
    kw = {"tickers": ["AAPL", "MSFT"], "start": "2024-06-30", "end": "2024-12-31", "mc_n": 50}
    assert B.run(DATA, workers=2, **kw) == B.run(DATA, workers=1, **kw)
