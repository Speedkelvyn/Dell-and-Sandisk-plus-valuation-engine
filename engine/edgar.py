"""SEC EDGAR fundamentals: companyfacts (XBRL) → the annual/quarterly series that research.build() reads.

The SEC publishes every number a US company files, tagged and dated, free and without a key. This module turns
that file into the same shape the Yahoo feed has ({Name: [[date, value], ...]}, Yahoo's field names and signs), so
the builders, both engines and every test downstream stay as they are. Three things need care:

* Flows (revenue, cash flow) are reported as year-to-date in 10-Qs. A discrete quarter is the difference between
  consecutive year-to-date figures, and the fourth quarter is the full year less nine months.
* Companies change the tag they use over time, so each field tries a list of tags and takes the first one that has a
  value for each period.
* A restated number is filed again later. The latest filing for a period wins.

Per-share fields (EPS, average shares) cannot be derived by subtraction, so a fourth quarter is left out for those.
Parsing works on a saved file; fetching needs the network and an identifying User-Agent (SEC rule).
"""

import datetime as dt
import gzip
import json
import os
import pathlib
import urllib.request

SOURCE = "SEC EDGAR (XBRL companyfacts)"
USD, PER_SHARE, SHARES = "USD", "USD/shares", "shares"

# Yahoo field → (kind, unit, sign, tags in order of preference). kind: flow, per_share_flow, instant.
FIELDS = {
    "TotalRevenue": (
        "flow",
        USD,
        1,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ],
    ),
    "OperatingIncome": ("flow", USD, 1, ["OperatingIncomeLoss"]),
    "PretaxIncome": (
        "flow",
        USD,
        1,
        [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ],
    ),
    "TaxProvision": ("flow", USD, 1, ["IncomeTaxExpenseBenefit"]),
    "NetIncome": ("flow", USD, 1, ["NetIncomeLoss", "ProfitLoss"]),
    "NetIncomeCommonStockholders": (
        "flow",
        USD,
        1,
        ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    ),
    "InterestExpense": (
        "flow",
        USD,
        1,
        [
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestExpenseDebt",
            "InterestExpenseLongTermDebt",
        ],
    ),
    "DilutedEPS": ("per_share_flow", PER_SHARE, 1, ["EarningsPerShareDiluted"]),
    "DilutedAverageShares": (
        "per_share_flow",
        SHARES,
        1,
        ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    ),
    "ReconciledDepreciation": (
        "flow",
        USD,
        1,
        [
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "Depreciation",
        ],
    ),
    "CapitalExpenditure": (
        "flow",
        USD,
        -1,
        ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    ),
    "CashDividendsPaid": ("flow", USD, -1, ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    "RepurchaseOfCapitalStock": ("flow", USD, -1, ["PaymentsForRepurchaseOfCommonStock"]),
    "AccountsReceivable": ("instant", USD, 1, ["AccountsReceivableNetCurrent"]),
    "AccountsPayable": ("instant", USD, 1, ["AccountsPayableCurrent"]),
    "Inventory": ("instant", USD, 1, ["InventoryNet"]),
    "CashAndCashEquivalents": ("instant", USD, 1, ["CashAndCashEquivalentsAtCarryingValue"]),
    "TotalAssets": ("instant", USD, 1, ["Assets"]),
    "CommonStockEquity": ("instant", USD, 1, ["StockholdersEquity"]),
    "MinorityInterest": ("instant", USD, 1, ["MinorityInterest"]),
    "OrdinarySharesNumber": ("instant", SHARES, 1, ["CommonStockSharesOutstanding"]),
    # the lines the earnings-quality grade (Beneish M-score, accruals, cash conversion) reads
    "OperatingCashFlow": (
        "flow",
        USD,
        1,
        [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
    ),
    "CostOfRevenue": (
        "flow",
        USD,
        1,
        ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"],
    ),
    "SellingGeneralAndAdministration": ("flow", USD, 1, ["SellingGeneralAndAdministrativeExpense"]),
    "CurrentAssets": ("instant", USD, 1, ["AssetsCurrent"]),
    "CurrentLiabilities": ("instant", USD, 1, ["LiabilitiesCurrent"]),
    "NetPPE": (
        "instant",
        USD,
        1,
        [
            "PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        ],
    ),
    "LongTermDebt": (
        "instant",
        USD,
        1,
        ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"],
    ),
}
DEBT_CURRENT = ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]
DEBT_OTHER = ["ShortTermBorrowings", "CommercialPaper"]
DEBT_LONG = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"]
LEASES = ["FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent"]
# One-off charges Yahoo leaves out of operating income. The combined line is used when a company reports it,
# otherwise the separate impairment lines are added.
SPECIAL_COMBINED = "RestructuringCostsAndAssetImpairmentCharges"
SPECIAL_PARTS = [
    "GoodwillImpairmentLoss",
    "AssetImpairmentCharges",
    "ImpairmentOfIntangibleAssetsExcludingGoodwill",
]


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _rows(facts, tag, unit):
    """[(start, end, value, filed)] for one tag, the latest filing winning for each (start, end)."""
    best = {}
    for start, end, val, _fp, _form, filed in ((facts.get("facts") or {}).get(tag) or {}).get(unit) or []:
        k = (start, end)
        if val is not None and (k not in best or filed >= best[k][3]):
            best[k] = (start, end, val, filed)
    return list(best.values())


def _by_tags(facts, tags, unit, pick):
    """Merge several tags: for each period the first tag in the list that has it wins."""
    out = {}
    for tag in reversed(tags):  # later tags first, so earlier (preferred) tags overwrite them
        for r in _rows(facts, tag, unit):
            key = pick(r)
            if key is not None:
                out[key] = r[2]
    return out


def _flow_series(facts, tags, unit, discrete=True):
    """(annual, quarterly): {end: value}. Quarters are direct 3-month facts or differences of year-to-date facts."""
    ann, direct, cum = {}, {}, {}
    for tag in reversed(tags):
        for s, e, v, _ in _rows(facts, tag, unit):
            if s is None:
                continue
            n = _days(s, e)
            if 340 <= n <= 390:
                ann[e] = v
            elif 80 <= n <= 100:
                direct[e] = v
            if n >= 80:
                cum.setdefault(s, {})[e] = v
    quarters = dict(direct)
    if discrete:
        for s, chain in cum.items():
            prev_end, prev_val = None, 0.0
            for e in sorted(chain):
                n = _days(s, e)
                step = _days(prev_end, e) if prev_end else n
                if 80 <= step <= 100 and e not in quarters and (prev_end or n <= 100):
                    quarters[e] = chain[e] - prev_val
                if 80 <= step <= 100 or prev_end is None or n > 340:
                    prev_end, prev_val = e, chain[e]
    return ann, quarters


def _fy_ends(facts):
    """Fiscal year-end dates, from the 10-K facts of the first tag that has them."""
    ends = set()
    for t in ("Assets", "StockholdersEquity", "Revenues", "NetIncomeLoss"):
        for _s, e, _v, _fp, form, _f in (
            next(iter(((facts.get("facts") or {}).get(t) or {}).values()), []) or []
        ):
            if form and form.startswith("10-K"):
                ends.add(e)
        if ends:
            break
    return sorted(ends)


def _near(date, dates, tol=7):
    return any(abs(_days(date, d)) <= tol for d in dates)


def _month_end(date):
    """52/53-week years end on a Friday near month end (Dell 30 Jan, Sandisk 27 Jun); Yahoo reports month ends."""
    y, m, d = (int(x) for x in date.split("-"))
    if d >= 25 or d <= 5:
        if d <= 5:
            m, y = (m - 1, y) if m > 1 else (12, y - 1)
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        return (nxt - dt.timedelta(days=1)).isoformat()
    return date


def _sorted(d, sign=1):
    out = {}
    for e, v in sorted(d.items()):
        out[_month_end(e)] = sign * v
    return [[e, v] for e, v in sorted(out.items())]


def parse(facts, since="2020-12-31"):
    """companyfacts dict → {'annual': {Name: [[date, value], ...]}, 'quarterly': {...}, 'source': ..., 'filed': ...}."""
    annual, quarterly = {}, {}
    fy_ends = _fy_ends(facts)
    for name, (kind, unit, sign, tags) in FIELDS.items():
        if kind == "instant":
            ser = _by_tags(facts, tags, unit, lambda r: r[1] if r[1] >= since else None)
            if name == "OrdinarySharesNumber" and not ser:
                last = ((facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding") or [None])[-1]
                if last:
                    ser = {last[0]: last[1]}
            q = ser
            a = {e: v for e, v in ser.items() if _near(e, fy_ends)}
        else:
            a, q = _flow_series(facts, tags, unit, discrete=(kind == "flow"))
            a = {e: v for e, v in a.items() if e >= since}
            q = {e: v for e, v in q.items() if e >= since}
        if a:
            annual[name] = _sorted(a, sign)
        if q:
            quarterly[name] = _sorted(q, sign)
    _special(facts, annual, quarterly, since)
    for out in (annual, quarterly):
        _operating_fallback(out)
    for target, tgt_name in ((annual, "a"), (quarterly, "q")):
        _derived(facts, target, since, annual_only=(tgt_name == "a"), fy_ends=fy_ends)
    filed = max(
        (r[5] for per in (facts.get("facts") or {}).values() for rows in per.values() for r in rows),
        default=None,
    )
    return {
        "annual": annual,
        "quarterly": quarterly,
        "source": SOURCE,
        "filed": filed,
        "name": facts.get("name"),
    }


def _operating_fallback(out):
    """Some large filers (Exxon, Chevron, Pfizer, Merck, Nike) never tag an operating income. Yahoo's figure for them is
    income before tax plus interest expense, so the same is built here; OperatingIncomeReported stays absent so the
    page can tell the figure is derived."""
    if out.get("OperatingIncome") or not out.get("PretaxIncome"):
        return
    interest = {d: abs(v) for d, v in out.get("InterestExpense") or []}
    out["OperatingIncome"] = [[d, v + interest.get(d, 0.0)] for d, v in out["PretaxIncome"]]


def _special(facts, annual, quarterly, since):
    """OperatingIncome becomes reported income plus impairment and restructuring-impairment charges (Yahoo's
    definition, so margins are not distorted by one-offs); the figure as filed stays as OperatingIncomeReported."""
    a, q = _flow_series(facts, [SPECIAL_COMBINED], USD)
    if not a and not q:
        parts = [_flow_series(facts, [t], USD) for t in SPECIAL_PARTS]
        a, q = ({}, {})
        for pa, pq in parts:
            for e, v in pa.items():
                a[e] = a.get(e, 0.0) + v
            for e, v in pq.items():
                q[e] = q.get(e, 0.0) + v
    for out, add in ((annual, a), (quarterly, q)):
        base = out.get("OperatingIncome")
        if not base:
            continue
        out["OperatingIncomeReported"] = [list(r) for r in base]
        sp = {_month_end(e): v for e, v in add.items() if e >= since}
        out["OperatingIncome"] = [[d, v + sp.get(d, 0.0)] for d, v in base]


def _derived(facts, out, since, annual_only, fy_ends):
    """Total debt (with leases), lease obligations and tangible book value, which Yahoo reports as one line each."""

    def inst(tags):
        d = _by_tags(facts, tags, USD, lambda r: r[1] if r[1] >= since else None)
        return {e: v for e, v in d.items() if _near(e, fy_ends)} if annual_only else d

    cur, oth, lng, fin = inst(DEBT_CURRENT), inst(DEBT_OTHER), inst(DEBT_LONG), inst(LEASES)
    gw, ia, eq = (
        inst(["Goodwill"]),
        inst(["IntangibleAssetsNetExcludingGoodwill"]),
        inst(["StockholdersEquity"]),
    )
    debt, leases, tbv = {}, {}, {}
    for e in set(cur) | set(oth) | set(lng):
        debt[e] = sum(d.get(e, 0.0) for d in (cur, oth, lng, fin))
    for e in set(fin):
        leases[e] = fin[e]
    for e in eq:
        tbv[e] = eq[e] - gw.get(e, 0.0) - ia.get(e, 0.0)
    for name, d in (("TotalDebt", debt), ("CapitalLeaseObligations", leases), ("TangibleBookValue", tbv)):
        if d:
            out[name] = _sorted(d)


def overlay(feeds, parsed):
    """Feeds with the SEC series in place of Yahoo's, field by field (Yahoo stays where the SEC has no figure).

    feeds["fundamentals"] records which source each field came from, so the page and the tests can say so."""
    out = dict(feeds)
    used = {}
    for period in ("annual", "quarterly"):
        merged = dict(feeds.get(period) or {})
        for name, rows in (parsed.get(period) or {}).items():
            if rows:
                merged[name] = rows
                used[name] = "edgar"
        out[period] = merged
        # Debt and leases are one definition: if the SEC supplies debt, Yahoo's lease line must not be mixed in.
        if "TotalDebt" in (parsed.get(period) or {}) and "CapitalLeaseObligations" not in (
            parsed.get(period) or {}
        ):
            merged.pop("CapitalLeaseObligations", None)
    for name in feeds.get("annual") or {}:
        used.setdefault(name, "yahoo")
    out["fundamentals"] = {"source": SOURCE, "filed": parsed.get("filed"), "fields": used}
    return out


def load(path):
    p = pathlib.Path(path)
    raw = p.read_bytes()
    return json.loads(gzip.decompress(raw) if p.suffix == ".gz" else raw)


# ----------------------------------------------------------------------------------------------- network
UA_HELP = (
    "The SEC asks every program to identify itself. Set the environment variable SEC_USER_AGENT to your name and "
    "email, for example: SEC_USER_AGENT='Your Name you@example.com'."
)
SEC_FILES, SEC_DATA = (
    "https://www.sec.gov/files/company_tickers.json",
    "https://data.sec.gov/api/xbrl/companyfacts/",
)


def _get(url, ua=None, timeout=20):
    ua = ua or os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError(UA_HELP)
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body)


def cik_of(ticker, ua=None):
    for row in _get(SEC_FILES, ua).values():
        if row["ticker"].upper() == ticker.upper().replace("-", "."):
            return int(row["cik_str"])
    raise LookupError(f"{ticker} is not an SEC registrant (foreign companies that file 20-F are not covered)")


def fetch(ticker, ua=None, since="2020-12-31"):
    """Download and compact the companyfacts for one ticker (the same compact form the fixtures use)."""
    cik = cik_of(ticker, ua)
    j = _get(f"{SEC_DATA}CIK{cik:010d}.json", ua, timeout=60)
    g = (j.get("facts") or {}).get("us-gaap") or {}
    wanted = set()
    for _k, _u, _s, tags in FIELDS.values():
        wanted.update(tags)
    wanted.update(
        DEBT_CURRENT + DEBT_OTHER + DEBT_LONG + LEASES + ["Goodwill", "IntangibleAssetsNetExcludingGoodwill"]
    )
    wanted.update([SPECIAL_COMBINED, *SPECIAL_PARTS])
    out = {"cik": j.get("cik"), "name": j.get("entityName"), "facts": {}}
    for tag in wanted & set(g):
        for unit, arr in g[tag]["units"].items():
            rows = [
                [e.get("start"), e["end"], e["val"], e.get("fp"), e.get("form"), e.get("filed")]
                for e in arr
                if e["end"] >= since
            ]
            if rows:
                out["facts"].setdefault(tag, {})[unit] = rows
    dei = ((j.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if dei:
        out["dei"] = {
            "EntityCommonStockSharesOutstanding": [
                [e["end"], e["val"], e.get("form"), e.get("filed")] for e in dei["units"]["shares"]
            ]
        }
    return out


# ------------------------------------------------------------------------------------------ cross-check
CHECK = [
    "TotalRevenue",
    "OperatingIncome",
    "NetIncome",
    "CapitalExpenditure",
    "ReconciledDepreciation",
    "TotalAssets",
    "TotalDebt",
    "CashAndCashEquivalents",
    "CommonStockEquity",
]


def compare(edgar_series, yahoo_series, fields=CHECK):
    """Latest annual and quarterly value by field in both sources: [(field, period, edgar, yahoo, relative gap)]."""
    rows = []
    for period in ("annual", "quarterly"):
        for f in fields:
            e = dict(edgar_series[period].get(f) or [])
            y = dict(yahoo_series[period].get(f) or [])
            for d in sorted(set(e) & set(y))[-1:]:
                gap = abs(e[d] - y[d]) / max(abs(y[d]), 1.0)
                rows.append((f, period, d, e[d], y[d], gap))
    return rows
