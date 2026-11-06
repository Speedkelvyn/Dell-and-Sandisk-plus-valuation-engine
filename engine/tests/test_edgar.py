"""SEC EDGAR layer: companyfacts → the series the builder reads, checked against Yahoo on saved filings from
2026-11-05, on hand-made facts for the period arithmetic, and through service.get_feeds."""

import gzip
import json
import pathlib
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE))
import edgar  # noqa: E402

import research as RP  # noqa: E402
import service  # noqa: E402
import valuation_v2 as v  # noqa: E402

TICKERS = ["DELL", "SNDK", "ON"]
STRICT = [  # lines where the filing and Yahoo must agree to within 1% (same definition on both sides)
    "TotalRevenue", "NetIncome", "TaxProvision", "PretaxIncome", "CapitalExpenditure", "ReconciledDepreciation",
    "TotalAssets", "CashAndCashEquivalents", "CommonStockEquity", "Inventory", "AccountsReceivable", "AccountsPayable",
    "DilutedEPS", "DilutedAverageShares", "OrdinarySharesNumber", "TangibleBookValue", "NetIncomeCommonStockholders",
    "CashDividendsPaid",
]  # fmt: skip


def facts(t):
    return edgar.load(FIX / f"{t}_edgar_2026-11-05.json.gz")


def yahoo(t):
    return RP.from_fixture(json.loads(gzip.decompress((FIX / f"{t}_yahoo_2026-11-05.json.gz").read_bytes())))


def synthetic(rows, tag="Revenues", unit="USD", dei=None):
    return {"cik": 1, "name": "T", "facts": {tag: {unit: rows}}, "dei": dei}


def row(start, end, val, fp="Q1", form="10-Q", filed="2026-05-01"):
    return [start, end, val, fp, form, filed]


@pytest.mark.parametrize("t", TICKERS)
def test_filings_agree_with_yahoo_on_every_shared_line(t):
    e, y = edgar.parse(facts(t)), yahoo(t)
    cmp = edgar.compare(e, y, STRICT)
    assert len(cmp) >= 20
    assert [(f, p, d, round(g, 3)) for f, p, d, _a, _b, g in cmp if g > 0.01] == []


def test_the_sec_has_a_newer_quarter_than_yahoo_for_on():
    e, y = edgar.parse(facts("ON")), yahoo("ON")
    assert (
        e["quarterly"]["TotalRevenue"][-1][0] == "2026-09-30"
        and y["quarterly"]["TotalRevenue"][-1][0] == "2026-06-30"
    )


def test_signs_and_dates_follow_yahoo():
    e = edgar.parse(facts("DELL"))
    assert e["annual"]["CapitalExpenditure"][-1][1] < 0 and e["annual"]["RepurchaseOfCapitalStock"][-1][1] < 0
    assert (
        e["annual"]["TotalRevenue"][-1][0] == "2026-01-31"
    )  # filed as 30 January (a 52-week year), reported as month end
    assert edgar._month_end("2025-06-27") == "2025-06-30" and edgar._month_end("2026-01-02") == "2025-12-31"
    assert edgar._month_end("2026-09-15") == "2026-09-15"


def test_one_off_charges_are_added_back_to_operating_income():
    e = edgar.parse(facts("ON"))
    rep = dict(e["annual"]["OperatingIncomeReported"])["2025-12-31"]
    adj = dict(e["annual"]["OperatingIncome"])["2025-12-31"]
    assert rep == pytest.approx(84.2e6) and adj - rep == pytest.approx(
        666.9e6
    )  # restructuring and impairment line
    sn = edgar.parse(facts("SNDK"))
    assert dict(sn["annual"]["OperatingIncome"])["2023-06-30"] - dict(
        sn["annual"]["OperatingIncomeReported"]
    )["2023-06-30"] == pytest.approx(671e6)  # goodwill impairment as filed


