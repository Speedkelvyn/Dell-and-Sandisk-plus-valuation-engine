# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""Tests for credit_spread.py: the SEC statements become the lines the rating reads, and the arithmetic is checkable."""

import math
import pathlib
import statistics

import edgar
import pytest

import credit_score as cs
import credit_spread as sp

FIX = pathlib.Path(__file__).parent / "fixtures"
M = 1e6


def _inst(end, val):
    return [None, end, val, "FY", "10-K", "2023-03-01"]


def _flow(end, val):
    return [end[:4] + "-01-01", end, val, "FY", "10-K", "2023-03-01"]


def ll_facts():
    """LL Flooring FY2022, keyed in from its Form 10-K (the figures in the backtested memo): a hand-assembled filing."""
    e = "2022-12-31"
    f = {
        "Revenues": _flow(e, 1110.679 * M),
        "OperatingIncomeLoss": _flow(e, -11.722 * M),
        "GoodwillImpairmentLoss": _flow(e, 9.693 * M),
        "DepreciationDepletionAndAmortization": _flow(e, 18.410 * M),
        "InterestExpense": _flow(e, 1.816 * M),
        "NetIncomeLoss": _flow(e, -12.081 * M),
        "PaymentsToAcquirePropertyPlantAndEquipment": _flow(e, 22.048 * M),
        "Assets": _inst(e, 614.0 * M),
        "Liabilities": _inst(e, 357.9 * M),
        "AssetsCurrent": _inst(e, 369.748 * M),
        "LiabilitiesCurrent": _inst(e, 180.524 * M),
        "LongTermDebtNoncurrent": _inst(e, 72.0 * M),
        "RetainedEarningsAccumulatedDeficit": _inst(e, 177.5 * M),
        "StockholdersEquity": _inst(e, 256.081 * M),
    }
    return {"name": "LL Flooring Holdings, Inc.", "facts": {k: {"USD": [v]} for k, v in f.items()}}


def test_ll_flooring_statements_match_the_memo():
    (y,) = sp.statements(ll_facts(), since="2020-12-31")
    assert (
        y["at"] == pytest.approx(614.0)
        and y["lt"] == pytest.approx(357.9)
        and y["re"] == pytest.approx(177.5)
    )
    assert y["oiadp"] == pytest.approx(-11.722)  # as filed
    assert y["oibdp"] == pytest.approx(16.381, abs=0.001)  # EBITDA before the goodwill impairment
    assert y["dltt"] == pytest.approx(72.0) and y["dlc"] == pytest.approx(0.0)
    assert y["capex"] == pytest.approx(22.048)


def test_ll_flooring_altman_z_matches_the_memo():
    (y,) = sp.statements(ll_facts(), since="2020-12-31")
    z = cs.altman_z(y, 3.80 * 29.257)
    assert z == pytest.approx(2.71, abs=0.01)


def test_coverage_arithmetic():
    (y,) = sp.statements(ll_facts(), since="2020-12-31")
    c = sp.coverage(y)
    assert c["cfads"] == pytest.approx(16.381 - 22.048, abs=0.001)
    assert c["debt_service"] == pytest.approx(1.816)
    assert c["dscr"] < 0  # EBITDA does not cover capex, let alone interest


def test_rating_from_filings_without_a_price():
    r = sp.rate_facts(ll_facts(), since="2020-12-31")
    assert r["mode"] == "statements only" and len(r["years"]) == 1
    y = r["years"][0]
    assert y["grade"] in range(1, 10) and math.isnan(y["dd"]) and not math.isnan(y["altman_z2"])


def test_market_input_changes_the_rating_when_the_stock_has_collapsed():
    plain = sp.rate_facts(ll_facts(), since="2020-12-31")["years"][0]
    market = {"market_equity": 20.0, "sigma_e": 1.0, "past_return": -0.8}
    stressed = sp.rate_facts(ll_facts(), market=market, since="2020-12-31")["years"][0]
    assert not math.isnan(stressed["dd"]) and stressed["score"] < plain["score"]


def test_dell_filing_lines_are_consistent():
    f = edgar.load(FIX / "DELL_edgar_2026-11-05.json.gz")
    y = sp.statements(f)[-1]
    parsed = edgar.parse(f)
    assert y["at"] == pytest.approx(dict(parsed["annual"]["TotalAssets"])["2026-01-31"] / M)
    assert y["dlc"] + y["dltt"] == pytest.approx(dict(parsed["annual"]["TotalDebt"])["2026-01-31"] / M)
    assert y["lt"] == pytest.approx(103756.0)  # the filed Liabilities line
    s = cs.spread(y)
    assert s["debt_assets"] == pytest.approx((y["dlc"] + y["dltt"]) / y["at"])


@pytest.mark.parametrize("ticker", ["DELL", "MSFT", "INTC", "ON", "JPM", "O"])
def test_fixture_companies_rate_without_error(ticker):
    r = sp.rate_facts(edgar.load(FIX / f"{ticker}_edgar_2026-11-05.json.gz"))
    assert r["years"] and all(y["grade"] is None or 1 <= y["grade"] <= 9 for y in r["years"])


def test_market_inputs():
    closes = [100.0, 110.0] * 7  # alternating +10% and -9.09%
    m = sp.market_inputs(closes, 5.0)
    last = closes[-13:]
    r = [last[i + 1] / last[i] - 1 for i in range(12)]
    assert m["market_equity"] == pytest.approx(closes[-1] * 5.0)
    assert m["sigma_e"] == pytest.approx(statistics.stdev(r) * math.sqrt(12))
    assert m["past_return"] == pytest.approx(last[-1] / last[0] - 1)
    assert sp.market_inputs([100.0] * 5, 5.0) is None  # too little history
    assert sp.market_inputs([100.0] * 13, 0) is None


def test_monthly_closes_takes_the_last_close_of_each_month():
    daily = [["2026-01-02", 10.0], ["2026-01-30", 12.0], ["2026-02-27", 9.0], ["2026-03-04", 8.0]]
    assert sp.monthly_closes(daily) == [12.0, 9.0, 8.0]


def test_market_from_the_dell_feeds():
    import gzip
    import json

    with gzip.open(FIX / "DELL_yahoo_2026-11-05.json.gz") as fh:
        feeds = json.load(fh)
    m = sp.market_from_feeds(feeds)
    assert m["market_equity"] == pytest.approx(feeds["summary"]["price"]["marketCap"]["raw"] / M)
    assert 0.1 < m["sigma_e"] < 2.0 and m["past_return"] > -1
    assert sp.market_from_feeds({"stock": []}) is None


def test_service_rates_from_filings_without_the_network(tmp_path):
    import service

    out = service.credit_rating(
        "LLFL", cache_dir=tmp_path, today="2026-11-17", with_market=False, sec_fetcher=lambda t: ll_facts()
    )
    assert (
        out["ticker"] == "LLFL"
        and out["mode"] == "statements only"
        and out["years"][0]["grade"] in range(1, 10)
    )
    assert (tmp_path / "LLFL_edgar_2026-11-17.json.gz").exists()  # saved once a day like the other feeds


def test_service_reports_a_missing_filing(tmp_path):
    import service

    def boom(t):
        raise LookupError("not an SEC registrant")

    with pytest.raises(service.DataError, match="SEC filings"):
        service.credit_rating(
            "ZZZZ", cache_dir=tmp_path, today="2026-11-17", with_market=False, sec_fetcher=boom
        )
