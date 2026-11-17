# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""
credit_spread.py -- spread a company's annual statements from SEC filings and rate it.

Input is the compact SEC companyfacts that edgar.py already fetches and saves. For each fiscal year it builds the
statement lines the rating reads (US$ millions), the cash flow available for debt service and the debt service
coverage, then runs credit_score.rate on them: ratios, Altman Z and Z'', distance to default (when a price history is
given), the score and the 1-to-9 grade.

    cash flow available for debt service (CFADS) = EBITDA less capital expenditure
    debt service                                 = interest expense + debt due within a year
    DSCR                                         = CFADS / debt service

EBITDA adds back the impairment and restructuring charges edgar.py already separates; operating income is as filed.
Total liabilities are the filed figure, or assets less equity when a company does not tag one. A line a company does not
file stays missing, and the rating re-weights what is left (credit_score.score).

Market inputs. The distance to default needs the equity value, a year of monthly returns and the debt. Pass monthly
closes (oldest first, 13 or more) and the share count to market_inputs(); without them the rating rests on the
statements alone, which is the case for a private borrower.
"""

from __future__ import annotations

import math

import edgar

import credit_score as cs

USD_M = 1e6
NAN = float("nan")


def _series(parsed: dict, name: str) -> dict[str, float]:
    return dict(parsed["annual"].get(name) or [])


def _near(date: str, series: dict[str, float], tol: int = 7) -> float | None:
    """Value of the series at the date nearest within tol days (the parser and the filing use slightly different ends)."""
    best = min(series, key=lambda d: abs(edgar._days(date, d)), default=None)
    return series[best] if best is not None and abs(edgar._days(date, best)) <= tol else None


def statements(facts: dict, since: str = "2018-12-31") -> list[dict]:
    """One dict per fiscal year, oldest first: the statement lines in US$ millions, plus the year-end date."""
    parsed = edgar.parse(facts, since=since)
    ends = [e for e in edgar._fy_ends(facts) if e >= since]
    raw = {k: _series(parsed, k) for k in parsed["annual"]}
    liab = edgar._by_tags(facts, ["Liabilities"], edgar.USD, lambda r: r[1] if r[1] >= since else None)
    retained = edgar._by_tags(
        facts, ["RetainedEarningsAccumulatedDeficit"], edgar.USD, lambda r: r[1] if r[1] >= since else None
    )
    rows = []
    for e in ends:
        m = edgar._month_end(e)

        def g(name, m=m):
            v = _near(m, raw.get(name) or {})
            return None if v is None else float(v)

        assets, equity = g("TotalAssets"), g("CommonStockEquity")
        if assets is None:
            continue
        lt = _near(e, liab)
        if lt is None and equity is not None:
            lt = assets - equity - (g("MinorityInterest") or 0.0)
        total_debt, long_debt = g("TotalDebt"), g("LongTermDebt")
        dltt = long_debt if long_debt is not None else (total_debt if total_debt is not None else None)
        dlc = max(total_debt - dltt, 0.0) if total_debt is not None and dltt is not None else None
        special = g("OperatingIncome")
        reported = g("OperatingIncomeReported")
        oiadp = reported if reported is not None else special
        da = g("ReconciledDepreciation")
        oibdp = special + da if special is not None and da is not None else None
        capex = g("CapitalExpenditure")
        row = {
            "date": e,
            "at": assets,
            "lt": lt,
            "sale": g("TotalRevenue"),
            "ni": g("NetIncome"),
            "oibdp": oibdp,
            "oiadp": oiadp,
            "xint": abs(g("InterestExpense")) if g("InterestExpense") is not None else None,
            "act": g("CurrentAssets"),
            "lct": g("CurrentLiabilities"),
            "dlc": dlc,
            "dltt": dltt,
            "re": _near(e, retained),
            "ceq": equity,
            "capex": abs(capex) if capex is not None else None,
        }
        rows.append({k: (v / USD_M if k != "date" and v is not None else v) for k, v in row.items()})
    return rows


def coverage(f: dict) -> dict:
    """CFADS, debt service and DSCR for one statement year."""
    ebitda, capex = f.get("oibdp"), f.get("capex")
    cfads = ebitda - capex if ebitda is not None and capex is not None else NAN
    ds = (
        (f.get("xint") or 0.0) + (f.get("dlc") or 0.0)
        if f.get("xint") is not None or f.get("dlc") is not None
        else NAN
    )
    return {
        "cfads": cfads,
        "debt_service": ds,
        "dscr": cfads / ds if ds and not math.isnan(ds) and ds > 0 else NAN,
    }


def monthly_closes(daily: list) -> list[float]:
    """Last close of each calendar month from [[date, close], ...] (the latest month is the price to date)."""
    last: dict[str, float] = {}
    for d, c in sorted(daily):
        if c is not None and c > 0:
            last[d[:7]] = float(c)
    return list(last.values())


def market_inputs(
    closes: list[float], shares_millions: float, market_equity: float | None = None
) -> dict | None:
    """Equity value, annualised volatility and one-year return from monthly closes (oldest first, 13 or more).

    market_equity (US$ millions) overrides price times shares when the feed gives the equity value directly."""
    if (
        len(closes) < 13
        or not (shares_millions or market_equity)
        or any(c is None or c <= 0 for c in closes[-13:])
    ):
        return None
    c = closes[-13:]
    r = [c[i + 1] / c[i] - 1 for i in range(12)]
    mean = sum(r) / 12
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 11)
    return {
        "market_equity": market_equity or c[-1] * shares_millions,
        "sigma_e": sd * math.sqrt(12),
        "past_return": c[-1] / c[0] - 1,
    }


def rate_facts(facts: dict, market: dict | None = None, since: str = "2018-12-31") -> dict:
    """Rate every fiscal year in the filings. A market dict (market_inputs) applies to the latest year only."""
    years = []
    rows = statements(facts, since)
    for i, f in enumerate(rows):
        use = market if market and i == len(rows) - 1 else None
        r = cs.rate(f, **(use or {}))
        years.append({**f, **coverage(f), **r})
    return {
        "name": facts.get("name"),
        "years": years,
        "mode": "statements and market" if market else "statements only",
    }


def market_from_feeds(feeds: dict) -> dict | None:
    """market_inputs from the Yahoo feeds the app already holds: daily closes and the market capitalisation."""
    try:
        cap = feeds["summary"]["price"]["marketCap"]["raw"] / USD_M
    except (KeyError, TypeError):
        return None
    return market_inputs(monthly_closes(feeds.get("stock") or []), 0.0, market_equity=cap)