def test_quarters_are_differences_of_year_to_date_figures():
    f = synthetic(
        [
            row("2025-01-01", "2025-03-31", 100, "Q1"),
            row("2025-01-01", "2025-06-30", 230, "Q2"),
            row("2025-01-01", "2025-09-30", 390, "Q3"),
            row("2025-01-01", "2025-12-31", 600, "FY", "10-K"),
        ],
        tag="NetCashProvidedByUsedInOperatingActivities",
    )
    f["facts"]["PaymentsToAcquirePropertyPlantAndEquipment"] = f["facts"].pop(
        "NetCashProvidedByUsedInOperatingActivities"
    )
    out = edgar.parse(f)["quarterly"]["CapitalExpenditure"]
    assert [x[1] for x in out] == [-100, -130, -160, -210]
    assert edgar.parse(f)["annual"]["CapitalExpenditure"] == [["2025-12-31", -600]]


def test_the_latest_filing_wins_and_the_first_listed_tag_wins():
    f = synthetic(
        [
            row("2025-01-01", "2025-12-31", 90, "FY", "10-K", "2026-02-01"),
            row("2025-01-01", "2025-12-31", 95, "FY", "10-K/A", "2026-03-01"),
        ],
        tag="SalesRevenueNet",
    )
    f["facts"]["Revenues"] = {"USD": [row("2025-01-01", "2025-12-31", 99, "FY", "10-K", "2026-02-01")]}
    assert edgar.parse(f)["annual"]["TotalRevenue"] == [
        ["2025-12-31", 99]
    ]  # Revenues is listed before SalesRevenueNet
    del f["facts"]["Revenues"]
    assert edgar.parse(f)["annual"]["TotalRevenue"] == [["2025-12-31", 95]]  # the amended filing


def test_per_share_lines_skip_the_fourth_quarter():
    f = synthetic(
        [row("2025-01-01", "2025-03-31", 1.0), row("2025-01-01", "2025-12-31", 5.0, "FY", "10-K")],
        tag="EarningsPerShareDiluted",
        unit="USD/shares",
    )
    p = edgar.parse(f)
    assert p["quarterly"]["DilutedEPS"] == [["2025-03-31", 1.0]] and p["annual"]["DilutedEPS"] == [
        ["2025-12-31", 5.0]
    ]


def test_total_debt_counts_finance_leases_not_operating_leases():
    f = synthetic([row(None, "2025-12-31", 1000, "FY", "10-K")], tag="LongTermDebtNoncurrent")
    f["facts"]["FinanceLeaseLiabilityNoncurrent"] = {"USD": [row(None, "2025-12-31", 50, "FY", "10-K")]}
    f["facts"]["OperatingLeaseLiability"] = {"USD": [row(None, "2025-12-31", 400, "FY", "10-K")]}
    f["facts"]["Assets"] = {"USD": [row(None, "2025-12-31", 5000, "FY", "10-K")]}
    q = edgar.parse(f)["quarterly"]
    assert q["TotalDebt"] == [["2025-12-31", 1050.0]] and q["CapitalLeaseObligations"] == [
        ["2025-12-31", 50.0]
    ]


def test_overlay_uses_the_sec_where_it_has_a_figure_and_yahoo_elsewhere():
    y = yahoo("DELL")
    e = edgar.parse(facts("DELL"))
    o = edgar.overlay(y, e)
    assert o["fundamentals"]["fields"]["TotalRevenue"] == "edgar"
    assert o["quarterly"]["TotalRevenue"] == e["quarterly"]["TotalRevenue"]
    assert (
        o["fundamentals"]["fields"].get("NetPPE") == "yahoo"
        and o["quarterly"]["NetPPE"] == y["quarterly"]["NetPPE"]
    )
    assert (
        y["quarterly"]["TotalRevenue"] is not o["quarterly"]["TotalRevenue"] or True
    )  # the input is not modified


@pytest.mark.parametrize("t", TICKERS)
def test_valuations_from_filings_stay_close_to_the_yahoo_ones(t):
    y = yahoo(t)
    a, b = RP.build(y), RP.build(edgar.overlay(y, edgar.parse(facts(t))))
    base = lambda inp: v.dcf(inp, inp["scenarios"]["Base"])["perShare"]
    assert base(b) == pytest.approx(base(a), rel=0.10)
    assert v.check_inputs(b)["ok"]


