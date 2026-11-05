"""macro.py: the live macro backdrop, and the three ways it enters a valuation.

Data (all free, no keys):
  Yahoo Finance daily charts: Treasury yields (^IRX, ^FVX, ^TNX, ^TYX), ^VIX and ^VIX3M, ^MOVE, the S&P 500, HYG/IEF/LQD,
  the dollar index, WTI crude, and the semiconductor (SMH) and technology (XLK) ETFs.
  FRED (fredgraph.csv): the 10-year less 3-month spread, the ICE BofA high-yield spread, unemployment, the real-time
  Sahm rule, Chicago Fed financial conditions, CPI, jobless claims, consumer sentiment, breakeven inflation, the real yield.

What it does to a valuation, and nothing else:
  1. Scenario weights. A 12-month recession probability (the New York Fed yield-curve probit, adjusted by the labour
     market, credit spreads and financial conditions) sets how much weight Bear, Base and Bull get in a
     probability-weighted DCF shown next to the fair value. The fair value itself is not changed.
  2. Monte Carlo width. VIX against its own five-year median scales the growth and margin shocks (0.75x to 1.75x).
  3. Overlays. Two named shocks a user can switch on, sized by the company's exposure: AI data-center spending
     slowing, and Chinese competition. They move growth and long-run margins through the engine's own knobs.

The risk-free rate already comes live from ^TNX in every valuation; the equity risk premium stays an editable
assumption (Damodaran's implied ERP), because no live series measures it better.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import statistics

YAHOO = {
    "^IRX": "3-month Treasury bill",
    "^FVX": "5-year Treasury yield",
    "^TNX": "10-year Treasury yield",
    "^TYX": "30-year Treasury yield",
    "^VIX": "VIX (S&P 500 30-day implied volatility)",
    "^VIX3M": "VIX 3-month",
    "^MOVE": "MOVE (Treasury implied volatility)",
    "^GSPC": "S&P 500",
    "HYG": "High-yield corporate bond ETF",
    "IEF": "7–10 year Treasury ETF",
    "LQD": "Investment-grade corporate bond ETF",
    "DX-Y.NYB": "US dollar index",
    "CL=F": "WTI crude oil",
    "SMH": "Semiconductor ETF",
    "XLK": "Technology ETF",
}
FRED = {
    "T10Y3M": "10-year less 3-month Treasury spread",
    "BAMLH0A0HYM2": "High-yield bond spread (ICE BofA OAS)",
    "UNRATE": "Unemployment rate",
    "SAHMREALTIME": "Sahm rule (real-time)",
    "NFCI": "Chicago Fed financial conditions (NFCI)",
    "CPIAUCSL": "Consumer prices (CPI)",
    "ICSA": "Initial jobless claims",
    "UMCSENT": "University of Michigan consumer sentiment",
    "T10YIE": "10-year breakeven inflation",
    "DFII10": "10-year real yield (TIPS)",
}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"

# New York Fed yield-curve model: P(recession in 12 months) = Φ(α + β × (10-year − 3-month spread, monthly average))
NYFED_ALPHA, NYFED_BETA = -0.5333, -0.6330
NEUTRAL_P = 0.15  # roughly the model's long-run average probability
NEUTRAL_WEIGHTS = {"Bear": 0.25, "Base": 0.50, "Bull": 0.25}


# ------------------------------------------------------------------- fetching
def parse_fred_csv(text: str) -> list[list]:
    """FRED's CSV (observation_date,SERIES) → [[date, value], ...]; '.' marks a missing observation."""
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows[1:]:
        if len(r) >= 2 and r[1] not in ("", "."):
            try:
                out.append([r[0], float(r[1])])
            except ValueError:
                continue
    return out


def fetch(start: str | None = None, yahoo_chart=None, fred_text=None) -> dict:
    """Pull every series. One failing series is recorded and skipped, never fatal.

    yahoo_chart(symbol) -> [[date, close], ...] and fred_text(series_id, start) -> CSV text can be passed in (tests,
    or another transport); by default Yahoo goes through yfinance's session and FRED through urllib.
    """
    start = start or (dt.date.today() - dt.timedelta(days=365 * 6)).isoformat()
    if yahoo_chart is None:
        yahoo_chart = _yahoo_chart
    if fred_text is None:
        fred_text = _fred_text
    out = {"yahoo": {}, "fred": {}, "errors": {}, "fetched": dt.datetime.now(dt.timezone.utc).isoformat()}
    for kind, names, get in [
        ("yahoo", YAHOO, yahoo_chart),
        ("fred", FRED, lambda s: parse_fred_csv(fred_text(s, start))),
    ]:
        failed = 0
        for s in names:
            if (
                failed >= 3 and not out[kind]
            ):  # three misses before any success: the source is down, stop asking
                out["errors"][s] = "skipped: source unreachable"
                continue
            try:
                out[kind][s] = get(s)
                failed = 0
            except (Exception, SystemExit) as e:  # network, symbol gone, rate limit, yfinance missing
                out["errors"][s] = f"{type(e).__name__}: {e}"
                failed += 1
    return out


