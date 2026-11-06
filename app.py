"""app.py: the valuation engine as a web app.

    streamlit run app.py          (or double-click run_app.bat on Windows, run_app.command on a Mac)

Four tabs: a watchlist that values many tickers at once, a portfolio view for your holdings, a page that values
one stock in full and lets you change any assumption, and a short note on how the valuation works.
Everything runs on your own computer; your lists are saved in the my_data folder next to this file.

Valuations are model outputs built on public data. Educational research, not investment advice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import sys

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))
import service as S  # noqa: E402

st.set_page_config(page_title="Stock valuation engine", page_icon="📈", layout="wide")

CALL_COLORS = {
    "UNDERVALUED": "#2f855a", "FAIRLY VALUED": "#718096", "OVERVALUED": "#c0392b", "NO CALL": "#b7791f",
}  # fmt: skip
PRICE_COLOR, FAIR_COLOR = "#c0392b", "#2b6cb0"


# ------------------------------------------------------------------ helpers
def esc(text: str) -> str:
    """Markdown reads a pair of dollar signs as a formula: escape them so prices print as prices."""
    return text.replace("$", "\\$")


def money(x, d: int = 2) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"${x:,.{d}f}"


def pct(x, d: int = 1) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:.{d}%}"


def key_of(inp: dict) -> str:
    return hashlib.sha1(json.dumps(inp, sort_keys=True, default=str).encode()).hexdigest()[:12]


@st.cache_data(show_spinner="Valuing…", max_entries=64)
def run_cached(
    inputs_key: str,
    scenario: str,
    mc: int,
    overrides_json: str,
    macro_key: str,
    _inputs: dict,
    _macro: dict | None,
) -> S.Valuation:
    """The valuation of one inputs file, remembered while the settings stay the same (the inputs are not hashed)."""
    return S.run(_inputs, scenario, mc, json.loads(overrides_json), macro=_macro)


@st.cache_data(ttl=3600, show_spinner="Getting the macro backdrop…")
def macro_cached(day: str, refresh_n: int) -> dict:
    """Today's macro summary (live, else the last saved day, else the bundled example)."""
    try:
        return S.get_macro(refresh=refresh_n > 0)
    except S.MacroError as e:
        return {"error": str(e)}


def ss(name: str, default):
    if name not in st.session_state:
        st.session_state[name] = default
    return st.session_state[name]


# ------------------------------------------------------------------ settings
with st.sidebar:
    st.header("Settings")
    scenario = st.radio(
        "Case",
        S.SCENARIOS,
        index=1,
        horizontal=True,
        help="Which of the model's three cases drives the headline value.",
    )
    mc = st.select_slider(
        "Monte Carlo draws",
        options=[1000, 2000, 5000, 10000],
        value=2000,
        help="More draws, a steadier simulation, a slower run.",
    )
    refresh = st.checkbox(
        "Get fresh data",
        value=False,
        help="Today's data is saved the first time you value a ticker. Tick this to download it again.",
    )
    with st.expander("Where the financial statements come from"):
        src = st.radio(
            "Statements",
            ["Automatic", "Yahoo Finance only", "SEC filings"],
            label_visibility="collapsed",
            help="SEC filings are the companies' own 10-K and 10-Q numbers, free and a quarter fresher than Yahoo "
            "for some companies. Automatic uses them when an SEC contact is set and falls back to Yahoo if the SEC "
            "cannot be reached. US filers only. Prices, peers and news always come from Yahoo.",
        )
        if S.HOSTED:
            st.caption("On the shared copy the SEC contact is set by the host (SEC_USER_AGENT).")
        else:
            contact = st.text_input(
                "Your name and email",
                value=os.environ.get("SEC_USER_AGENT", ""),
                placeholder="Your Name you@example.com",
                help="The SEC asks every program that downloads its data to identify itself. It is used only in "
                "the request header and is not saved.",
            )
            if contact.strip():
                os.environ["SEC_USER_AGENT"] = contact.strip()
        S.FUNDAMENTALS = {"Automatic": "auto", "Yahoo Finance only": "yahoo", "SEC filings": "edgar"}[src]
    use_macro = st.checkbox(
        "Use the macro backdrop",
        value=True,
        help="Recession probability sets the weights of a probability-weighted DCF, and the VIX regime widens or "
        "narrows the Monte Carlo. The fair value itself does not change.",
    )
    macro_now = macro_cached(dt.date.today().isoformat(), ss("macro_refresh", 0)) if use_macro else None
    if macro_now and macro_now.get("error"):
        st.caption(f"Macro data unavailable: {macro_now['error']}")
        macro_now = None
    if macro_now:
        reg = macro_now["regime"]
        st.caption(
            f"Macro: {reg['label'].lower()}, {reg['recession_prob']:.0%} recession probability "
            f"(data {macro_now.get('as_of')}, {macro_now.get('source')})."
        )
    MACRO_KEY = f"{macro_now.get('as_of')}_{macro_now.get('source')}" if macro_now else "none"
    if S.HOSTED:
        st.caption("Shared web copy: your lists are not saved. They last until you close this tab.")
    else:
        st.caption(f"Your lists are saved in `{S.DATA_DIR}` on this computer.")
    st.caption("Educational research, not investment advice.")

st.title("Stock valuation engine")
st.caption(
    "A discounted-cash-flow, Monte Carlo and comparables valuation for any listed company, with every input traced to a source."
)

tab_watch, tab_port, tab_one, tab_macro, tab_how = st.tabs(
    ["Watchlist", "Portfolio", "Value one stock", "Macro and market", "How it works"]
)

vals: dict[str, S.Valuation] = ss("vals", {})


