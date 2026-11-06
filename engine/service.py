"""service.py: the valuation engine as a plain Python API.

Everything the command line and the app need, with no printing and no files unless asked:

    from service import value_ticker, value_many, run, football, whats_priced_in

    val = value_ticker("AAPL")                       # fetch (cached for the day), build inputs, value
    val.ok, val.verdict["call"], val.fair_value
    val = run(val.inputs, "Bull", overrides={"g": 0.025, "beta": 1.1})   # re-value with your own assumptions
    vals = value_many(["AAPL", "MSFT", "NVDA"])      # one failure never stops the rest

A valuation that cannot be run (missing figures, Yahoo unreachable, a ticker with no data) comes back as a
Valuation with `error` or `blocking` filled in, never as an exception, so a watchlist always finishes.

Valuations are illustrations built on public data, not investment advice.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
for _p in (HERE, HERE / "web"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import edgar as EDGAR  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import build_page  # noqa: E402
import excel_model  # noqa: E402
import macro as MACRO  # noqa: E402
import research as RP  # noqa: E402
import run_valuation  # noqa: E402
import sentiment as SE  # noqa: E402
import valuation_v2 as v  # noqa: E402

ROOT = HERE.parent
# A shared web copy (Streamlit Community Cloud runs from /mount/src) must not keep one visitor's lists where the
# next visitor could open them: there, lists live only in the browser session and only Yahoo's public data is cached.
HOSTED = os.environ.get("VALUATION_HOSTED") == "1" or ROOT.as_posix().startswith("/mount/src")
DATA_DIR = pathlib.Path(
    os.environ.get("VALUATION_DATA_DIR")
    or (pathlib.Path(tempfile.gettempdir()) / "valuation_cache" if HOSTED else ROOT / "my_data")
)
SCENARIOS = ("Bear", "Base", "Bull")
EXAMPLE_MACRO = HERE / "data" / "macro_2026-11-05.json.gz"  # offline example: the readings of 5 November 2026
PUBLISHED = {"DELL": "dell", "SNDK": "sndk"}
TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,9}$")  # class shares use a dash (BRK-B)


class DataError(RuntimeError):
    """The data for a ticker could not be fetched or built."""


# ------------------------------------------------------------------ tickers
def clean_ticker(t: str) -> str:
    t = (t or "").strip()
    if not TICKER.match(t):
        raise ValueError(
            f"'{t}' is not a ticker symbol (letters, digits, '.' or '-', at most 10 characters)."
        )
    return t.upper()


def parse_tickers(text: str) -> tuple[list[str], list[str]]:
    """Split a pasted list ("AAPL, msft  NVDA") into (valid tickers without repeats, rejected entries)."""
    good: list[str] = []
    bad: list[str] = []
    for tok in re.split(r"[\s,;]+", text or ""):
        if not tok:
            continue
        try:
            t = clean_ticker(tok)
        except ValueError:
            bad.append(tok)
            continue
        if t not in good:
            good.append(t)
    return good, bad


# --------------------------------------------------------------------- data
def _peers_key(peers: list[str] | None) -> str:
    return "" if not peers else "_" + hashlib.sha1(",".join(sorted(peers)).encode()).hexdigest()[:8]


def snapshot_path(
    cache_dir: pathlib.Path, ticker: str, day: str, peers: list[str] | None = None
) -> pathlib.Path:
    return pathlib.Path(cache_dir) / f"{ticker}_yahoo_{day}{_peers_key(peers)}.json"


# Where the financial statements come from: "auto" uses SEC filings when the SEC contact is set (environment variable
# SEC_USER_AGENT) or a filing snapshot is already saved, and otherwise Yahoo; "edgar" requires the SEC; "yahoo" never
# calls it. Prices, peers, news and consensus always come from Yahoo.
FUNDAMENTALS = os.environ.get("VALUATION_FUNDAMENTALS", "auto")


def sec_path(cache_dir: pathlib.Path, ticker: str, day: str) -> pathlib.Path:
    return pathlib.Path(cache_dir) / f"{ticker}_edgar_{day}.json.gz"


def get_sec(ticker: str, cache_dir: pathlib.Path, day: str, refresh: bool = False, fetcher=None) -> dict:
    """The compact SEC companyfacts for a ticker (saved once a day), or an exception saying why not."""
    path = sec_path(cache_dir, ticker, day)
    if path.exists() and not refresh:
        with contextlib.suppress(OSError, ValueError):
            return EDGAR.load(path)
    facts = (fetcher or EDGAR.fetch)(ticker)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        import gzip

        path.write_bytes(gzip.compress(json.dumps(facts).encode("utf-8"), 6))
    return facts


def with_sec(feeds: dict, ticker: str, cache_dir, day: str, mode, refresh=False, sec_fetcher=None) -> dict:
    """feeds with the SEC statements overlaid, or feeds.fundamentals saying why Yahoo's were kept."""
    mode = mode or FUNDAMENTALS
    if mode == "yahoo":
        return {**feeds, "fundamentals": {"source": "Yahoo Finance", "fields": {}}}
    wanted = mode == "edgar"
    try:
        have = sec_path(cache_dir, ticker, day).exists()
        if not (wanted or have or sec_fetcher or os.environ.get("SEC_USER_AGENT")):
            return {**feeds, "fundamentals": {"source": "Yahoo Finance", "fields": {}}}
        facts = get_sec(ticker, cache_dir, day, refresh, sec_fetcher)
        out = EDGAR.overlay(feeds, EDGAR.parse(facts))
        out["fundamentals"]["gaps"] = [
            {"field": f, "period": p, "date": d, "gap": g}
            for f, p, d, _e, _y, g in EDGAR.compare(EDGAR.parse(facts), feeds)
            if g > 0.05 and f not in ("OperatingIncome", "TotalDebt")
        ]
        return out
    except Exception as e:
        if wanted:
            raise DataError(f"Could not get SEC filings for {ticker}: {type(e).__name__}: {e}") from e
        return {
            **feeds,
            "fundamentals": {"source": "Yahoo Finance", "fields": {}, "note": f"SEC EDGAR not used: {e}"},
        }