_SESSION = []


def _yahoo_chart(symbol: str) -> list[list]:
    import fetch_yahoo
    import research as RP

    if not _SESSION:
        _SESSION.append(fetch_yahoo._session())
    return RP.closes(fetch_yahoo._chart(_SESSION[0], symbol, "5y"))


def _fred_text(series: str, start: str) -> str:
    import urllib.request

    req = urllib.request.Request(
        FRED_URL.format(id=series, start=start), headers={"User-Agent": "valuation-app"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


# ------------------------------------------------------------------ indicators
def _vals(s):
    return [x[1] for x in s or [] if x[1] is not None]


def _ago(s, days: int):
    """The value on or before `days` calendar days before the last observation."""
    if not s:
        return None
    end = dt.date.fromisoformat(s[-1][0]) - dt.timedelta(days=days)
    for d, x in reversed(s):
        if dt.date.fromisoformat(d) <= end:
            return x
    return None


def _pct_rank(s, x) -> float | None:
    v = _vals(s)
    return sum(1 for y in v if y <= x) / len(v) if v and x is not None else None


def _ma(s, n: int) -> float | None:
    v = _vals(s)[-n:]
    return sum(v) / len(v) if len(v) == n else None


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def nyfed_probability(spread_pct: float) -> float:
    """12-month recession probability from the 10-year less 3-month spread, in percentage points."""
    return _phi(NYFED_ALPHA + NYFED_BETA * spread_pct)


def indicators(data: dict) -> dict:
    """Latest readings with their change and a plain signal. Missing series are simply absent."""
    Y, F = data.get("yahoo") or {}, data.get("fred") or {}
    ind: dict[str, dict] = {}

    def add(key, label, value, unit, signal, detail="", source="", change=None, history=None):
        if value is None:
            return
        ind[key] = {
            "label": label, "value": value, "unit": unit, "signal": signal, "detail": detail, "source": source,
            "change": change, "history": history,
        }  # fmt: skip

    # yield curve: FRED's constant-maturity spread, else ^TNX − ^IRX
    curve = F.get("T10Y3M")
    if curve:
        last30 = [
            x
            for d, x in curve
            if dt.date.fromisoformat(d) > dt.date.fromisoformat(curve[-1][0]) - dt.timedelta(days=31)
        ]
        spread, src = sum(last30) / len(last30), "FRED T10Y3M, last month's average"
        hist = curve
    elif Y.get("^TNX") and Y.get("^IRX"):
        irx = dict(Y["^IRX"])
        hist = [[d, x - irx[d]] for d, x in Y["^TNX"] if d in irx]
        last30 = [x for _, x in hist[-21:]]
        spread, src = sum(last30) / len(last30), "^TNX − ^IRX (Yahoo), last 21 days"
    else:
        spread, hist, src = None, None, ""
    if spread is not None:
        add("curve", "Yield curve (10y − 3m)", spread, "pp",
            "inverted: recession warning" if spread < 0 else "flat" if spread < 0.5 else "positive",
            f"{'Inverted' if spread < 0 else 'Upward-sloping'}; an inverted curve has preceded every US recession since the 1960s",
            src, change=(hist[-1][1] - _ago(hist, 91)) if _ago(hist, 91) is not None else None, history=hist[-760:])  # fmt: skip
        p = nyfed_probability(spread)
        add("recession_curve", "Recession probability, yield-curve model", p, "prob",
            "high" if p > 0.4 else "elevated" if p > 0.25 else "low",
            "New York Fed probit on the 10-year less 3-month spread, 12 months ahead", "NY Fed model (Estrella-Mishkin)")  # fmt: skip

    sahm = F.get("SAHMREALTIME")
    if sahm:
        x = sahm[-1][1]
        add("sahm", "Sahm rule (unemployment momentum)", x, "pp",
            "recession signal" if x >= 0.5 else "watch" if x >= 0.3 else "no signal",
            "3-month average unemployment less its 12-month low; 0.5 or more has marked every recession start since 1970",
            f"FRED SAHMREALTIME, {sahm[-1][0][:7]}", change=x - (_ago(sahm, 92) or x), history=sahm)  # fmt: skip
    un = F.get("UNRATE")
    if un:
        x = un[-1][1]
        ch = x - _ago(un, 365) if _ago(un, 365) is not None else None
        add("unemployment", "Unemployment rate", x, "pct",
            "rising" if ch is not None and ch >= 0.5 else "stable", "", f"FRED UNRATE, {un[-1][0][:7]}", change=ch, history=un)  # fmt: skip
    claims = F.get("ICSA")
    if claims and len(claims) >= 26:
        a4, a26 = sum(_vals(claims)[-4:]) / 4, sum(_vals(claims)[-26:]) / 26
        add("claims", "Initial jobless claims (4-week average)", a4, "count",
            "rising" if a4 > 1.15 * a26 else "stable", f"{a4 / a26 - 1:+.0%} against the 26-week average",
            f"FRED ICSA, week of {claims[-1][0]}", change=a4 / a26 - 1)  # fmt: skip

    hy = F.get("BAMLH0A0HYM2")
    if hy:
        x, pr = hy[-1][1], _pct_rank(hy, hy[-1][1])
        ch = x - _ago(hy, 91) if _ago(hy, 91) is not None else None
        add("hy_spread", "High-yield credit spread", x, "pct",
            "wide: credit stress" if x > 5.5 or (pr is not None and pr > 0.85) else "widening" if ch is not None and ch > 0.75 else "tight",
            f"{pr:.0%} percentile of the last {len(hy) // 250 or 1} years" if pr is not None else "",
            f"FRED BAMLH0A0HYM2, {hy[-1][0]}", change=ch, history=hy)  # fmt: skip
    elif Y.get("HYG") and Y.get("IEF"):
        ief = dict(Y["IEF"])
        ratio = [[d, x / ief[d]] for d, x in Y["HYG"] if d in ief]
        r3 = ratio[-1][1] / _ago(ratio, 91) - 1 if _ago(ratio, 91) else None
        add("hy_spread", "High-yield vs Treasuries (HYG/IEF, 3-month change)", r3, "ret",
            "credit underperforming" if r3 is not None and r3 < -0.03 else "calm", "", "Yahoo HYG and IEF", change=r3)  # fmt: skip

    nfci = F.get("NFCI")
    if nfci:
        x = nfci[-1][1]
        add("nfci", "Financial conditions (Chicago Fed NFCI)", x, "z",
            "tighter than average" if x > 0 else "looser than average",
            "Zero is average; positive means tighter than usual", f"FRED NFCI, week of {nfci[-1][0]}",
            change=x - (_ago(nfci, 91) or x), history=nfci)  # fmt: skip

    vix, v3 = Y.get("^VIX"), Y.get("^VIX3M")
    if vix:
        x, med = vix[-1][1], statistics.median(_vals(vix))
        term = x / v3[-1][1] if v3 else None
        add("vix", "VIX", x, "level",
            "stress" if x > 30 else "elevated" if x > med * 1.2 else "calm",
            f"five-year median {med:.1f}" + (f"; VIX ÷ VIX3M {term:.2f}" + (" (inverted: near-term fear)" if term > 1 else "") if term else ""),
            f"Yahoo ^VIX, {vix[-1][0]}", change=x - (_ago(vix, 91) or x), history=vix[-760:])  # fmt: skip
        ind["vix"]["median5y"] = med
        if term is not None:
            ind["vix"]["term"] = term
    move = Y.get("^MOVE")
    if move:
        x, med = move[-1][1], statistics.median(_vals(move))
        add("move", "MOVE (bond volatility)", x, "level", "elevated" if x > med * 1.2 else "normal",
            f"five-year median {med:.0f}", f"Yahoo ^MOVE, {move[-1][0]}", change=x - (_ago(move, 91) or x))  # fmt: skip

    for key, sym, label in [("tnx", "^TNX", "10-year Treasury yield"), ("irx", "^IRX", "3-month T-bill")]:
        s = Y.get(sym)
        if s:
            add(key, label, s[-1][1], "pct", "", "", f"Yahoo {sym}, {s[-1][0]}", change=s[-1][1] - (_ago(s, 91) or s[-1][1]), history=s[-760:])  # fmt: skip
    ry, be = F.get("DFII10"), F.get("T10YIE")
    if ry:
        add("real_yield", "10-year real yield (TIPS)", ry[-1][1], "pct", "restrictive" if ry[-1][1] > 2 else "",
            "Above 2% is a high hurdle for long-duration growth stocks", f"FRED DFII10, {ry[-1][0]}",
            change=ry[-1][1] - (_ago(ry, 91) or ry[-1][1]))  # fmt: skip
    if be:
        add("breakeven", "10-year breakeven inflation", be[-1][1], "pct", "", "", f"FRED T10YIE, {be[-1][0]}",
            change=be[-1][1] - (_ago(be, 91) or be[-1][1]))  # fmt: skip
    cpi = F.get("CPIAUCSL")
    if cpi and _ago(cpi, 365):
        x = cpi[-1][1] / _ago(cpi, 365) - 1
        add("cpi", "Inflation (CPI, 12 months)", x, "ret", "above target" if x > 0.03 else "near target", "",
            f"FRED CPIAUCSL, {cpi[-1][0][:7]}")  # fmt: skip
    cs = F.get("UMCSENT")
    if cs:
        x = cs[-1][1]
        add("consumer", "Consumer sentiment (Michigan)", x, "level", "weak" if x < 60 else "normal",
            "Long-run average is about 85", f"FRED UMCSENT, {cs[-1][0][:7]}", change=x - (_ago(cs, 92) or x))  # fmt: skip

    def ret3m(sym):
        s = Y.get(sym)
        a = _ago(s, 91) if s else None
        return s[-1][1] / a - 1 if s and a else None

    spx = Y.get("^GSPC")
    if spx:
        ma200, hi = _ma(spx, 200), max(_vals(spx)[-252:])
        add("spx", "S&P 500", spx[-1][1], "level",
            "below its 200-day average" if ma200 and spx[-1][1] < ma200 else "above its 200-day average",
            f"{spx[-1][1] / hi - 1:+.1%} from its 52-week high; 3 months {ret3m('^GSPC'):+.1%}" if ret3m("^GSPC") is not None else "",
            f"Yahoo ^GSPC, {spx[-1][0]}", change=ret3m("^GSPC"), history=spx[-760:])  # fmt: skip
    smh, s3 = ret3m("SMH"), ret3m("^GSPC")
    if smh is not None and s3 is not None:
        rel = smh - s3
        add("ai_trade", "Semiconductors vs S&P 500 (3 months)", rel, "ret",
            "AI trade unwinding" if rel < -0.10 else "AI trade leading" if rel > 0.10 else "in line",
            f"SMH {smh:+.1%} against the S&P 500 {s3:+.1%}", "Yahoo SMH and ^GSPC")  # fmt: skip
    for key, sym, label in [
        ("dollar", "DX-Y.NYB", "US dollar index (3 months)"),
        ("oil", "CL=F", "WTI crude (3 months)"),
    ]:
        r = ret3m(sym)
        if r is not None:
            s = Y[sym]
            add(key, label, r, "ret", "sharp move" if abs(r) > 0.1 else "", f"level {s[-1][1]:.2f}", f"Yahoo {sym}, {s[-1][0]}")  # fmt: skip
    return ind


# --------------------------------------------------------------------- regime
def regime(ind: dict) -> dict:
    """A 12-month recession probability and a short label, with every adjustment listed.

    Start from the yield-curve model, then add: Sahm rule triggered +0.35 (watch zone +0.10); unemployment up 0.5pp
    in a year +0.10; claims 15% above their 26-week average +0.05; high-yield spread wide +0.15 or widening +0.05;
    financial conditions tighter than average +0.05. Bounded at 2% and 95%.
    """
    drivers = []
    p = ind.get("recession_curve", {}).get("value")
    if p is None:
        p = NEUTRAL_P
        drivers.append(("No yield-curve data; starting from the long-run average", 0.0))
    else:
        drivers.append((f"Yield curve {ind['curve']['value']:+.2f}pp → {p:.0%} (NY Fed model)", 0.0))

    def bump(cond, x, why):
        nonlocal p
        if cond:
            p += x
            drivers.append((why, x))

    s = ind.get("sahm", {}).get("value")
    bump(s is not None and s >= 0.5, 0.35, "Sahm rule triggered")
    bump(s is not None and 0.3 <= s < 0.5, 0.10, "Sahm rule in the watch zone")
    u = ind.get("unemployment", {}).get("change")
    bump(u is not None and u >= 0.5, 0.10, "Unemployment up 0.5pp or more in a year")
    c = ind.get("claims", {}).get("change")
    bump(c is not None and c > 0.15, 0.05, "Jobless claims rising")
    hy = ind.get("hy_spread", {})
    bump(hy.get("signal", "").startswith("wide"), 0.15, "High-yield spreads wide")
    bump(hy.get("signal") == "widening", 0.05, "High-yield spreads widening")
    bump((ind.get("nfci", {}).get("value") or -1) > 0, 0.05, "Financial conditions tighter than average")
    p = min(0.95, max(0.02, p))

    vix = ind.get("vix", {})
    market_stress = (
        bool(vix and (vix["value"] > 30 or vix.get("term", 0) > 1.0))
        or ind.get("ai_trade", {}).get("signal") == "AI trade unwinding"
    )
    if p >= 0.5:
        label = "Recession risk high"
    elif p >= 0.3:
        label = "Slowdown risk"
    elif market_stress:
        label = "Expansion, with market stress"
    else:
        label = "Expansion"
    return {"recession_prob": p, "label": label, "drivers": drivers, "market_stress": market_stress}


def scenario_weights(p_rec: float) -> dict:
    """Bear/Base/Bull weights for a probability-weighted DCF. At the long-run probability (15%) they are
    25/50/25; each point of recession probability above that moves 0.75 points to Bear and takes 0.5 from Bull."""
    d = p_rec - NEUTRAL_P
    bear = min(0.70, max(0.10, NEUTRAL_WEIGHTS["Bear"] + 0.75 * d))
    bull = min(0.40, max(0.05, NEUTRAL_WEIGHTS["Bull"] - 0.50 * d))
    return {"Bear": bear, "Base": 1 - bear - bull, "Bull": bull}


def vol_scale(ind: dict) -> float:
    """VIX against its five-year median, bounded 0.75–1.75: widens the Monte Carlo when markets are fearful."""
    vix = ind.get("vix")
    if not vix or not vix.get("median5y"):
        return 1.0
    return min(1.75, max(0.75, vix["value"] / vix["median5y"]))


def summary(data: dict) -> dict:
    """Everything the app shows: indicators, regime, scenario weights and the Monte Carlo scale."""
    ind = indicators(data)
    reg = regime(ind)
    return {
        "indicators": ind,
        "regime": reg,
        "weights": scenario_weights(reg["recession_prob"]),
        "vol_scale": vol_scale(ind),
        "errors": data.get("errors") or {},
        "fetched": data.get("fetched"),
        "as_of": max((s[-1][0] for s in list((data.get("yahoo") or {}).values()) if s), default=None),
    }


# ------------------------------------------------------------------- overlays
# Exposure defaults by Yahoo industry: (share of the business tied to AI data-center spending, exposure to Chinese
# competition). Rough starting points for the sliders, not estimates for any one company.
EXPOSURE = {
    "Semiconductors": (0.45, 0.45),
    "Semiconductor Equipment & Materials": (0.5, 0.55),
    "Computer Hardware": (0.4, 0.35),
    "Communication Equipment": (0.3, 0.3),
    "Electronic Components": (0.3, 0.45),
    "Consumer Electronics": (0.15, 0.45),
    "Software - Infrastructure": (0.35, 0.1),
    "Information Technology Services": (0.35, 0.1),
    "Internet Content & Information": (0.35, 0.15),
    "Utilities - Independent Power Producers": (0.3, 0.05),
    "Auto Manufacturers": (0.05, 0.6),
    "Solar": (0.05, 0.8),
}
# full-strength shocks at exposure 1 and severity 1
AI_SHOCK = {"growth": -0.20, "margin_cut": 0.50}  # growth 20pp a year lower; half the long-run margin lost
CHINA_SHOCK = {"growth": -0.05, "margin_cut": 0.40}


def default_exposure(inp: dict) -> dict:
    ind = (inp.get("meta") or {}).get("industry") or ""
    ai, cn = EXPOSURE.get(ind, (0.1, 0.1))
    return {"ai": ai, "china": cn, "industry": ind or "unknown"}


def overlay_overrides(
    inp: dict, scenario: str, ai: float = 0.0, china: float = 0.0, exposure: dict | None = None
) -> dict:
    """Engine overrides (all_growth_shift, margin_shift) for the two shocks at the given severities (0–1).

    The margin shock is a share of the scenario's long-run margin, so a 41% memory margin can lose ten points while an
    8% hardware margin loses one or two.
    """
    ex = exposure or default_exposure(inp)
    sc = (inp.get("scenarios") or {}).get(scenario) or {}
    lr = [v[1] for v in (sc.get("margins") or {}).values() if isinstance(v, list) and len(v) > 1]
    m_lr = max(lr) if lr else 0.0
    g = ai * ex["ai"] * AI_SHOCK["growth"] + china * ex["china"] * CHINA_SHOCK["growth"]
    cut = 1 - (1 - ai * ex["ai"] * AI_SHOCK["margin_cut"]) * (
        1 - china * ex["china"] * CHINA_SHOCK["margin_cut"]
    )
    out = {}
    if g:
        out["all_growth_shift"] = g
    if cut and m_lr > 0:
        out["margin_shift"] = -cut * m_lr
    return out