# --------------------------------------------------------------- the charts
def football_chart(val: S.Valuation) -> alt.Chart:
    ff = S.football(val)
    order = list(ff["Method"])
    y = alt.Y("Method:N", sort=order, title=None, axis=alt.Axis(labelLimit=300))
    tip = [
        alt.Tooltip("Method:N"),
        alt.Tooltip("Low:Q", format="$,.2f"),
        alt.Tooltip("Mid:Q", format="$,.2f"),
        alt.Tooltip("High:Q", format="$,.2f"),
    ]
    bars = (
        alt.Chart(ff)
        .mark_bar(height=16, opacity=0.7, color="#718096")
        .encode(
            y=y,
            x=alt.X("Low:Q", title="Value per share ($)", scale=alt.Scale(zero=False)),
            x2="High:Q",
            tooltip=tip,
        )
    )
    mids = (
        alt.Chart(ff).mark_tick(thickness=3, height=22, color="#1a202c").encode(y=y, x="Mid:Q", tooltip=tip)
    )
    marks = pd.DataFrame({"v": [val.price, val.fair_value], "Line": ["Price", "Model fair value"]})
    rules = (
        alt.Chart(marks)
        .mark_rule(strokeDash=[5, 3], size=2)
        .encode(
            x="v:Q",
            color=alt.Color(
                "Line:N",
                scale=alt.Scale(domain=["Price", "Model fair value"], range=[PRICE_COLOR, FAIR_COLOR]),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=[alt.Tooltip("Line:N"), alt.Tooltip("v:Q", title="Value", format="$,.2f")],
        )
    )
    return (bars + mids + rules).properties(height=34 * len(ff) + 60)


def mc_chart(val: S.Valuation) -> alt.Chart:
    counts, edges = np.histogram(np.asarray(val.result["mc"]["values"], dtype=float), bins=40)
    df = pd.DataFrame({"lo": edges[:-1], "hi": edges[1:], "Draws": counts})
    hist = (
        alt.Chart(df)
        .mark_rect(color="#a0aec0")
        .encode(
            x=alt.X("lo:Q", title="Value per share ($)", scale=alt.Scale(zero=False)),
            x2="hi:Q",
            y=alt.Y("Draws:Q"),
            y2=alt.datum(0),
            tooltip=[
                alt.Tooltip("lo:Q", title="From", format="$,.2f"),
                alt.Tooltip("hi:Q", title="To", format="$,.2f"),
                "Draws:Q",
            ],
        )
    )
    marks = pd.DataFrame({"v": [val.price, val.fair_value], "Line": ["Price", "Model fair value"]})
    rules = (
        alt.Chart(marks)
        .mark_rule(strokeDash=[5, 3], size=2)
        .encode(
            x="v:Q",
            color=alt.Color(
                "Line:N",
                scale=alt.Scale(domain=["Price", "Model fair value"], range=[PRICE_COLOR, FAIR_COLOR]),
                legend=alt.Legend(orient="bottom", title=None),
            ),
        )
    )
    return (hist + rules).properties(height=260)


def heatmap(grid: pd.DataFrame, price: float, x_name: str, y_name: str, x_fmt, y_fmt) -> alt.Chart:
    long = grid.reset_index().melt(id_vars=grid.index.name, var_name="y", value_name="value")
    long = long.rename(columns={grid.index.name: "x"})
    long["x_label"] = long["x"].map(x_fmt)
    long["y_label"] = long["y"].map(y_fmt)
    long["vs price"] = long["value"] / price - 1
    xs, ys = [x_fmt(x) for x in grid.index], [y_fmt(y) for y in grid.columns]
    base = alt.Chart(long).encode(
        x=alt.X("x_label:O", sort=xs, title=x_name), y=alt.Y("y_label:O", sort=ys, title=y_name)
    )
    cells = base.mark_rect().encode(
        color=alt.Color(
            "vs price:Q",
            scale=alt.Scale(scheme="redyellowgreen", domainMid=0, domain=[-0.6, 0.6], clamp=True),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("x_label:O", title=x_name),
            alt.Tooltip("y_label:O", title=y_name),
            alt.Tooltip("value:Q", title="Value", format="$,.2f"),
            alt.Tooltip("vs price:Q", title="vs price", format="+.1%"),
        ],
    )
    text = base.mark_text(fontSize=11, color="#1a202c").encode(text=alt.Text("value:Q", format="$,.0f"))
    return (cells + text).properties(height=300, padding={"left": 12, "right": 8, "top": 4, "bottom": 4})


# ------------------------------------------------------- macro and sentiment
COMPONENT_NAMES = {
    "revisions": "Estimate revisions", "analysts": "Analyst actions", "trend": "Price trend",
    "news": "News tone", "short": "Short interest",
}  # fmt: skip


def sentiment_panel(val: S.Valuation, key: str) -> None:
    sent = val.sentiment
    if sent.get("score") is None:
        st.info("No sentiment data for this stock.")
        return
    c1, c2 = st.columns([1, 3])
    c1.metric("Sentiment", f"{sent['score']:+.0f}", help="−100 (very negative) to +100 (very positive)")
    c1.markdown(f"**{sent['label']}**")
    setup = setup_line(val, sent)
    if setup:
        c2.markdown(esc(setup))
    c2.caption(
        "Sentiment is kept apart from value: it does not change the fair value. It says whether estimates, analysts, "
        "the trend and the news are moving with the valuation or against it."
        + (
            " Partial reading: this inputs file carries only the analyst mix and recent prices."
            if sent.get("partial")
            else ""
        )
    )
    rows = []
    for k, c in sent["components"].items():
        if c:
            rows.append(
                {
                    "Component": COMPONENT_NAMES.get(k, k),
                    "Score": c["score"] * 100,
                    "What it shows": "; ".join(c["facts"]),
                }
            )
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={"Score": st.column_config.NumberColumn(format="%+.0f", help="−100 to +100")},
    )  # fmt: skip
    tr = sent["components"].get("trend")
    if tr and tr.get("levels"):
        st.markdown("**Levels a recovery has to clear**")
        lv = pd.DataFrame(
            [{"Level": k, "Price": x, "vs today": x / val.price - 1} for k, x in tr["levels"].items()]
        )
        st.dataframe(
            lv, hide_index=True, width="stretch",
            column_config={"Price": st.column_config.NumberColumn(format="$%.2f"), "vs today": st.column_config.NumberColumn(format="percent")},
        )  # fmt: skip
        st.caption(
            "A buy-stop set just above the 10-day high or the 20-day average waits for the price to turn before it fills; "
            "the 10-day low is where a failed bounce shows. These are reference levels, not recommendations."
        )
    nw = sent["components"].get("news")
    if nw and nw.get("headlines"):
        with st.expander(f"Headlines scored ({sent.get('lexicon', 'built-in headline list')})"):
            for h in sorted(nw["headlines"], key=lambda h: -(h.get("time") or 0))[:15]:
                when = dt.datetime.fromtimestamp(h["time"]).strftime("%b %d") if h.get("time") else ""
                tone = "🟢" if h["tone"] > 0 else "🔴" if h["tone"] < 0 else "⚪"
                st.markdown(f"{tone} {esc(h.get('title') or '')} · {esc(h.get('publisher') or '')} {when}")


