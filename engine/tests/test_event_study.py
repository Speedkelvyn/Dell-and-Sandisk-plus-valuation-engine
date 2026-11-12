# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
import datetime as dt

import event_study as es
import fetch_filing_dates as ffd
import numpy as np

import backtest as bt


def quarters(n=9, start=(2020, 3), rev0=100.0, growth=0.0, margin=0.1):
    rev, oi = {}, {}
    y, m = start
    for k in range(n):
        d = (dt.date(y, m, 1) + dt.timedelta(days=31)).replace(day=1) - dt.timedelta(days=1)
        rev[d.isoformat()] = rev0 * (1 + growth) ** (k / 4)
        oi[d.isoformat()] = rev[d.isoformat()] * margin
        m += 3
        if m > 12:
            m -= 12
            y += 1
    return rev, oi


def test_surprise_is_against_the_shrunk_growth_forecast():
    rev, oi = quarters(growth=0.0)
    dates = sorted(rev)
    last = dates[-1]
    # growth has been 0%, so the forecast growth is 6.5% + 0.25 * (0 - 6.5%) = 4.875%
    expected = rev[dates[-5]] * (1 + 0.065 + 0.25 * (0.0 - 0.065))
    rev[last] = expected * 1.10
    oi[last] = rev[last] * 0.15  # margin 15% against 10% a year earlier
    s = es.surprise(rev, oi, last)
    assert abs(s["rev_surprise"] - 0.10) < 1e-9
    assert abs(s["oi_surprise"] - (rev[last] * 0.15 - expected * 0.10) / expected) < 1e-9


def test_surprise_needs_a_year_of_history():
    rev, oi = quarters(n=4)
    assert es.surprise(rev, oi, sorted(rev)[-1]) is None


def test_after_close_filings_react_the_next_session():
    assert es.event_day("2024-08-01T20:05:12.000Z", "2024-08-01") == ("2024-08-01", True)  # 4:05pm New York
    assert es.event_day("2024-08-01T12:30:00.000Z", "2024-08-01") == ("2024-08-01", False)  # 8:30am
    assert es.event_day("2024-12-05T21:30:00.000Z", "2024-12-05") == ("2024-12-05", True)  # winter, 4:30pm
    assert es.event_day("2024-12-05T20:30:00.000Z", "2024-12-05") == ("2024-12-05", False)  # winter, 3:30pm
    assert es.event_day("", "2024-08-01") == ("2024-08-01", False)


def test_reported_quarter_is_the_latest_one_that_ended_before_the_event():
    q = ["2024-03-31", "2024-06-30", "2024-09-30"]
    assert es.reported_quarter(q, "2024-08-01") == "2024-06-30"
    assert es.reported_quarter(["2024-03-31"], "2024-04-02") is None  # too soon after quarter end
    assert es.reported_quarter(q, "2025-06-01") is None  # more than 110 days


def test_abnormal_return_subtracts_the_market():
    dates = [f"2024-01-{d:02d}" for d in range(1, 21)]
    px = bt.Prices({"rows": [[d, 100 * 1.01**i, 100 * 1.01**i] for i, d in enumerate(dates)], "splits": []})
    mk = bt.Prices({"rows": [[d, 100 * 1.004**i, 100 * 1.004**i] for i, d in enumerate(dates)], "splits": []})
    got = es.abnormal(px, mk, 5, 0, 1)  # two sessions of 1% against 0.4%
    assert abs(got - 100 * 2 * (np.log(1.01) - np.log(1.004))) < 1e-9
    assert es.abnormal(px, mk, 0, 0, 1) is None  # no close before day 0
    assert es.abnormal(px, mk, 19, 2, 60) is None


def test_summarize_finds_a_planted_reaction_and_not_noise():
    rng = np.random.default_rng(0)
    rows = []
    for m in range(1, 13):
        for _ in range(120):
            s = rng.normal()
            rows.append(
                {
                    "date": f"2020-{m:02d}-15",
                    "rev_surprise": s,
                    "car01": 2 * s + rng.normal(0, 3),
                    "drift": rng.normal(0, 3),
                }
            )
    a = es.summarize(rows, "rev_surprise", "car01")
    b = es.summarize(rows, "rev_surprise", "drift")
    assert a["ic"] > 0.3 and a["ic_t"] > 5 and a["beat"] > a["miss"]
    assert abs(b["ic_t"]) < 3
    shuf = es.shuffled(rows)
    assert abs(es.summarize(shuf, "rev_surprise", "car01")["ic_t"]) < 3


def test_earnings_rows_keep_only_item_202_8ks():
    block = {
        "form": ["8-K", "10-Q", "8-K", "8-K", "8-K"],
        "filingDate": ["2024-08-01", "2024-08-02", "2024-09-05", "2008-01-15", "2024-10-31"],
        "acceptanceDateTime": ["2024-08-01T20:05:12.000Z", "x", "y", "z", "2024-10-31T11:00:00.000Z"],
        "items": ["2.02,9.01", "", "5.02", "2.02", "2.02"],
    }
    assert ffd.earnings_rows(block) == [
        ["2024-08-01", "2024-08-01T20:05:12.000Z", "2.02,9.01"],
        ["2024-10-31", "2024-10-31T11:00:00.000Z", "2.02"],
    ]
