"""sentiment.py: what the market and the analysts are saying about a stock, kept apart from what it is worth.

Five components, each scored from -1 (very negative) to +1 (very positive):

  Estimate revisions (30%)  consensus EPS now against 30 and 90 days ago, and analysts revising up against down.
                            Revisions are among the best-documented short-term return predictors.
  Analyst actions (15%)     upgrades against downgrades and price-target raises against cuts in the last 30 days,
                            plus the drift in the buy/hold/sell mix over three months.
  Price trend (30%)         price against its 50- and 200-day averages, 1- and 3-month returns against the S&P 500,
                            and the distance from the 52-week high.
  News tone (15%)           headlines from the last 14 days, scored word by word with a finance lexicon (the
                            Loughran-McDonald dictionary when its CSV is supplied, else a built-in headline list).
  Short interest (10%)      short interest as a share of the float and its change on the month.

The composite runs from -100 to +100. It changes nothing in the valuation: a cheap stock with falling estimates and
a broken trend is a different situation from a cheap stock with rising ones, and the app shows the two side by side.
The trend component also returns the levels a recovery would have to clear (the 20- and 50-day averages and the
10-day high), the numbers a stop order for a bounce is usually set against.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import pathlib
import re

WEIGHTS = {"revisions": 0.30, "analysts": 0.15, "trend": 0.30, "news": 0.15, "short": 0.10}


# Built-in headline lexicon: words that carry tone in financial headlines. Kept short and literal on purpose.
def _words(text: str) -> frozenset:
    return frozenset(text.split())


POSITIVE = _words(
    """
    beat beats beating record records surge surges surged soar soars soared jump jumps jumped rally rallies
    rallied gain gains gained rise rises rising rose climb climbs climbed upgrade upgrades upgraded raise
    raises raised boost boosts boosted strong stronger strongest outperform outperforms outperformed bullish
    buy accelerate accelerates accelerating expand expands expansion growth grows grew profit profitable
    profits win wins won breakthrough optimistic upbeat exceed exceeds exceeded tops topped rebound rebounds
    rebounded recover recovers recovery approval approved partnership contract award awarded momentum
    upside opportunity sold-out
    """
)
NEGATIVE = _words(
    """
    miss misses missed plunge plunges plunged sink sinks sank sinking slump slumps slumped fall falls fell
    falling drop drops dropped tumble tumbles tumbled crash crashes crashed slide slides slid sell-off
    selloff rout downgrade downgrades downgraded cut cuts cutting lower lowers lowered weak weaker weakness
    warn warns warned warning bearish sell underperform underperforms loss losses lose losing decline
    declines declined slowdown slow slows slowing risk risks fear fears worry worries concern concerns
    lawsuit probe investigation fraud default debt dilution layoffs layoff recall delay delays delayed
    shortfall halt halts pause bubble crater craters cratered hurt pressure headwind headwinds glut
    oversupply tariff tariffs ban bans restrictions sanction sanctions volatile
    """
)
WORD = re.compile(r"[a-z][a-z\-']*")


def load_lm(path: str | pathlib.Path) -> tuple[set, set]:
    """Positive and negative words from the Loughran-McDonald master dictionary CSV (a word counts when its flag is
    positive: the flag is the year it was added, and a negative flag marks a word LM removed)."""
    pos, neg = set(), set()
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            w = (row.get("Word") or "").lower()
            try:
                if float(row.get("Positive") or 0) > 0:
                    pos.add(w)
                if float(row.get("Negative") or 0) > 0:
                    neg.add(w)
            except ValueError:
                continue
    return pos, neg


def find_lm(folder: pathlib.Path) -> pathlib.Path | None:
    hits = sorted(pathlib.Path(folder).glob("Loughran-McDonald_MasterDictionary*.csv"))
    return hits[-1] if hits else None


def _raw(o):
    return o.get("raw") if isinstance(o, dict) else o if isinstance(o, (int, float)) else None


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _label(score100: float) -> str:
    return (
        "Very negative" if score100 <= -50 else "Negative" if score100 <= -15 else
        "Neutral" if score100 < 15 else "Positive" if score100 < 50 else "Very positive"
    )  # fmt: skip


# ------------------------------------------------------------------ components
def revisions(summary: dict) -> dict | None:
    trend = (summary.get("earningsTrend") or {}).get("trend") or []
    changes, up, down, facts = [], 0, 0, []
    for t in trend:
        if t.get("period") not in ("0y", "+1y"):
            continue
        et, er = t.get("epsTrend") or {}, t.get("epsRevisions") or {}
        now, d30, d90 = _raw(et.get("current")), _raw(et.get("30daysAgo")), _raw(et.get("90daysAgo"))
        if now is not None and d30:
            c30 = now / d30 - 1 if d30 > 0 else (now - d30) / abs(d30)
            changes.append(c30)
            facts.append(f"{t['period']} EPS {now:.2f}, {c30:+.1%} in 30 days")
        if now is not None and d90:
            changes.append((now / d90 - 1 if d90 > 0 else (now - d90) / abs(d90)) / 2)
        up += _raw(er.get("upLast30days")) or 0
        down += _raw(er.get("downLast30days")) or 0
    if not changes and not (up or down):
        return None
    drift = sum(changes) / len(changes) if changes else 0.0
    net = (up - down) / (up + down) if up + down else 0.0
    score = _clip(0.7 * math.tanh(drift * 20) + 0.3 * net)
    facts.append(f"{up} analysts revised up and {down} down in 30 days")
    return {"score": score, "facts": facts, "drift": drift}


def analysts(summary: dict, today: dt.date, price: float | None) -> dict | None:
    hist = (summary.get("upgradeDowngradeHistory") or {}).get("history") or []
    cutoff = today - dt.timedelta(days=30)
    recent = [
        h for h in hist if h.get("epochGradeDate") and dt.date.fromtimestamp(h["epochGradeDate"]) >= cutoff
    ]
    ups = sum(1 for h in recent if h.get("action") == "up")
    downs = sum(1 for h in recent if h.get("action") == "down")
    raises = sum(1 for h in recent if (h.get("priceTargetAction") or "").lower() == "raises")
    cuts = sum(1 for h in recent if (h.get("priceTargetAction") or "").lower() == "lowers")
    rt = (summary.get("recommendationTrend") or {}).get("trend") or []

    def mean_rating(r):
        n = [r.get(k) or 0 for k in ("strongBuy", "buy", "hold", "sell", "strongSell")]
        return sum((i + 1) * x for i, x in enumerate(n)) / sum(n) if sum(n) else None

    now = mean_rating(rt[0]) if rt else None
    then = mean_rating(rt[-1]) if len(rt) > 1 else None
    if not recent and now is None:
        return None
    parts = []
    if ups + downs:
        parts.append((ups - downs) / (ups + downs))
    if raises + cuts:
        parts.append((raises - cuts) / (raises + cuts))
    if now is not None and then is not None:
        parts.append(_clip((then - now) * 2))  # a lower mean rating is more bullish
    score = _clip(sum(parts) / len(parts)) if parts else 0.0
    facts = [f"{ups} upgrades, {downs} downgrades; {raises} target raises, {cuts} cuts (30 days)"]
    if now is not None:
        facts.append(
            f"mean rating {now:.2f} (1 = strong buy, 5 = strong sell)"
            + (f", {then:.2f} three months ago" if then else "")
        )
    targets = [h.get("currentPriceTarget") for h in recent if h.get("currentPriceTarget")]
    if targets and price:
        facts.append(
            f"recent targets average ${sum(targets) / len(targets):,.0f} against a ${price:,.2f} price"
        )
    return {"score": score, "facts": facts}


def _ma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def trend(stock: list, index: list | None) -> dict | None:
    """Price against its averages and the market; also the levels a recovery has to clear."""
    v = [x[1] for x in stock or [] if x[1] is not None]
    if len(v) < 60:
        return None
    p, ma20, ma50, ma200 = v[-1], _ma(v, 20), _ma(v, 50), _ma(v, 200)
    r1, r3 = p / v[-22] - 1, p / v[-64] - 1 if len(v) >= 64 else None
    hi52 = max(v[-252:])
    rel3 = None
    if index and r3 is not None:
        iv = [x[1] for x in index if x[1] is not None]
        if len(iv) >= 64:
            rel3 = r3 - (iv[-1] / iv[-64] - 1)
    parts = [1.0 if p > ma50 else -1.0]
    if ma200:
        parts.append(1.0 if p > ma200 else -1.0)
    parts.append(_clip(r1 * 5))
    if rel3 is not None:
        parts.append(_clip(rel3 * 3))
    parts.append(_clip((p / hi52 - 1) * 3 + 0.5))  # 0 at 17% below the high, -1 at 50% below
    gains = [max(0.0, v[i] - v[i - 1]) for i in range(len(v) - 14, len(v))]
    losses = [max(0.0, v[i - 1] - v[i]) for i in range(len(v) - 14, len(v))]
    rsi = 100 - 100 / (1 + (sum(gains) / sum(losses))) if sum(losses) else 100.0
    facts = [
        f"price {p:,.2f}; 50-day average {ma50:,.2f}" + (f", 200-day {ma200:,.2f}" if ma200 else ""),
        f"1 month {r1:+.1%}" + (f", 3 months {r3:+.1%}" if r3 is not None else "")
        + (f" ({rel3:+.1%} against the S&P 500)" if rel3 is not None else ""),
        f"{p / hi52 - 1:+.1%} from the 52-week high; 14-day RSI {rsi:.0f}"
        + (" (oversold)" if rsi < 30 else " (overbought)" if rsi > 70 else ""),
    ]  # fmt: skip
    levels = {
        "10-day high": max(v[-10:]),
        "20-day average": ma20,
        "50-day average": ma50,
        **({"200-day average": ma200} if ma200 else {}),
        "10-day low": min(v[-10:]),
    }
    return {"score": _clip(sum(parts) / len(parts)), "facts": facts, "levels": levels, "rsi": rsi}


def news(items: list, lexicon: tuple[set, set] | None = None, today: dt.date | None = None) -> dict | None:
    pos, neg = lexicon or (POSITIVE, NEGATIVE)
    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=14)
    scored = []
    for it in items or []:
        t = it.get("time")
        if t and dt.date.fromtimestamp(t) < cutoff:
            continue
        words = WORD.findall((it.get("title") or "").lower())
        p_ = sum(1 for w in words if w in pos)
        n_ = sum(1 for w in words if w in neg)
        scored.append({**it, "pos": p_, "neg": n_, "tone": (p_ - n_) / (p_ + n_) if p_ + n_ else 0.0})
    if not scored:
        return None
    toned = [s for s in scored if s["pos"] + s["neg"]]
    score = sum(s["tone"] for s in toned) / len(toned) if toned else 0.0
    n_pos = sum(1 for s in scored if s["tone"] > 0)
    n_neg = sum(1 for s in scored if s["tone"] < 0)
    return {
        "score": _clip(score),
        "facts": [
            f"{len(scored)} headlines in 14 days: {n_pos} positive, {n_neg} negative, {len(scored) - n_pos - n_neg} neutral"
        ],
        "headlines": scored,
    }


def short_interest(summary: dict) -> dict | None:
    ks = summary.get("defaultKeyStatistics") or {}
    si = _raw(ks.get("shortPercentOfFloat"))
    if si is None:
        return None
    now, prior = _raw(ks.get("sharesShort")), _raw(ks.get("sharesShortPriorMonth"))
    ch = now / prior - 1 if now and prior else None
    score = _clip(-(si - 0.05) / 0.10 - (0.5 * ch if ch is not None else 0))
    facts = [f"{si:.1%} of the float sold short" + (f", {ch:+.0%} on the month" if ch is not None else "")]
    if si > 0.15:
        facts.append("crowded short: bearish, but a squeeze can force sharp rallies")
    return {"score": score, "facts": facts}


# ------------------------------------------------------------------- composite
def score(
    summary: dict,
    stock: list | None = None,
    index: list | None = None,
    headlines: list | None = None,
    lexicon: tuple[set, set] | None = None,
    today: dt.date | None = None,
) -> dict:
    """All five components and the weighted composite (weights re-normalised over the components with data)."""
    today = today or dt.date.today()
    price = _raw((summary.get("price") or {}).get("regularMarketPrice"))
    comps = {
        "revisions": revisions(summary),
        "analysts": analysts(summary, today, price),
        "trend": trend(stock or [], index),
        "news": news(headlines or [], lexicon, today),
        "short": short_interest(summary),
    }
    have = {k: c for k, c in comps.items() if c is not None}
    if not have:
        return {"score": None, "label": "No data", "components": comps}
    w = sum(WEIGHTS[k] for k in have)
    s = 100 * sum(WEIGHTS[k] * c["score"] for k, c in have.items()) / w
    return {"score": s, "label": _label(s), "components": comps, "coverage": w}


def from_inputs(inp: dict, today: dt.date | None = None) -> dict:
    """A partial reading from an inputs file alone (published examples, uploads): analyst mix and the price history
    the file carries. No revisions, news or short interest."""
    c = inp.get("consensus") or {}
    r = c.get("ratings") or {}
    summary = {
        "price": {"regularMarketPrice": (inp.get("market") or {}).get("price")},
        "recommendationTrend": {
            "trend": [
                {
                    "strongBuy": r.get("strong_buy"), "buy": r.get("buy"), "hold": r.get("hold"),
                    "sell": r.get("sell"), "strongSell": r.get("strong_sell"),
                }
            ]
        },
    }  # fmt: skip
    prices = ((inp.get("history") or {}).get("prices")) or []
    out = score(summary, prices, None, None, None, today)
    out["partial"] = True
    return out


def setup(call: str | None, upside: float | None, sent: dict) -> str:
    """One sentence putting value and sentiment together."""
    s = sent.get("score")
    if s is None or call in (None, "NOT VALUED", "NO CALL"):
        return ""
    rev = (sent["components"].get("revisions") or {}).get("score")
    tr = (sent["components"].get("trend") or {}).get("score")
    if call == "UNDERVALUED":
        if s <= -15 and (rev is not None and rev < 0):
            return "Cheap on the model, but estimates are being cut and the trend is down: the classic value-trap pattern. Waiting for revisions to stop falling costs little."
        if s <= -15:
            return "Cheap on the model while sentiment is poor; estimates are holding, so the selling is about the price, not the numbers."
        if s >= 15:
            return "Cheap on the model with sentiment turning up: value and momentum agree."
        return "Cheap on the model; sentiment is mixed."
    if call == "OVERVALUED":
        if s >= 15:
            return "Expensive on the model and popular: the price needs the good news to keep coming."
        if tr is not None and tr < 0:
            return "Expensive on the model and the trend has turned down."
        return "Expensive on the model; sentiment is mixed."
    return "Near fair value; sentiment " + (
        "is positive." if s >= 15 else "is negative." if s <= -15 else "is neutral."
    )