def setup_line(val: S.Valuation, sent: dict) -> str:
    import sentiment as SE

    return SE.setup(val.verdict.get("call"), val.upside, sent)


def macro_panel(val: S.Valuation) -> None:
    m = val.macro
    if not m:
        st.info("Macro backdrop is switched off (sidebar).")
    else:
        reg, w = m["regime"], m["weights"]
        mw, base = val.macro_weighted, val.result["scen3"]["Base"]["perShare"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Recession probability (12 months)", f"{reg['recession_prob']:.0%}", help=reg["label"])
        c2.metric(
            "Probability-weighted DCF",
            money(mw),
            delta=f"{mw / base - 1:+.1%} vs Base" if base > 0 else None,
            delta_color="off",
        )
        c3.metric(
            "Monte Carlo width",
            f"{m['vol_scale']:.2f}x",
            help="VIX against its five-year median; 1.00x is normal",
        )
        st.caption(esc(f"{reg['label']}. Bear, Base and Bull weighted {w['Bear']:.0%}, {w['Base']:.0%} and {w['Bull']:.0%} "
                       f"(25/50/25 at a normal 15% recession probability). Data as of {m.get('as_of')}, {m.get('source')}."))  # fmt: skip
    st.markdown("**AI spending and China stress test** (Base case, full-strength shocks)")
    tbl = S.stress_table(val)
    if len(tbl):
        show_cols = ["Scenario", "Value per share", "Change ($)"] + (
            ["Change"] if tbl["Change"].notna().any() else []
        )
        st.dataframe(
            tbl[show_cols], hide_index=True, width="stretch",
            column_config={"Value per share": st.column_config.NumberColumn(format="$%.2f"), "Change ($)": st.column_config.NumberColumn(format="$%+.2f"),
                           "Change": st.column_config.NumberColumn(format="percent")},
        )  # fmt: skip
        ex = S.MACRO.default_exposure(val.base_inputs or val.inputs)
        ov = val.overrides or {}
        st.caption(
            f"Exposures: {ov.get('ai_exposure', ex['ai']):.0%} of the business tied to AI data-center spending, "
            f"{ov.get('china_exposure', ex['china']):.0%} exposure to Chinese competition (defaults for {ex['industry']}; "
            "set your own under Change the assumptions). Full strength: AI growth 20 points a year lower and half the "
            "long-run margin lost on the exposed share; China growth 5 points lower and 40% of the margin lost. "
            "'Accelerates' is half-strength in the other direction."
        )


def show_macro(m: dict | None) -> None:
    if not m:
        st.info("Switch on the macro backdrop in the sidebar.")
        return
    reg, w, ind = m["regime"], m["weights"], m["indicators"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recession probability, 12 months", f"{reg['recession_prob']:.0%}")
    c2.markdown(
        f"**Regime**<br><span style='font-size:1.3rem;font-weight:700'>{reg['label']}</span>",
        unsafe_allow_html=True,
    )
    c3.metric(
        "DCF weights, Bear / Base / Bull",
        f"{w['Bear'] * 100:.0f}/{w['Base'] * 100:.0f}/{w['Bull'] * 100:.0f}",
    )
    c4.metric("Monte Carlo width", f"{m['vol_scale']:.2f}x")
    st.caption(
        esc(
            f"Data as of {m.get('as_of')} ({m.get('source')})."
            + (f" Live fetch failed: {m['fetch_error']}" if m.get("fetch_error") else "")
        )
    )
    b1, _ = st.columns([1, 5])
    if b1.button("Refresh macro data"):
        st.session_state["macro_refresh"] = ss("macro_refresh", 0) + 1
        st.rerun()
    st.markdown("**How the probability is built**")
    for why, x in reg["drivers"]:
        st.markdown(f"- {esc(why)}" + (f" ({x:+.0%})" if x else ""))

    def show(v):
        u, x = v["unit"], v["value"]
        return (f"{x:+.2f} pp" if u == "pp" else f"{x:.2f}%" if u == "pct" else f"{x:.0%}" if u == "prob"
                else f"{x:+.1%}" if u == "ret" else f"{x:,.0f}" if u == "count" else f"{x:+.2f}" if u == "z" else f"{x:,.2f}")  # fmt: skip

    def chg(v):
        c, u = v.get("change"), v["unit"]
        if c is None:
            return ""
        return (
            f"{c:+.2f}" if u in ("pp", "pct", "z") else f"{c:+.1%}" if u in ("ret", "count") else f"{c:+.1f}"
        )

    order = ["recession_curve", "curve", "sahm", "unemployment", "claims", "hy_spread", "nfci", "vix", "move", "tnx",
             "irx", "real_yield", "breakeven", "cpi", "consumer", "spx", "ai_trade", "dollar", "oil"]  # fmt: skip
    rows = [{"Indicator": ind[k]["label"], "Reading": show(ind[k]), "Signal": ind[k]["signal"], "3-month change": chg(ind[k]),
             "Detail": ind[k]["detail"], "Source": ind[k]["source"]} for k in order if k in ind]  # fmt: skip
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    charts = [
        ("curve", "Yield curve, 10y − 3m (pp)"),
        ("hy_spread", "High-yield spread (%)"),
        ("vix", "VIX"),
        ("spx", "S&P 500"),
    ]
    cols = st.columns(2)
    for i, (k, title) in enumerate(charts):
        h = (ind.get(k) or {}).get("history")
        if not h:
            continue
        df = pd.DataFrame(h, columns=["Date", "Value"])
        df["Date"] = pd.to_datetime(df["Date"])
        ch = alt.Chart(df).mark_line(strokeWidth=1.5, color=FAIR_COLOR).encode(
            x=alt.X("Date:T", title=None), y=alt.Y("Value:Q", title=None, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Value:Q", format=",.2f")],
        ).properties(title=title, height=220)  # fmt: skip
        if k == "curve":
            ch = ch + alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
                color=PRICE_COLOR, strokeDash=[4, 3]
            ).encode(y="y:Q")
        cols[i % 2].altair_chart(ch, width="stretch")
    st.caption(
        "What the backdrop changes: the scenario weights of the probability-weighted DCF and the width of the Monte "
        "Carlo. What it does not: the fair value, which stays the blend of DCF, Monte Carlo, comparables and "
        "regression. The 10-year yield enters every valuation directly as the risk-free rate."
    )


# ------------------------------------------------------------ the valuation
def show_valuation(val: S.Valuation, key: str, model_val: S.Valuation | None = None) -> None:
    """Everything about one valuation. model_val is the same stock before the user's assumptions, if they changed any."""
    if val.error:
        st.error(val.error)
        return
    if val.inputs is None:
        return
    meta = val.inputs.get("meta", {})
    st.subheader(f"{val.name} ({val.ticker})")
    st.caption(
        f"{meta.get('exchange', '')} · {meta.get('units', '')} · valued {meta.get('valuation_date', '')} on the {val.scenario} case"
        + (f" · data from {val.source}" if val.source else "")
    )
    if not val.ok:
        st.error("This stock cannot be valued yet: the model will not guess at figures it does not have.")
        for b in val.blocking:
            st.markdown(f"- **{b.get('path', '')}**: {esc(b.get('msg', ''))}")
        st.download_button(
            "Download the inputs file to fill in by hand",
            S.inputs_json(val),
            f"{val.ticker}_inputs.json",
            "application/json",
            key=f"{key}_dl_missing",
        )
        st.caption(
            "Enter the missing figures in the file (from the latest 10-Q or 10-K), then load it on the Value one stock tab."
        )
        return

    vd = val.verdict
    call = vd.get("call", "")
    if not val.valued:
        st.warning(esc(val.note))
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Model fair value",
        money(val.fair_value),
        delta=f"{val.upside:+.1%} vs price" if math.isfinite(val.upside) else None,
        delta_color="off",
    )
    c2.metric("Price", money(val.price))
    c3.metric("Required return", pct(vd.get("requiredReturn")))
    c4.metric(
        "Expected alpha",
        pct(vd.get("alpha")),
        help="Expected return over the required return if the price moves to fair value within the horizon.",
    )
    c5.markdown(
        f"**Call**<br><span style='font-size:1.6rem;font-weight:700;color:{CALL_COLORS.get(call, '#718096')}'>{call}</span>"
        + ("<br><span style='color:#b7791f'>low confidence</span>" if vd.get("confidence") == "low" else ""),
        unsafe_allow_html=True,
    )
    if model_val is not None and model_val.ok and val.overrides:
        st.info(
            esc(
                f"With your assumptions the fair value is {money(val.fair_value)}; the model's own is "
                f"{money(model_val.fair_value)} ({val.fair_value / model_val.fair_value - 1:+.1%})."
            )
        )
    for line in S.reading(val):
        st.markdown(f"- {esc(line)}")

    t_range, t_cash, t_check, t_priced, t_mood, t_notes, t_save = st.tabs(
        [
            "Value ranges",
            "Cash flows and cases",
            "Simulation and comparables",
            "What the price implies",
            "Macro and sentiment",
            "Checks and sources",
            "Download",
        ]
    )
    with t_mood:
        macro_panel(val)
        st.divider()
        sentiment_panel(val, key)

    with t_range:
        st.altair_chart(football_chart(val), width="stretch")
        st.caption(
            "Bars show each method's range, ticks its central value. The red line is today's price, the blue line the model's blended fair value."
        )

    with t_cash:
        st.markdown("**Explicit forecast**")
        st.dataframe(format_forecast(S.forecast_table(val)), width="stretch")
        st.markdown("**The three cases**")
        st.dataframe(scenario_table(val), width="stretch", hide_index=True)

    with t_check:
        a, b = st.columns([3, 2])
        with a:
            st.markdown("**Monte Carlo: value per share across the draws**")
            st.caption(
                "A model-error term (0.5 in log terms, added to the simulated spread) widens the range so that the 10th to 90th "
                "percentile band matches how often prices landed inside it in the backtest. The median and the call do not change."
            )
            st.altair_chart(mc_chart(val), width="stretch")
        with b:
            mcr = val.result["mc"]
            st.dataframe(
                pd.DataFrame(
                    {
                        "": ["5th percentile", "10th", "25th", "Median", "75th", "90th", "95th", "Chance value > price"],
                        "Value": [money(mcr["p5"]), money(mcr["p10"]), money(mcr["p25"]), money(mcr["median"]), money(mcr["p75"]), money(mcr["p90"]), money(mcr["p95"]), pct(mcr["probAbove"], 0)],
                    }
                ),
                hide_index=True, width="stretch",
            )  # fmt: skip
        st.markdown("**Comparables**")
        rel = comps_table(val)
        if rel is not None:
            st.dataframe(rel, hide_index=True, width="stretch")
        reg = val.result["reg"]
        if math.isfinite(S._f(reg.get("r2"))):
            note = "left out of the blend" if reg.get("weak") else "included in the blend"
            implied = money(S._f(reg.get("impliedRaw")))
            st.caption(
                esc(
                    f"Peer regression: R² {reg['r2']:.2f} on {reg.get('n', 0)} peers, implied value {implied}, {note}."
                )
            )
        peers = val.result["rel"].get("peers")
        if peers is not None and len(peers):
            with st.expander("The peers"):
                st.dataframe(peers.round(3), width="stretch")

    with t_priced:
        st.markdown("**The one assumption that would make the model's value equal today's price**")
        st.dataframe(priced_table(val), hide_index=True, width="stretch")
        st.caption(
            "Each row changes only that assumption and solves for the value that matches the price. A blank means no value in a sensible range does."
        )
        g1 = S.sensitivity_grid(val, "wacc_g")
        if not g1.empty:
            st.markdown("**Value per share: discount rate against terminal growth**")
            st.altair_chart(
                heatmap(
                    g1,
                    val.price,
                    "Discount rate (WACC)",
                    "Terminal growth",
                    lambda x: f"{x:.1%}",
                    lambda y: f"{y:.1%}",
                ),
                width="stretch",
            )
        g2 = S.sensitivity_grid(val, "growth_margin")
        if not g2.empty:
            st.markdown("**Value per share: revenue growth against long-run margin**")
            st.altair_chart(
                heatmap(
                    g2,
                    val.price,
                    "Growth shift per year",
                    "Margin shift",
                    lambda x: f"{x * 100:+.1f} pts",
                    lambda y: f"{y * 100:+.1f} pts",
                ),
                width="stretch",
            )
        st.caption(
            "Green cells are worth more than the price, red cells less. The centre cell is the model's own value."
        )

    with t_notes:
        quality = vd.get("quality") or {}
        if quality:
            st.markdown(
                f"**Earnings quality: grade {quality.get('grade')}** (A is cleanest; C lowers confidence; D withholds the call)"
            )
        notes = S.data_notes(val)
        if notes:
            for n in notes:
                icon = {"red": "🔴", "amber": "🟠"}.get(n["level"], "🔵")
                st.markdown(
                    f"{icon} {esc(n['msg'])}  \n<span style='color:#718096;font-size:0.8rem'>{n['where']}</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.success("Nothing to check.")
        prov = val.inputs.get("provenance") or {}
        if prov:
            kinds = pd.Series([p[0] for p in prov.values()]).value_counts()
            st.markdown("**Where each input came from**")
            st.caption(
                " · ".join(f"{n} {k}" for k, n in kinds.items())
                + ". Sourced: read from a feed or filing. Derived: computed from sourced figures. Assumption: a modelling choice you can change."
            )
            with st.expander("Show every input and its source"):
                tbl = pd.DataFrame(
                    [{"Input": path, "Kind": p[0], "Source": p[1]} for path, p in prov.items()]
                )
                kind = st.multiselect(
                    "Kind",
                    sorted(tbl["Kind"].unique()),
                    default=sorted(tbl["Kind"].unique()),
                    key=f"{key}_prov",
                )
                st.dataframe(tbl[tbl["Kind"].isin(kind)], hide_index=True, width="stretch")

    with t_save:
        st.download_button(
            "Inputs file (JSON)",
            S.inputs_json(val),
            f"{val.ticker}_inputs.json",
            "application/json",
            key=f"{key}_dl_json",
        )
        prepared = ss("prepared", {})
        stamp = f"{key}_{val.scenario}_{key_of(val.inputs)}"
        if st.button(
            "Prepare Excel workbook, PDF report and interactive page",
            key=f"{key}_prep",
            help="Builds the workbook with live formulas and the one-file interactive page for this valuation.",
        ):
            with st.spinner("Building the files (the workbook takes about ten seconds)…"):
                prepared[stamp] = (S.workbook_bytes(val), S.page_html(val), _report_pdf(val))
        if stamp in prepared:
            xlsx, page, pdf = prepared[stamp]
            d1, d2, d3 = st.columns(3)
            if pdf:
                d3.download_button(
                    "Client report (PDF)",
                    pdf,
                    f"{val.ticker}_report.pdf",
                    "application/pdf",
                    key=f"{key}_dl_pdf",
                )
            d1.download_button(
                "Excel workbook (live formulas)",
                xlsx,
                f"{val.ticker}_valuation.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{key}_dl_xlsx",
            )
            d2.download_button(
                "Interactive page (HTML)",
                page,
                f"{val.ticker}_valuation.html",
                "text/html",
                key=f"{key}_dl_html",
            )
        st.caption("The workbook and the page include any assumptions you changed.")


def _report_pdf(val):
    """The PDF report, or None when reportlab or matplotlib is not installed (the other files still work)."""
    try:
        return S.pdf_bytes(val)
    except ImportError:
        return None


def format_forecast(t: pd.DataFrame) -> pd.DataFrame:
    if t.empty:
        return t
    out = t.astype(object).copy()
    for row in t.index:
        for col in t.columns:
            x = t.loc[row, col]
            if row in ("Growth", "Operating margin"):
                out.loc[row, col] = pct(x)
            elif row == "EPS":
                out.loc[row, col] = f"{x:,.2f}"
            else:
                out.loc[row, col] = f"{x:,.0f}"
    return out


def scenario_table(val: S.Valuation) -> pd.DataFrame:
    rows = []
    for name, d in val.result["scen3"].items():
        rows.append(
            {
                "Case": name,
                "Value per share": money(S._f(d.get("perShare"))),
                "Exit-multiple check": money(S._f(d.get("perShareExit"))),
                "Enterprise value": f"{S._f(d.get('ev')):,.0f}"
                if math.isfinite(S._f(d.get("ev")))
                else "n/a",
                "Equity value": f"{S._f(d.get('equity')):,.0f}"
                if math.isfinite(S._f(d.get("equity")))
                else "n/a",
                "Terminal value share": pct(S._f(d.get("tvShare")), 0),
            }
        )
    return pd.DataFrame(rows)


def comps_table(val: S.Valuation) -> pd.DataFrame | None:
    t = val.result["rel"].get("table")
    if t is None or not len(t):
        return None
    rows = []
    for k, r in t.iterrows():
        name = S._MULTIPLE_NAMES.get(k, k)
        rows.append(
            {
                "Multiple": name,
                "Peers": int(r["n"]),
                "Peer 25th pct": f"{r['q1']:.2f}",
                "Peer central": f"{r['k']:.2f}",
                "Peer 75th pct": f"{r['q3']:.2f}",
                "Company": f"{S._f(r['own']):.2f}",
                "Implied value": money(S._f(r["implied"])),
                "Implied range": f"{money(S._f(r['impliedQ1']))} to {money(S._f(r['impliedQ3']))}",
                "Weight": pct(S._f(r["weight"]), 0),
            }
        )
    return pd.DataFrame(rows)


def priced_table(val: S.Valuation) -> pd.DataFrame:
    t = S.whats_priced_in(val)
    rows = []
    for _, r in t.iterrows():
        shift = "shift" in r["Assumption"]
        f = (
            (lambda x: f"{x * 100:+.1f} points" if math.isfinite(x) else "")
            if shift
            else (lambda x: pct(x, 1) if math.isfinite(x) else "")
        )
        rows.append(
            {
                "Assumption": r["Assumption"],
                "Model": f(r["Model"]),
                "Needed for today's price": f(r["Needed for today's price"]),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- watchlist
def value_tickers(tickers: list[str]) -> None:
    if not tickers:
        return
    bar = st.progress(0.0, text="Starting…")

    def progress(i: int, n: int, t: str) -> None:
        bar.progress(i / n if n else 1.0, text=f"Valuing {t} ({i + 1} of {n})" if t else "Done")

    out = S.value_many(tickers, scenario, mc, refresh=refresh, progress=progress, macro=macro_now)
    vals.update(out)
    st.session_state["vals_meta"] = {"scenario": scenario, "mc": mc}
    bar.empty()


with tab_watch:
    saved = S.load_watchlist()
    default_text = ", ".join(saved)
    ss("watch_text", default_text)
    text = st.text_area(
        "Tickers", key="watch_text", height=80, placeholder="AAPL, MSFT, NVDA   (commas, spaces or new lines)"
    )
    tickers, rejected = S.parse_tickers(text)
    if rejected:
        st.warning("Not tickers, skipped: " + ", ".join(rejected))
    b1, b2, b3, _ = st.columns([1.2, 1.2, 3, 3])
    if b1.button("Value all", type="primary", disabled=not tickers):
        S.save_watchlist(tickers)
        value_tickers(tickers)
    if b2.button("Save list", disabled=not tickers):
        S.save_watchlist(tickers)
        st.toast(f"Saved {len(tickers)} tickers.")
    if b3.button(
        "Try the examples (no internet needed)", help="Loads the published Dell and Sandisk valuations."
    ):
        for name in ("DELL", "SNDK"):
            vals[name] = S.value_published(name, scenario, mc, macro=macro_now)
        st.session_state["vals_meta"] = {"scenario": scenario, "mc": mc}

    shown = {t: v for t, v in vals.items() if not tickers or t in tickers or v.source == "published"}
    if not shown:
        st.info("Type the tickers you want valued and press **Value all**. The first run downloads each company's data from Yahoo Finance; "
                "later runs the same day use the saved copy. Or press **Try the examples** to see how it looks.")  # fmt: skip
    else:
        meta = st.session_state.get("vals_meta") or {}
        if meta and (meta.get("scenario") != scenario or meta.get("mc") != mc):
            st.caption(
                f"These results use the {meta.get('scenario')} case and {meta.get('mc'):,} draws. Press Value all to apply the current settings."
            )
        df = S.summary_table(shown)
        cfg = {
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "Fair value": st.column_config.NumberColumn(format="$%.2f"),
            "Upside": st.column_config.NumberColumn(format="percent"),
            "Bear": st.column_config.NumberColumn(format="$%.0f"),
            "Base": st.column_config.NumberColumn(format="$%.0f"),
            "Bull": st.column_config.NumberColumn(format="$%.0f"),
            "MC p10": st.column_config.NumberColumn(format="$%.0f"),
            "MC p90": st.column_config.NumberColumn(format="$%.0f"),
            "P(value > price)": st.column_config.NumberColumn(format="percent"),
            "WACC": st.column_config.NumberColumn(format="percent"),
            "Beta": st.column_config.NumberColumn(format="%.2f"),
            "Street target": st.column_config.NumberColumn(format="$%.0f"),
            "Macro-weighted DCF": st.column_config.NumberColumn(format="$%.0f", help="Bear, Base and Bull weighted by today's recession probability"),
            "Sentiment": st.column_config.NumberColumn(format="%+.0f", help="Estimate revisions, analyst actions, trend, news tone and short interest, −100 to +100"),
        }  # fmt: skip
        st.markdown("**Select a row to open the full valuation.**")
        event = st.dataframe(
            df, hide_index=True, width="stretch", column_config=cfg, on_select="rerun", selection_mode="single-row", key="wl_table",
            column_order=["Ticker", "Name", "Price", "Fair value", "Upside", "Call", "Confidence", "Bear", "Base", "Bull",
                          "Macro-weighted DCF", "Sentiment", "Sentiment label", "P(value > price)", "Earnings quality",
                          "Street target", "Warnings", "Status", "Note"],
        )  # fmt: skip
        d1, d2 = st.columns([2, 5])
        d1.download_button(
            "Download table (CSV)", df.to_csv(index=False), "watchlist_valuations.csv", "text/csv"
        )
        ok = df[df["Status"] == "Valued"]
        if len(ok) > 1:
            chart = (
                alt.Chart(ok).mark_bar().encode(
                    y=alt.Y("Ticker:N", sort="-x", title=None),
                    x=alt.X("Upside:Q", title="Model fair value vs price", axis=alt.Axis(format="%")),
                    color=alt.Color("Call:N", scale=alt.Scale(domain=list(CALL_COLORS), range=list(CALL_COLORS.values())), legend=alt.Legend(orient="bottom", title=None)),
                    tooltip=["Ticker", alt.Tooltip("Fair value:Q", format="$,.2f"), alt.Tooltip("Price:Q", format="$,.2f"), alt.Tooltip("Upside:Q", format="+.1%")],
                ).properties(height=36 * len(ok) + 130)
            )  # fmt: skip
            st.altair_chart(chart, width="stretch")
        rows = event.selection.rows if event and event.selection else []
        if rows:
            t = df.iloc[rows[0]]["Ticker"]
            st.divider()
            show_valuation(shown[t], key=f"w_{t}")
            if shown[t].inputs and st.button(
                "Open in “Value one stock” to change its assumptions", key=f"send_{t}"
            ):
                inp = shown[t].base_inputs or shown[t].inputs
                st.session_state["single"] = {
                    "key": key_of(inp),
                    "inputs": inp,
                    "source": shown[t].source or "watchlist",
                }
                st.session_state["reset_n"] = ss("reset_n", 0) + 1
                st.session_state["overrides"] = {}
                st.success("Opened. Go to the Value one stock tab.")


# ---------------------------------------------------------------- portfolio
with tab_port:
    st.markdown("Enter what you own. The model values each stock and totals it for the whole portfolio. "
                + ("This shared web copy does not save holdings; they last until you close the tab."
                   if S.HOSTED else
                   "Your holdings stay on this computer (`my_data/holdings.csv`, excluded from git)."))  # fmt: skip
    held = ss("holdings_df", S.load_holdings())
    edited = st.data_editor(
        held, num_rows="dynamic", hide_index=True, width="stretch", key="holdings_editor",
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", max_chars=10),
            "Shares": st.column_config.NumberColumn("Shares", step=1),
            "Cost per share": st.column_config.NumberColumn("Cost per share", format="$%.2f", help="Optional: gives your gain or loss."),
        },
    )  # fmt: skip
    clean = S.clean_holdings(edited)
    p1, p2, _ = st.columns([1.4, 1.6, 5])
    if p1.button("Save holdings"):
        S.save_holdings(clean)
        st.session_state["holdings_df"] = clean
        st.toast("Holdings saved.")
    if p2.button("Value my holdings", type="primary", disabled=clean.empty):
        S.save_holdings(clean)
        value_tickers(list(clean["Ticker"]))
    if clean.empty:
        st.info("Add a row: ticker, number of shares and, if you like, what you paid per share.")
    else:
        pf = S.portfolio(clean, vals)
        tot = S.portfolio_totals(pf)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Market value", money(tot["market_value"], 0))
        m2.metric(
            "Model fair value of positions",
            money(tot["fair_value"], 0),
            help="Only the positions the model could value.",
        )
        m3.metric("Upside to fair value", pct(tot["upside"]))
        m4.metric("Gain or loss on cost", pct(tot["gain"]), help="Only positions with a cost per share.")
        if tot["valued"] < tot["positions"]:
            st.warning(
                f"{tot['positions'] - tot['valued']} of {tot['positions']} positions are not valued yet. Press Value my holdings."
            )
        st.dataframe(
            pf, hide_index=True, width="stretch",
            column_config={
                "Shares": st.column_config.NumberColumn(format="%.2f"),
                "Price": st.column_config.NumberColumn(format="$%.2f"),
                "Market value": st.column_config.NumberColumn(format="$%.0f"),
                "Weight": st.column_config.NumberColumn(format="percent"),
                "Fair value": st.column_config.NumberColumn(format="$%.2f"),
                "Fair value of position": st.column_config.NumberColumn(format="$%.0f"),
                "Upside": st.column_config.NumberColumn(format="percent"),
                "Cost per share": st.column_config.NumberColumn(format="$%.2f"),
                "Gain or loss": st.column_config.NumberColumn(format="percent"),
            },
        )  # fmt: skip
        failed = [v for t, v in vals.items() if t in set(clean["Ticker"]) and not v.ok]
        for v in failed:
            st.caption(f"{v.ticker}: {v.note}")


# --------------------------------------------------------- value one stock
with tab_one:
    st.markdown("Value one company in full, then change any assumption and see the value move.")
    mode = st.radio(
        "Where does the data come from?",
        ["A ticker (live from Yahoo Finance)", "An example (Dell or Sandisk)", "A file I have"],
        horizontal=True,
    )
    if mode.startswith("A ticker"):
        a, b, c = st.columns([1.2, 3, 1])
        t_in = a.text_input("Ticker", placeholder="AAPL", key="one_ticker")
        peers_in = b.text_input(
            "Peers to compare with (optional)",
            placeholder="MSFT, GOOGL, META  (blank: Yahoo's related companies)",
            key="one_peers",
        )
        c.write("")
        if c.button("Value it", type="primary", disabled=not t_in.strip()):
            peers, _ = S.parse_tickers(peers_in)
            try:
                with st.spinner(f"Getting {t_in.strip().upper()}…"):
                    inp, source = S.build_ticker_inputs(t_in, peers or None, refresh=refresh)
                st.session_state["single"] = {"key": key_of(inp), "inputs": inp, "source": source}
                st.session_state["reset_n"] = ss("reset_n", 0) + 1
                st.session_state["overrides"] = {}
            except S.DataError as e:
                st.error(str(e))
    elif mode.startswith("An example"):
        pick = st.selectbox("Example", ["Dell Technologies", "Sandisk"])
        if st.button("Open example", type="primary"):
            inp = S.load_published("dell" if pick.startswith("Dell") else "sndk")
            st.session_state["single"] = {"key": key_of(inp), "inputs": inp, "source": "published"}
            st.session_state["reset_n"] = ss("reset_n", 0) + 1
            st.session_state["overrides"] = {}
    else:
        up = st.file_uploader(
            "An inputs file (…_inputs.json) or a saved Yahoo snapshot (…_yahoo_….json)", type=["json"]
        )
        if up is not None and st.button("Open file", type="primary"):
            try:
                inp = S.inputs_from_text(up.getvalue())
                st.session_state["single"] = {"key": key_of(inp), "inputs": inp, "source": "upload"}
                st.session_state["reset_n"] = ss("reset_n", 0) + 1
                st.session_state["overrides"] = {}
            except S.DataError as e:
                st.error(str(e))

    single = st.session_state.get("single")
    if single:
        inp, ikey = single["inputs"], single["key"]
        model_val = run_cached(ikey, scenario, mc, "{}", MACRO_KEY, inp, macro_now)
        model_val.source = single.get("source", "")
        reset_n = ss("reset_n", 0)
        if model_val.error is None and model_val.inputs is not None:
            with st.expander("Change the assumptions", expanded=bool(st.session_state.get("overrides"))):
                st.caption("Each slider starts at the model's own number. Move any of them and press the button; the value, the ranges and the files all update. "
                           "Growth and margin shifts apply to all three cases.")  # fmt: skip
                specs = S.default_assumptions(model_val)
                with st.form(f"assume_{reset_n}"):
                    cols = st.columns(3)
                    chosen: dict[str, float] = {}
                    for i, sp in enumerate(specs):
                        col = cols[i % 3]
                        k = f"as_{sp['key']}_{reset_n}"
                        if sp["kind"] == "pct":
                            shift = "shift" in sp["key"] or "shift" in sp["label"].lower()
                            x = (
                                col.slider(
                                    sp["label"] + (" (points)" if shift else ""),
                                    sp["lo"] * 100,
                                    sp["hi"] * 100,
                                    float(sp["value"] * 100),
                                    sp["step"] * 100,
                                    format="%+.2f%%" if shift else "%.2f%%",
                                    key=k,
                                )
                                / 100
                            )
                        elif sp["kind"] == "x":
                            x = col.slider(
                                sp["label"],
                                sp["lo"],
                                sp["hi"],
                                float(sp["value"]),
                                sp["step"],
                                format="%.2fx" if sp["step"] < 0.1 else "%.1fx",
                                key=k,
                            )
                        else:
                            x = col.slider(
                                sp["label"],
                                sp["lo"],
                                sp["hi"],
                                float(min(max(sp["value"], sp["lo"]), sp["hi"])),
                                sp["step"],
                                format="%.2f",
                                key=k,
                            )
                        if abs(x - sp["value"]) > 1e-9:
                            chosen[sp["key"]] = x
                    go_b, reset_b = st.columns([1, 1])
                    applied = go_b.form_submit_button("Re-value with these assumptions", type="primary")
                    cleared = reset_b.form_submit_button("Back to the model's assumptions")
                if cleared:
                    st.session_state["overrides"] = {}
                    st.session_state["reset_n"] = reset_n + 1
                    st.rerun()
                if applied:
                    st.session_state["overrides"] = chosen
                    st.rerun()
        overrides = st.session_state.get("overrides") or {}
        val = (
            run_cached(ikey, scenario, mc, json.dumps(overrides, sort_keys=True), MACRO_KEY, inp, macro_now)
            if overrides
            else model_val
        )
        val.source = single.get("source", "")
        if overrides:
            labels = {s["key"]: s for s in S.default_assumptions(model_val)}

            def show(spec: dict, x: float) -> str:
                if spec["kind"] == "pct":
                    return f"{x * 100:+.1f} points" if "shift" in spec["key"] else f"{x:.2%}"
                return f"{x:.1f}x" if spec["kind"] == "x" else f"{x:.2f}"

            st.caption(
                "Your assumptions: "
                + "; ".join(
                    f"{labels[k]['label'].lower()} {show(labels[k], labels[k]['value'])} → {show(labels[k], x)}"
                    for k, x in overrides.items()
                    if k in labels
                )
            )
        show_valuation(val, key="one", model_val=model_val)
    else:
        st.info("Pick a source above and open a company.")


# ---------------------------------------------------------- macro and market
with tab_macro:
    show_macro(macro_now)


# ------------------------------------------------------------- how it works
with tab_how:
    st.markdown(
        """
**What it does.** For any listed company the engine builds an inputs file from Yahoo Finance (price, filings, analyst
estimates, peers, the Treasury yield and two years of daily prices), tags every input as *sourced*, *derived* or
*assumption*, and values the company six ways, in order:

1. **History and earnings quality.** Five years of statements screened with the Beneish M-score, Sloan accruals and cash conversion. A weak grade lowers confidence or withholds the call.
2. **Future cash flows.** Free cash flow to the firm for operating companies, excess returns for banks and insurers, dividends for REITs, in three cases: Bear, Base and Bull.
3. **Discount rate.** CAPM cost of equity with a beta regressed on daily returns against the S&P 500, blended with the cost of debt into WACC.
4. **Discount.** A ten-year forecast plus a terminal value, cross-checked against an exit multiple.
5. **Price.** Enterprise value bridged to equity value and divided by diluted shares.
6. **Verdict.** The DCF, the Monte Carlo median, comparables and a peer regression are blended into a fair value. The call uses a ±10% band around the price.

**Macro backdrop.** Rates, the yield curve, credit spreads, the VIX, jobs and inflation come live from Yahoo Finance and
FRED. A 12-month recession probability (the New York Fed yield-curve model, adjusted for the Sahm rule, unemployment,
claims, credit spreads and financial conditions) sets the weights of a probability-weighted DCF next to the fair value,
and the VIX regime widens or narrows the Monte Carlo. Two stress overlays, AI data-center spending and Chinese
competition, move growth and margins by the company's exposure.

**Sentiment.** Estimate revisions, analyst actions, the price trend, headline tone and short interest are scored from
−100 to +100. Sentiment never changes the fair value; it shows whether the market is moving with the valuation or
against it, and lists the price levels a recovery would have to clear.

**What it will not do.** It will not guess a missing figure: a stock with missing debt, cash or yield data is listed as
*Needs inputs* with the exact figures to supply. It does not forecast prices, time markets or give investment advice.

**What to check first.** The *Checks and sources* section lists every warning: thin peer sets, a weak regression,
consensus the forecast had to follow, earnings-quality flags. Treat a *low confidence* call as a prompt to look, not a result.

**Your data.** Everything runs on your computer. Yahoo's data for each ticker is saved once a day in `my_data/cache`
so a second run is instant and a valuation can be reproduced. Your watchlist and holdings are in `my_data` and are never uploaded.

**From Python.** `from service import value_ticker, value_many` (in `engine/`) returns the same valuations without the app.
        """
    )