def get_feeds(
    ticker: str,
    peers: list[str] | None = None,
    cache_dir: pathlib.Path | None = None,
    refresh: bool = False,
    fetcher=None,
    today: str | None = None,
    fundamentals: str | None = None,
    sec_fetcher=None,
) -> tuple[dict, str]:
    """The Yahoo feeds for a ticker and where they came from ("cache" or "yahoo").

    A snapshot of the raw feeds is saved under cache_dir, one per ticker per day, so the same valuation can be
    rerun without the network and a second click on the same day does not call Yahoo again.
    """
    t = clean_ticker(ticker)
    peers = [clean_ticker(p) for p in peers] if peers else None
    cache_dir = pathlib.Path(cache_dir) if cache_dir else DATA_DIR / "cache"
    day = today or dt.date.today().isoformat()
    path = snapshot_path(cache_dir, t, day, peers)
    if path.exists() and not refresh:
        with contextlib.suppress(OSError, ValueError, KeyError):
            cached = RP.from_fixture(json.loads(path.read_text(encoding="utf-8")))
            return with_sec(cached, t, cache_dir, day, fundamentals, refresh, sec_fetcher), "cache"
    try:
        if fetcher is None:
            import fetch_yahoo

            fetcher = fetch_yahoo.fetch
        feeds = fetcher(t, peers)
    except SystemExit as e:  # fetch_yahoo reports problems with SystemExit
        raise DataError(str(e) or f"Yahoo returned nothing for {t}.") from e
    except Exception as e:
        raise DataError(f"Could not get data for {t} from Yahoo Finance: {type(e).__name__}: {e}") from e
    feeds["ticker"] = t
    feeds.setdefault("today", day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import fetch_yahoo

        fetch_yahoo.save_snapshot(feeds, path)
    except OSError:
        pass  # a read-only folder only costs the cache
    return with_sec(feeds, t, cache_dir, day, fundamentals, refresh, sec_fetcher), "yahoo"


class MacroError(RuntimeError):
    """No macro data: neither Yahoo/FRED nor a cached or example snapshot could be read."""


def _read_macro(path: pathlib.Path) -> dict:
    import gzip

    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def get_macro(
    refresh: bool = False, cache_dir: pathlib.Path | None = None, fetcher=None, today: str | None = None
) -> dict:
    """macro.summary() of today's data, plus "source": "live", "cache", "cache (<date>)" or "example (<date>)".

    Live data is saved once a day. If the network fails, the newest saved day is used, then the bundled example.
    """
    cache_dir = pathlib.Path(cache_dir) if cache_dir else DATA_DIR / "cache"
    day = today or dt.date.today().isoformat()
    path = cache_dir / f"macro_{day}.json"
    if path.exists() and not refresh:
        with contextlib.suppress(OSError, ValueError):
            return {**MACRO.summary(_read_macro(path)), "source": "cache"}
    data, err = None, ""
    try:
        data = (fetcher or MACRO.fetch)()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    if data and (data.get("yahoo") or data.get("fred")):
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        return {**MACRO.summary(data), "source": "live"}
    err = err or next(iter(((data or {}).get("errors") or {}).values()), "")
    older = sorted(cache_dir.glob("macro_*.json")) if cache_dir.exists() else []
    for old in reversed(older):
        with contextlib.suppress(OSError, ValueError):
            return {
                **MACRO.summary(_read_macro(old)),
                "source": f"cache ({old.stem[6:]})",
                "fetch_error": err,
            }
    if EXAMPLE_MACRO.exists():
        return {
            **MACRO.summary(_read_macro(EXAMPLE_MACRO)),
            "source": f"example ({EXAMPLE_MACRO.stem.split('_')[1].split('.')[0]})",
            "fetch_error": err or "no live data",
        }
    raise MacroError(err or "No macro data available.")


def example_macro() -> dict:
    """The bundled readings (no network): for the examples, the tests and a first look."""
    return {**MACRO.summary(_read_macro(EXAMPLE_MACRO)), "source": "example (2026-11-05)"}


def stress_table(val: Valuation) -> pd.DataFrame:
    """Base-case DCF per share under the macro overlays at full strength, with the company's exposures."""
    cols = ["Scenario", "AI spending", "China competition", "Value per share", "Change", "Change ($)"]
    if not val.ok or v.is_fin(val.base_inputs or val.inputs):
        return pd.DataFrame(columns=cols)
    inp = val.base_inputs or val.inputs
    base_ov = {k: x for k, x in (val.overrides or {}).items() if k not in ("ai_shock", "china_shock")}
    rows = []
    for name, ai, cn in [
        ("Base case", 0.0, 0.0),
        ("AI data-center spending slows", 1.0, 0.0),
        ("Chinese competition intensifies", 0.0, 1.0),
        ("Both", 1.0, 1.0),
        ("AI spending accelerates", -0.5, 0.0),
    ]:
        trial = apply_overrides(inp, {**base_ov, "ai_shock": ai, "china_shock": cn})
        try:
            x = v.dcf(trial, trial["scenarios"]["Base" if "Base" in trial["scenarios"] else val.scenario])[
                "perShare"
            ]
        except ValueError:
            x = math.nan
        rows.append([name, ai, cn, x])
    df = pd.DataFrame(rows, columns=cols[:-2])
    base = df["Value per share"].iloc[0]
    # a percentage of a value near zero means nothing: below a tenth of the price, show dollars only
    meaningful = math.isfinite(base) and base > 0.1 * val.price
    df["Change"] = df["Value per share"] / base - 1 if meaningful else math.nan
    df["Change ($)"] = df["Value per share"] - base
    return df


def inputs_from_text(text: str | bytes) -> dict:
    """An inputs file or a saved Yahoo snapshot, as uploaded, to an inputs dict."""
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise DataError(f"That file is not valid JSON: {e}") from e
    if isinstance(obj, dict) and "scenarios" in obj and "meta" in obj:
        return obj
    if isinstance(obj, dict) and "summary" in obj and "annual" in obj:
        try:
            feeds = RP.from_fixture(obj)
            inp = RP.build(feeds)
        except Exception as e:
            raise DataError(f"Could not build inputs from that snapshot: {type(e).__name__}: {e}") from e
        with contextlib.suppress(Exception):
            inp["sentiment"] = sentiment_of(feeds)
        return inp
    raise DataError("That file is neither an inputs file (meta, scenarios, ...) nor a Yahoo snapshot.")


# ---------------------------------------------------------------- the result
@dataclasses.dataclass
class Valuation:
    ticker: str
    scenario: str
    inputs: dict | None = None
    result: dict | None = None
    error: str | None = None
    source: str = ""  # "yahoo", "cache", "published", "upload"
    overrides: dict = dataclasses.field(default_factory=dict)
    base_inputs: dict | None = None  # the inputs before any override
    macro: dict | None = None  # macro.summary() used for this run, if any

    @property
    def blocking(self) -> list[dict]:
        return list(((self.result or {}).get("checks") or {}).get("blocking", []))

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None and self.result["checks"]["ok"]

    @property
    def valued(self) -> bool:
        """Checks passed and at least one method gave a usable value."""
        return self.ok and self.verdict.get("call") != "NOT VALUED"

    @property
    def status(self) -> str:
        if self.error:
            return "Error"
        if not self.ok:
            return "Needs inputs"
        return "Valued" if self.valued else "Not valued"

    @property
    def verdict(self) -> dict:
        return (self.result or {}).get("verdict") or {}

    @property
    def price(self) -> float:
        return float(self.inputs["market"]["price"]) if self.inputs else math.nan

    @property
    def fair_value(self) -> float:
        return _f(self.verdict.get("fair")) if self.ok else math.nan

    @property
    def upside(self) -> float:
        return _f(self.verdict.get("upside")) if self.ok else math.nan

    @property
    def name(self) -> str:
        return ((self.inputs or {}).get("meta") or {}).get("name", self.ticker)

    @property
    def note(self) -> str:
        if self.error:
            return self.error
        if not self.ok:
            b = self.blocking
            return f"{len(b)} missing: " + "; ".join(x["msg"] for x in b[:2]) + ("…" if len(b) > 2 else "")
        if not self.valued:
            why = [
                w["msg"]
                for w in self.verdict.get("warnings", [])
                if w.get("path", "").startswith("verdict.weights")
            ]
            return "No usable value: " + (why[0] if why else "every method failed")
        return ""

    @property
    def sentiment(self) -> dict:
        """The sentiment reading saved with live inputs, else a partial one from the inputs file."""
        inp = self.base_inputs or self.inputs or {}
        return inp.get("sentiment") or SE.from_inputs(inp)

    @property
    def macro_weighted(self) -> float:
        """Bear, Base and Bull DCF values weighted by the macro scenario weights (NaN without macro data)."""
        if not (self.ok and self.macro):
            return math.nan
        s3, w = self.result.get("scen3") or {}, self.macro["weights"]
        vals = {k: _f((s3.get(k) or {}).get("perShare")) for k in w}
        if not all(math.isfinite(x) for x in vals.values()):
            return math.nan
        return sum(w[k] * vals[k] for k in w)


def _f(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


# -------------------------------------------------------------- assumptions
# Scenario-level knobs are applied to Bear, Base and Bull together so the range moves with the case.
_SCENARIO_KNOBS = {"all_growth_shift", "margin_shift", "roe_shift", "payout"}


def default_assumptions(val: Valuation) -> list[dict]:
    """The assumptions the app lets you change, with the model's own value as the default.

    Each item: key, label, value (the model's number), lo, hi, step, kind ("pct" or "x" or "plain").
    """
    inp = val.base_inputs or val.inputs
    if inp is None:
        return []
    cap, term = inp.get("capital", {}), inp.get("terminal", {})
    op = inp.get("operating", {})
    try:
        beta = _f(v.wacc(inp)["beta"]["chosen"])
    except Exception:
        beta = math.nan
    rm, rf = cap.get("rm"), cap.get("rf", 0.04)
    erp = (rm - rf) if rm is not None else cap.get("erp", 0.05)
    out = [
        {
            "key": "beta",
            "label": "Beta",
            "value": beta if math.isfinite(beta) else 1.0,
            "lo": 0.2,
            "hi": 3.5,
            "step": 0.05,
            "kind": "plain",
        },
        {
            "key": "erp",
            "label": "Equity risk premium",
            "value": erp,
            "lo": 0.02,
            "hi": 0.09,
            "step": 0.0025,
            "kind": "pct",
        },
        {
            "key": "rf",
            "label": "Risk-free rate",
            "value": rf,
            "lo": 0.0,
            "hi": 0.09,
            "step": 0.0025,
            "kind": "pct",
        },
        {
            "key": "g",
            "label": "Terminal growth",
            "value": term.get("g", 0.025),
            "lo": -0.02,
            "hi": 0.06,
            "step": 0.0025,
            "kind": "pct",
        },
    ]
    if v.is_fin(inp):
        out += [
            {
                "key": "roe_shift",
                "label": "Return on equity, shift",
                "value": 0.0,
                "lo": -0.1,
                "hi": 0.1,
                "step": 0.005,
                "kind": "pct",
            },
            {
                "key": "payout",
                "label": "Payout ratio",
                "value": _scenario_value(inp, val.scenario, "payout", 0.5),
                "lo": 0.0,
                "hi": 1.0,
                "step": 0.05,
                "kind": "pct",
            },
        ]
        return out
    out += [
        {
            "key": "exit_multiple",
            "label": "Exit EV/EBITDA multiple",
            "value": term.get("exit_multiple", 12.0),
            "lo": 3.0,
            "hi": 40.0,
            "step": 0.5,
            "kind": "x",
        },
        {
            "key": "ronic",
            "label": "Return on new invested capital",
            "value": term.get("ronic", 0.15),
            "lo": 0.02,
            "hi": 0.5,
            "step": 0.01,
            "kind": "pct",
        },
        {
            "key": "all_growth_shift",
            "label": "Revenue growth, shift per year",
            "value": 0.0,
            "lo": -0.15,
            "hi": 0.15,
            "step": 0.005,
            "kind": "pct",
        },
        {
            "key": "margin_shift",
            "label": "Long-run margin, shift",
            "value": 0.0,
            "lo": -0.15,
            "hi": 0.15,
            "step": 0.005,
            "kind": "pct",
        },
    ]
    ex = MACRO.default_exposure(inp)
    out += [
        {
            "key": "ai_shock",
            "label": "AI data-center spending shock (negative: acceleration)",
            "value": 0.0, "lo": -0.5, "hi": 1.0, "step": 0.05, "kind": "pct",
        },
        {
            "key": "china_shock",
            "label": "Chinese competition shock",
            "value": 0.0, "lo": 0.0, "hi": 1.0, "step": 0.05, "kind": "pct",
        },
        {
            "key": "ai_exposure",
            "label": "Share of the business tied to AI spending",
            "value": ex["ai"], "lo": 0.0, "hi": 1.0, "step": 0.05, "kind": "pct",
        },
        {
            "key": "china_exposure",
            "label": "Exposure to Chinese competition",
            "value": ex["china"], "lo": 0.0, "hi": 1.0, "step": 0.05, "kind": "pct",
        },
    ]  # fmt: skip
    if op.get("sales_to_capital") is not None:
        # a build-out: reinvestment follows growth, so the one number that matters is revenue per dollar invested
        s2c = op["sales_to_capital"]
        out.append(
            {
                "key": "sales_to_capital",
                "label": "Revenue added per $1 invested (sales-to-capital)",
                "value": s2c,
                "lo": 0.25,
                "hi": max(3.0, s2c),
                "step": 0.05,
                "kind": "x",
            }
        )
        return out
    capex_key = "capex_lr_pct" if op.get("capex_lr_pct") is not None else "capex_pct"
    capex = op.get(capex_key, 0.03)
    out += [
        {
            "key": capex_key,
            "label": "Long-run capex, % of revenue" if capex_key == "capex_lr_pct" else "Capex, % of revenue",
            "value": capex,
            "lo": 0.0,
            "hi": max(0.3, capex * 1.5),
            "step": 0.0025,
            "kind": "pct",
        },
        {
            "key": "nwc_pct",
            "label": "Net working capital, % of revenue",
            "value": op.get("nwc_pct", 0.05),
            "lo": -0.3,
            "hi": 0.5,
            "step": 0.005,
            "kind": "pct",
        },
    ]
    return out


def _scenario_value(inp: dict, scenario: str, key: str, default: float) -> float:
    try:
        return float(inp["scenarios"][scenario][key])
    except (KeyError, TypeError, ValueError):
        return default


def apply_overrides(inp: dict, overrides: dict | None) -> dict:
    """A copy of the inputs with the given assumptions changed. Keys are those of default_assumptions()."""
    trial = copy.deepcopy(inp)
    ov = dict(overrides or {})
    # the two macro overlays become growth and margin shifts, added to any shift chosen by hand
    ai, cn = ov.pop("ai_shock", None) or 0.0, ov.pop("china_shock", None) or 0.0
    ex = MACRO.default_exposure(inp)
    for k in ("ai", "china"):
        if ov.get(f"{k}_exposure") is not None:
            ex[k] = ov[f"{k}_exposure"]
        ov.pop(f"{k}_exposure", None)
    if (ai or cn) and not v.is_fin(inp):
        sc = "Base" if "Base" in inp.get("scenarios", {}) else next(iter(inp["scenarios"]))
        for k, x in MACRO.overlay_overrides(inp, sc, ai, cn, ex).items():
            ov[k] = (ov.get(k) or 0.0) + x
    for key, x in ov.items():
        if x is None:
            continue
        if key == "rf":
            cap = trial["capital"]
            old_rf = cap.get("rf", 0.04)
            if cap.get("rm") is not None:  # keep the equity risk premium, move the whole market return
                cap["rm"] = cap["rm"] - old_rf + x
            cap["rf"] = x
        elif key in _SCENARIO_KNOBS:
            for name in list(trial["scenarios"]):
                _, S, _ = v.apply_knob(trial, trial["scenarios"][name], key, x)
                trial["scenarios"][name] = S
        else:
            first = next(iter(trial["scenarios"]))
            trial, _, _ = v.apply_knob(trial, trial["scenarios"][first], key, x)
    return trial


# -------------------------------------------------------------------- valuing
def run(
    inp: dict,
    scenario: str = "Base",
    mc: int = 5000,
    overrides: dict | None = None,
    source: str = "",
    macro: dict | None = None,
) -> Valuation:
    """Value an inputs dict. Never raises for bad data: the problem is in Valuation.error or .blocking.

    With macro (macro.summary()), the Monte Carlo is scaled by the VIX regime and the valuation carries the
    macro scenario weights for the probability-weighted DCF.
    """
    meta = (inp or {}).get("meta") or {}
    val = Valuation(
        ticker=str(meta.get("ticker", "?")),
        scenario=scenario,
        inputs=inp,
        source=source,
        overrides=dict(overrides or {}),
        base_inputs=inp,
        macro=macro,
    )
    try:
        if scenario not in inp.get("scenarios", {}):
            raise ValueError(f"These inputs have no '{scenario}' scenario.")
        trial = apply_overrides(inp, overrides)
        if macro and macro.get("vol_scale") and not v.is_fin(trial):
            trial["monte_carlo"] = {**(trial.get("monte_carlo") or {}), "vol_scale": macro["vol_scale"]}
        val.inputs = trial
        val.result = v.run_all(trial, scenario, mc_n=int(mc))
    except (
        Exception
    ) as e:  # the engine raises ValueError for impossible cases, e.g. growth above the discount rate
        val.error = f"{type(e).__name__}: {e}"
    if val.error is not None:
        val.inputs = inp
    return val


def load_published(name: str) -> dict:
    """The inputs file of one of the published valuations (Dell or Sandisk)."""
    return run_valuation.load(PUBLISHED.get(name.upper(), name.lower()))


def value_published(
    name: str,
    scenario: str = "Base",
    mc: int = 5000,
    overrides: dict | None = None,
    macro: dict | None = None,
) -> Valuation:
    return run(load_published(name), scenario, mc, overrides, source="published", macro=macro)


def build_ticker_inputs(
    ticker: str,
    peers: list[str] | None = None,
    refresh: bool = False,
    cache_dir: pathlib.Path | None = None,
    fetcher=None,
    builder=None,
    fundamentals: str | None = None,
    sec_fetcher=None,
) -> tuple[dict, str]:
    """Fetch (cached for the day) and build the inputs for one ticker. Raises DataError with a readable message."""
    try:
        t = clean_ticker(ticker)
    except ValueError as e:
        raise DataError(str(e)) from e
    feeds, source = get_feeds(t, peers, cache_dir, refresh, fetcher, None, fundamentals, sec_fetcher)
    try:
        inp = (builder or RP.build)(feeds)
    except Exception as e:
        raise DataError(f"Could not build inputs for {t}: {type(e).__name__}: {e}") from e
    if isinstance(inp, dict) and feeds.get("fundamentals"):
        inp["fundamentals_source"] = feeds["fundamentals"]
    if isinstance(inp, dict) and isinstance(feeds.get("summary"), dict) and feeds["summary"].get("price"):
        with contextlib.suppress(Exception):  # sentiment is extra; a bad feed never stops the valuation
            inp["sentiment"] = sentiment_of(feeds)
    return inp, source


_LEXICON: dict = {}


def lexicon(data_dir: pathlib.Path | None = None) -> tuple[set, set] | None:
    """The Loughran-McDonald word lists when their CSV is in the data folder, else None (the built-in list)."""
    folder = _data_dir(data_dir)
    if folder not in _LEXICON:
        path = SE.find_lm(folder) if folder.exists() else None
        _LEXICON[folder] = SE.load_lm(path) if path else None
    return _LEXICON[folder]


def sentiment_of(feeds: dict, today: dt.date | None = None) -> dict:
    lx = lexicon()
    out = SE.score(
        feeds["summary"], feeds.get("stock"), feeds.get("index"), feeds.get("news"), lx,
        today or (dt.date.fromisoformat(feeds["today"]) if feeds.get("today") else None),
    )  # fmt: skip
    out["lexicon"] = "Loughran-McDonald" if lx else "built-in headline list"
    return out


def value_ticker(
    ticker: str,
    scenario: str = "Base",
    mc: int = 5000,
    peers: list[str] | None = None,
    refresh: bool = False,
    cache_dir: pathlib.Path | None = None,
    fetcher=None,
    builder=None,
    overrides: dict | None = None,
    macro: dict | None = None,
    fundamentals: str | None = None,
    sec_fetcher=None,
) -> Valuation:
    """Fetch (cached for the day), build the inputs and value one ticker. Problems come back in the result."""
    try:
        inp, source = build_ticker_inputs(
            ticker, peers, refresh, cache_dir, fetcher, builder, fundamentals, sec_fetcher
        )
    except DataError as e:
        return Valuation(ticker=str(ticker).strip().upper(), scenario=scenario, error=str(e), macro=macro)
    return run(inp, scenario, mc, overrides, source=source, macro=macro)


def value_many(
    tickers: list[str],
    scenario: str = "Base",
    mc: int = 2000,
    refresh: bool = False,
    cache_dir: pathlib.Path | None = None,
    fetcher=None,
    builder=None,
    progress=None,
    macro: dict | None = None,
    fundamentals: str | None = None,
    sec_fetcher=None,
) -> dict[str, Valuation]:
    """Value a list of tickers one after another. One failing ticker never stops the rest.

    progress(i, n, ticker) is called before each ticker, for a progress bar.
    """
    out: dict[str, Valuation] = {}
    n = len(tickers)
    for i, t in enumerate(tickers):
        if progress:
            progress(i, n, t)
        out[t] = value_ticker(
            t,
            scenario,
            mc,
            refresh=refresh,
            cache_dir=cache_dir,
            fetcher=fetcher,
            builder=builder,
            macro=macro,
            fundamentals=fundamentals,
            sec_fetcher=sec_fetcher,
        )
    if progress:
        progress(n, n, "")
    return out


# ----------------------------------------------------------------- summaries
SUMMARY_COLUMNS = [
    "Ticker", "Name", "Status", "Price", "Fair value", "Upside", "Call", "Confidence", "Bear", "Base", "Bull",
    "Macro-weighted DCF", "Sentiment", "Sentiment label", "MC p10", "MC p90", "P(value > price)", "WACC", "Beta",
    "Earnings quality", "Street target", "Warnings", "Valued", "Note",
]  # fmt: skip


def summary(val: Valuation) -> dict:
    """One row of the watchlist table."""
    row = dict.fromkeys(SUMMARY_COLUMNS)
    row.update({"Ticker": val.ticker, "Name": val.name, "Status": val.status, "Note": val.note})
    if val.inputs is None:
        return row
    row["Price"] = val.price
    row["Valued"] = (val.inputs.get("meta") or {}).get("valuation_date")
    sent = val.sentiment
    if sent.get("score") is not None and not sent.get("partial"):
        row["Sentiment"], row["Sentiment label"] = _f(sent["score"]), sent["label"]
    if not val.ok:
        return row
    R, vd = val.result, val.verdict
    s3, mc, W = R["scen3"], R["mc"], R["W"]
    targets = (val.inputs.get("consensus") or {}).get("targets") or {}
    row.update(
        {
            "Fair value": val.fair_value,
            "Upside": val.upside,
            "Call": vd.get("call"),
            "Confidence": vd.get("confidence"),
            "Bear": _f(s3.get("Bear", {}).get("perShare")),
            "Base": _f(s3.get("Base", {}).get("perShare")),
            "Bull": _f(s3.get("Bull", {}).get("perShare")),
            "Macro-weighted DCF": val.macro_weighted,
            "MC p10": _f(mc.get("p10")),
            "MC p90": _f(mc.get("p90")),
            "P(value > price)": _f(mc.get("probAbove")),
            "WACC": _f(W.get("wacc")),
            "Beta": _f((W.get("beta") or {}).get("chosen")),
            "Earnings quality": (vd.get("quality") or {}).get("grade"),
            "Street target": _f(targets.get("mean")),
            "Warnings": len(vd.get("warnings", []))
            + len((val.inputs.get("data_quality") or {}).get("warnings", [])),
        }
    )
    return row


def summary_table(vals: dict[str, Valuation] | list[Valuation]) -> pd.DataFrame:
    items = list(vals.values()) if isinstance(vals, dict) else list(vals)
    df = pd.DataFrame([summary(x) for x in items], columns=SUMMARY_COLUMNS)
    return df.sort_values("Upside", ascending=False, na_position="last", kind="stable").reset_index(drop=True)


_MULTIPLE_NAMES = {
    "pe_fwd": "Forward P/E", "ev_ebitda": "EV/EBITDA", "ev_sales": "EV/Sales", "pb": "Price/book",
    "div_yield": "Dividend yield", "ffo": "Price/FFO",
}  # fmt: skip


def football(val: Valuation) -> pd.DataFrame:
    """Valuation ranges per method, in dollars per share: Method, Low, Mid, High."""
    if not val.ok:
        return pd.DataFrame(columns=["Method", "Low", "Mid", "High"])
    R, inp = val.result, val.inputs
    rows: list[tuple[str, float, float, float]] = []
    s3 = R["scen3"]
    if all(k in s3 for k in ("Bear", "Base", "Bull")):
        rows.append(
            (
                "DCF, Bear to Bull",
                _f(s3["Bear"]["perShare"]),
                _f(s3["Base"]["perShare"]),
                _f(s3["Bull"]["perShare"]),
            )
        )
    mc = R["mc"]
    rows.append(("Monte Carlo, 10th to 90th percentile", _f(mc["p10"]), _f(mc["median"]), _f(mc["p90"])))
    table = R["rel"].get("table")
    if table is not None and len(table):
        for k, r in table.iterrows():
            if _f(r.get("weight")) > 0:
                rows.append(
                    (
                        f"Comparables, {_MULTIPLE_NAMES.get(k, k)}",
                        _f(r["impliedQ1"]),
                        _f(r["implied"]),
                        _f(r["impliedQ3"]),
                    )
                )
    reg = _f(R["reg"].get("implied"))
    if math.isfinite(reg):
        rows.append(("Peer regression", reg, reg, reg))
    m = inp["market"]
    if _f(m.get("low_52w")) > 0 and _f(m.get("high_52w")) > 0:
        rows.append(("52-week trading range", _f(m["low_52w"]), _f(m["price"]), _f(m["high_52w"])))
    t = (inp.get("consensus") or {}).get("targets") or {}
    if _f(t.get("low")) > 0 and _f(t.get("high")) > 0:
        rows.append(
            ("Analyst price targets", _f(t["low"]), _f(t.get("mean", t.get("median"))), _f(t["high"]))
        )
    df = pd.DataFrame(rows, columns=["Method", "Low", "Mid", "High"])
    return df[np.isfinite(df["Low"]) & np.isfinite(df["High"])].reset_index(drop=True)


_FORECAST_COLUMNS = {
    "rev": "Revenue", "growth": "Growth", "margin": "Operating margin", "ebit": "EBIT", "nopat": "NOPAT",
    "da": "D&A", "capex": "Capex", "dnwc": "Change in NWC", "fcff": "Free cash flow", "eps": "EPS", "pv": "Present value",
}  # fmt: skip


def forecast_table(val: Valuation) -> pd.DataFrame:
    """The explicit forecast for the chosen scenario, one column per year."""
    if not val.ok:
        return pd.DataFrame()
    rows = val.result["base"]["rows"]
    df = pd.DataFrame(rows)
    cols = [c for c in _FORECAST_COLUMNS if c in df.columns]
    out = df[cols].rename(columns=_FORECAST_COLUMNS)
    out.index = df["label"] if "label" in df.columns else range(1, len(df) + 1)
    return out.T


def whats_priced_in(val: Valuation) -> pd.DataFrame:
    """Reverse DCF: the single assumption that would make the model's value equal today's price."""
    if not val.ok:
        return pd.DataFrame(columns=["Assumption", "Model", "Needed for today's price"])
    inp, scen = val.inputs, val.inputs["scenarios"][val.scenario]
    W = val.result["W"]
    wacc, g = W["wacc"], inp["terminal"].get("g", 0.025)
    specs = [("wacc", "Discount rate (WACC)", wacc, max(g + 0.005, 0.01), 0.40)]
    if v.is_fin(inp):
        specs.append(("g", "Terminal growth", g, -0.03, min(wacc - 0.005, 0.08)))
    else:
        specs += [
            ("g", "Terminal growth", g, -0.03, min(wacc - 0.005, 0.08)),
            ("all_growth_shift", "Revenue growth, shift per year", 0.0, -0.30, 0.30),
            ("margin_shift", "Long-run margin, shift", 0.0, -0.30, 0.30),
        ]
    rows = []
    for knob, label, model_value, lo, hi in specs:
        try:
            needed = v.reverse(inp, scen, knob, lo, hi)
        except Exception:
            needed = math.nan
        rows.append({"Assumption": label, "Model": model_value, "Needed for today's price": _f(needed)})
    return pd.DataFrame(rows)


def sensitivity_grid(val: Valuation, kind: str = "wacc_g", steps: int = 3) -> pd.DataFrame:
    """Value per share on a grid of two assumptions, centred on the model's own. kind: "wacc_g" or "growth_margin"."""
    if not val.ok:
        return pd.DataFrame()
    inp, scen = val.inputs, val.inputs["scenarios"][val.scenario]
    idx = range(-steps, steps + 1)
    if kind == "wacc_g":
        wacc, g = val.result["W"]["wacc"], inp["terminal"].get("g", 0.025)
        xs = [wacc + 0.01 * i for i in idx]
        ys = [round(g + 0.005 * i, 6) for i in idx]
        return v.sensitivity(inp, scen, "wacc", xs, "g", ys)
    if v.is_fin(inp):
        return pd.DataFrame()
    xs = [round(0.025 * i, 6) for i in idx]
    ys = [round(0.025 * i, 6) for i in idx]
    return v.sensitivity(inp, scen, "all_growth_shift", xs, "margin_shift", ys)


def reading(val: Valuation) -> list[str]:
    """The result in plain sentences, every figure taken from the valuation (no opinion added)."""
    if not val.ok:
        return []
    if not val.valued:
        out = [val.note + "."]
        s3 = val.result.get("scen3") or {}
        if all(k in s3 for k in ("Bear", "Base", "Bull")):
            out.append(
                f"The DCF gives ${_f(s3['Bear']['perShare']):,.2f} (Bear), ${_f(s3['Base']['perShare']):,.2f} (Base) and "
                f"${_f(s3['Bull']['perShare']):,.2f} (Bull) against a ${val.price:,.2f} price: on these assumptions the "
                "cash flows do not support the price. The long-run margin and the sales-to-capital ratio decide it; "
                "change them under Change the assumptions."
            )
        return out
    price, fair, up = val.price, val.fair_value, val.upside
    s3, mc = val.result["scen3"], val.result["mc"]
    out = [
        f"The model's fair value is ${fair:,.2f}, {abs(up):.1%} {'above' if up >= 0 else 'below'} the ${price:,.2f} price."
    ]
    if all(k in s3 for k in ("Bear", "Base", "Bull")):
        bear, bull = _f(s3["Bear"]["perShare"]), _f(s3["Bull"]["perShare"])
        if price > bull:
            out.append(f"The price is above even the Bull case (${bull:,.2f}).")
        elif price < bear:
            out.append(f"The price is below even the Bear case (${bear:,.2f}).")
        else:
            out.append(
                f"The price sits inside the DCF range, ${bear:,.2f} in the Bear case to ${bull:,.2f} in the Bull case."
            )
    out.append(
        f"In the Monte Carlo, value exceeds the price in {_f(mc['probAbove']):.0%} of draws "
        f"(10th to 90th percentile: ${_f(mc['p10']):,.2f} to ${_f(mc['p90']):,.2f})."
    )
    mean_target = _f((((val.inputs.get("consensus") or {}).get("targets")) or {}).get("mean"))
    if math.isfinite(mean_target) and mean_target > 0:
        out.append(
            f"The mean analyst target is ${mean_target:,.2f}, {abs(mean_target / price - 1):.0%} "
            f"{'above' if mean_target >= price else 'below'} the price; the model's fair value is "
            f"{abs(fair / mean_target - 1):.0%} {'above' if fair >= mean_target else 'below'} it."
        )
    priced = whats_priced_in(val).set_index("Assumption")["Needed for today's price"]
    bits = []
    shift = _f(priced.get("Revenue growth, shift per year"))
    if math.isfinite(shift):
        bits.append(
            f"revenue growth {abs(shift) * 100:.1f} points a year {'above' if shift >= 0 else 'below'} the forecast"
        )
    wacc_needed = _f(priced.get("Discount rate (WACC)"))
    if math.isfinite(wacc_needed):
        bits.append(f"a discount rate of {wacc_needed:.1%} (the model uses {val.result['W']['wacc']:.1%})")
    if bits and abs(up) > 0.001:
        either = "either " if len(bits) > 1 else ""
        out.append(f"On its own, {either}{' or '.join(bits)} would bring the model's value to today's price.")
    q = (val.verdict.get("quality") or {}).get("grade")
    n = len([x for x in data_notes(val) if x["level"] in ("red", "amber")])
    if q:
        out.append(
            f"Earnings quality grade {q}; {n} item{'s' if n != 1 else ''} to check before relying on it."
        )
    if (val.inputs.get("operating") or {}).get("sales_to_capital") is not None:
        op = val.inputs["operating"]
        lr = (val.inputs["scenarios"][val.scenario]["margins"].get("total") or [None, None])[1]
        out.append(
            f"Build-out company: the value rests on two assumptions, a {lr:.0%} long-run margin and "
            f"${op['sales_to_capital']:.2f} of revenue per $1 invested. Treat the figure as a scenario, not a price."
            if lr is not None
            else "Build-out company: the value rests on the long-run margin and the sales-to-capital ratio."
        )
    fs = val.inputs.get("fundamentals_source") or {}
    if fs.get("filed"):
        out.append(
            f"Financial statements come from SEC filings (latest filed {fs['filed']}); operating income adds back "
            "impairment charges, as Yahoo does."
            + (
                f" The SEC and Yahoo differ by more than 5% on {len(fs['gaps'])} line(s)."
                if fs.get("gaps")
                else ""
            )
        )
    elif fs.get("note"):
        out.append(fs["note"] + "; Yahoo's statements were used.")
    mw = val.macro_weighted
    if math.isfinite(mw):
        reg, w = val.macro["regime"], val.macro["weights"]
        base = _f(s3["Base"]["perShare"])
        out.append(
            f"Macro: {reg['label'].lower()}, {reg['recession_prob']:.0%} recession probability; weighting Bear, Base and "
            f"Bull {w['Bear']:.0%}/{w['Base']:.0%}/{w['Bull']:.0%} gives ${mw:,.2f} ({mw / base - 1:+.1%} against the Base DCF)."
        )
    sent = val.sentiment
    if sent.get("score") is not None:
        line = f"Sentiment: {sent['label'].lower()} ({sent['score']:+.0f} on a −100 to +100 scale"
        line += ", partial: analyst mix and price only)." if sent.get("partial") else ")."
        setup = SE.setup(val.verdict.get("call"), up, sent)
        out.append(line + (" " + setup if setup else ""))
    return out


def data_notes(val: Valuation) -> list[dict]:
    """Everything the model wants you to check, most important first: level (red/amber/info), source, message."""
    notes: list[dict] = []
    if val.error:
        return [{"level": "red", "where": "run", "msg": val.error}]
    for b in val.blocking:
        notes.append({"level": "red", "where": b.get("path", ""), "msg": b.get("msg", "")})
    if val.result and val.ok:
        for w in val.verdict.get("warnings", []):
            notes.append({"level": "amber", "where": w.get("path", ""), "msg": w.get("msg", "")})
        for fl in (val.verdict.get("quality") or {}).get("flags", []):
            notes.append(
                {"level": fl.get("level", "amber"), "where": "earnings quality", "msg": fl.get("msg", "")}
            )
    dq = (val.inputs or {}).get("data_quality") or {}
    for w in dq.get("warnings", []):
        notes.append(
            {
                "level": "amber",
                "where": w.get("path", "data") if isinstance(w, dict) else "data",
                "msg": w.get("msg", str(w)) if isinstance(w, dict) else str(w),
            }
        )
    order = {"red": 0, "amber": 1}
    return sorted(notes, key=lambda n: order.get(n["level"], 2))


# ------------------------------------------------------------------- exports
def workbook_bytes(val: Valuation) -> bytes:
    """The Excel workbook with live formulas for this valuation's inputs (your assumption changes included)."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "valuation.xlsx"
        excel_model.build_workbook(val.inputs, str(path), val.scenario)
        return path.read_bytes()


def page_html(val: Valuation) -> str:
    """The interactive terminal as one self-contained HTML file."""
    with tempfile.TemporaryDirectory() as d:
        return build_page.build(val.inputs, pathlib.Path(d) / "index.html", inline=True)


def inputs_json(val: Valuation) -> str:
    return json.dumps(val.inputs, indent=1)


# --------------------------------------------------------------- local files
def _data_dir(data_dir: pathlib.Path | None) -> pathlib.Path:
    return pathlib.Path(data_dir) if data_dir else DATA_DIR


def load_watchlist(data_dir: pathlib.Path | None = None) -> list[str]:
    if HOSTED and data_dir is None:
        return []
    p = _data_dir(data_dir) / "watchlist.json"
    try:
        return parse_tickers(" ".join(json.loads(p.read_text(encoding="utf-8"))))[0]
    except (OSError, ValueError, TypeError):
        return []


def save_watchlist(tickers: list[str], data_dir: pathlib.Path | None = None) -> None:
    if HOSTED and data_dir is None:
        return
    p = _data_dir(data_dir) / "watchlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(parse_tickers(" ".join(tickers))[0]), encoding="utf-8")


HOLDING_COLUMNS = ["Ticker", "Shares", "Cost per share"]


def load_holdings(data_dir: pathlib.Path | None = None) -> pd.DataFrame:
    if HOSTED and data_dir is None:
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    p = _data_dir(data_dir) / "holdings.csv"
    try:
        df = pd.read_csv(p)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    return clean_holdings(df)


def save_holdings(df: pd.DataFrame, data_dir: pathlib.Path | None = None) -> None:
    if HOSTED and data_dir is None:
        return
    p = _data_dir(data_dir) / "holdings.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    clean_holdings(df).to_csv(p, index=False)


def clean_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """Valid tickers only, shares and cost as numbers, one row per ticker (repeats are added together)."""
    rows: dict[str, list[float]] = {}
    for _, r in df.iterrows():
        try:
            t = clean_ticker(str(r.get("Ticker", "")))
        except ValueError:
            continue
        sh = _f(r.get("Shares"))
        if not math.isfinite(sh) or sh == 0:
            continue
        cost = _f(r.get("Cost per share"))
        old = rows.get(t)
        if old is None:
            rows[t] = [sh, cost * sh if math.isfinite(cost) else math.nan]
        else:
            old[0] += sh
            if math.isfinite(old[1]) and math.isfinite(cost):
                old[1] += cost * sh
            else:
                old[1] = math.nan
    out = [(t, sh, (c / sh if math.isfinite(c) else math.nan)) for t, (sh, c) in rows.items()]
    return pd.DataFrame(out, columns=HOLDING_COLUMNS)


PORTFOLIO_COLUMNS = [
    "Ticker", "Shares", "Price", "Market value", "Weight", "Fair value", "Fair value of position", "Upside",
    "Cost per share", "Gain or loss", "Call",
]  # fmt: skip


def portfolio(holdings: pd.DataFrame, vals: dict[str, Valuation]) -> pd.DataFrame:
    """Positions with market value, the model's fair value of the position and the gain or loss on cost."""
    rows = []
    h = clean_holdings(holdings)
    for _, r in h.iterrows():
        t, sh, cost = r["Ticker"], float(r["Shares"]), _f(r["Cost per share"])
        val = vals.get(t)
        price = val.price if val is not None and val.inputs else math.nan
        fair = val.fair_value if val is not None else math.nan
        rows.append(
            {
                "Ticker": t,
                "Shares": sh,
                "Price": price,
                "Market value": sh * price,
                "Fair value": fair,
                "Fair value of position": sh * fair,
                "Upside": val.upside if val is not None else math.nan,
                "Cost per share": cost,
                "Gain or loss": (price / cost - 1)
                if math.isfinite(cost) and cost > 0 and math.isfinite(price)
                else math.nan,
                "Call": val.verdict.get("call") if val is not None and val.ok else None,
            }
        )
    df = pd.DataFrame(rows, columns=PORTFOLIO_COLUMNS)
    total = df["Market value"].sum(skipna=True)
    df["Weight"] = df["Market value"] / total if total else math.nan
    return df.sort_values("Market value", ascending=False, na_position="last", kind="stable").reset_index(
        drop=True
    )


def portfolio_totals(df: pd.DataFrame) -> dict:
    """Totals over the positions that could be valued: market value, fair value, weighted upside, gain on cost."""
    if df.empty:
        return {
            "market_value": 0.0,
            "fair_value": 0.0,
            "upside": math.nan,
            "valued": 0,
            "positions": 0,
            "cost": math.nan,
            "gain": math.nan,
        }
    ok = df[np.isfinite(df["Fair value of position"]) & np.isfinite(df["Market value"])]
    mv, fv = float(ok["Market value"].sum()), float(ok["Fair value of position"].sum())
    costed = df[np.isfinite(df["Cost per share"]) & np.isfinite(df["Market value"])]
    cost = float((costed["Shares"] * costed["Cost per share"]).sum()) if len(costed) else math.nan
    mv_c = float(costed["Market value"].sum()) if len(costed) else math.nan
    return {
        "market_value": float(df["Market value"].sum(skipna=True)),
        "fair_value": fv,
        "upside": (fv / mv - 1) if mv else math.nan,
        "valued": len(ok),
        "positions": len(df),
        "cost": cost,
        "gain": (mv_c / cost - 1) if cost and math.isfinite(cost) else math.nan,
    }