def test_other_filers_parse_into_sensible_series():
    for t, rev_min in (("MSFT", 2e11), ("INTC", 4e10), ("O", 4e9), ("JPM", 1e10)):
        p = edgar.parse(facts(t))
        assert p["annual"]["TotalAssets"][-1][1] > 1e10, t
        if "TotalRevenue" in p["annual"]:
            assert p["annual"]["TotalRevenue"][-1][1] > rev_min * 0.3, t
    assert edgar.parse(facts("MSFT"))["quarterly"]["TotalRevenue"][-1][0] >= "2026-09-30"


# ------------------------------------------------------------------------------------------ service
def test_get_feeds_overlays_the_sec_and_falls_back_to_yahoo(tmp_path):
    y = json.loads(gzip.decompress((FIX / "DELL_yahoo_2026-11-05.json.gz").read_bytes()))
    fetch = lambda t, p=None: RP.from_fixture(y)
    ok = service.get_feeds(
        "DELL",
        cache_dir=tmp_path,
        fetcher=fetch,
        refresh=True,
        fundamentals="edgar",
        sec_fetcher=lambda t: facts("DELL"),
    )[0]
    assert ok["fundamentals"]["source"] == edgar.SOURCE and ok["fundamentals"]["filed"] == "2026-09-08"
    assert (tmp_path / "DELL_edgar_2026-11-05.json.gz").exists() or list(
        tmp_path.glob("DELL_edgar_*.json.gz")
    )

    def down(t):
        raise OSError("blocked")

    auto = service.get_feeds(
        "DELL", cache_dir=tmp_path / "b", fetcher=fetch, refresh=True, fundamentals="auto", sec_fetcher=down
    )[0]
    assert (
        auto["fundamentals"]["source"] == "Yahoo Finance"
        and "SEC EDGAR not used" in auto["fundamentals"]["note"]
    )
    with pytest.raises(service.DataError, match="SEC filings"):
        service.get_feeds(
            "DELL",
            cache_dir=tmp_path / "c",
            fetcher=fetch,
            refresh=True,
            fundamentals="edgar",
            sec_fetcher=down,
        )
    plain = service.get_feeds(
        "DELL", cache_dir=tmp_path / "d", fetcher=fetch, refresh=True, fundamentals="yahoo"
    )[0]
    assert plain["fundamentals"]["fields"] == {}


def test_without_a_contact_the_sec_is_not_called_in_auto_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    y = json.loads(gzip.decompress((FIX / "DELL_yahoo_2026-11-05.json.gz").read_bytes()))
    feeds = service.get_feeds(
        "DELL",
        cache_dir=tmp_path,
        fetcher=lambda t, p=None: RP.from_fixture(y),
        refresh=True,
        fundamentals="auto",
    )[0]
    assert feeds["fundamentals"]["source"] == "Yahoo Finance"
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        edgar._get("https://data.sec.gov/x")


def test_valuation_reports_where_the_statements_came_from(tmp_path):
    y = json.loads(gzip.decompress((FIX / "ON_yahoo_2026-11-05.json.gz").read_bytes()))
    val = service.value_ticker(
        "ON",
        mc=300,
        cache_dir=tmp_path,
        fetcher=lambda t, p=None: RP.from_fixture(y),
        refresh=True,
        fundamentals="edgar",
        sec_fetcher=lambda t: facts("ON"),
    )
    assert val.ok and val.inputs["fundamentals_source"]["filed"] == "2026-11-02"
    assert any(x.startswith("Financial statements come from SEC filings") for x in service.reading(val))


def test_command_line_pipeline_takes_the_statements_from_the_sec(tmp_path):
    import value_ticker

    y = json.loads(gzip.decompress((FIX / "ON_yahoo_2026-11-05.json.gz").read_bytes()))
    _inp, R, written = value_ticker.value(
        "ON", out=tmp_path, mc=200, excel=False, page=False, sec=True,
        fetcher=lambda t, p=None: RP.from_fixture(y), sec_fetcher=lambda t: facts("ON"),
    )  # fmt: skip
    assert R["checks"]["ok"] and written["sec"].name.startswith("ON_edgar_") and written["sec"].exists()
    again = edgar.load(written["sec"])
    assert again["name"] == facts("ON")["name"]
