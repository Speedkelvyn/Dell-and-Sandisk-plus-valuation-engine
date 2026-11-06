"""report_pdf.py: a client-ready PDF of one valuation.

    from report_pdf import build_pdf
    pdf_bytes = build_pdf(valuation)        # a service.Valuation (what the app and value_ticker produce)

Pages: the verdict and the range of values by method; the forecast and the scenarios; comparables; the checks,
the sources and the method. Every figure comes from the valuation; nothing is added by hand. Needs reportlab and
matplotlib (both optional for the rest of the engine).
"""

from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import service as S

INK, MUTED, RULE = colors.HexColor("#1a202c"), colors.HexColor("#718096"), colors.HexColor("#cbd5e0")
GOOD, BAD, FLAT = colors.HexColor("#2f855a"), colors.HexColor("#c53030"), colors.HexColor("#4a5568")
DISCLAIMER = (
    "This report is an illustration built on public data. It is educational research, not investment advice, and not "
    "an offer to buy or sell any security. Figures come from the feeds and filings named on the last page and from modelling "
    "assumptions that can be wrong; check them before relying on any of it."
)


def _f(x):
    return S._f(x)


def money(x, d=2):
    x = _f(x)
    return "n/a" if math.isnan(x) else f"${x:,.{d}f}"


def pct(x, d=1, sign=False):
    x = _f(x)
    return "n/a" if math.isnan(x) else f"{x:+.{d}%}" if sign else f"{x:.{d}%}"


def big(x, units):
    """A statement figure (the inputs are in `units`, usually USD millions)."""
    x = _f(x)
    if math.isnan(x):
        return "n/a"
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.2f}"


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle(
        "b", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK
    )
    return {
        "body": base,
        "small": ParagraphStyle("s", parent=base, fontSize=8, leading=10.5, textColor=MUTED),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=2
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=16,
            spaceBefore=12,
            spaceAfter=5,
        ),
        "sub": ParagraphStyle("sub", parent=base, fontSize=10, textColor=MUTED, spaceAfter=8),
        "cell": ParagraphStyle("c", parent=base, fontSize=8.5, leading=10.5),
        "bullet": ParagraphStyle("bl", parent=base, leftIndent=12, bulletIndent=2, spaceAfter=2),
    }


def _table(rows, widths, header=True, align_right_from=1, bold_last=False):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)
        if header
        else ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        st.append(("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5))
    if bold_last:
        st.append(("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.5))
    t.setStyle(TableStyle(st))
    return t


# ------------------------------------------------------------------------------------------------ charts
def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def football(val):
    """Range of value per share by method, with today's price."""
    R, price = val.result, val.price
    bars = []  # (label, low, mid, high)
    s3 = R.get("scen3") or {}
    if all(k in s3 for k in ("Bear", "Base", "Bull")):
        bars.append(
            ("DCF, Bear to Bull", s3["Bear"]["perShare"], s3["Base"]["perShare"], s3["Bull"]["perShare"])
        )
    mc = R.get("mc") or {}
    if mc.get("p10") is not None:
        bars.append(("Monte Carlo, 10th to 90th pct", mc["p10"], mc["median"], mc["p90"]))
    tbl = (R.get("rel") or {}).get("table")
    if tbl is not None and len(tbl):
        usable = tbl[tbl["usable"].astype(bool)] if "usable" in tbl else tbl
        for name, row in usable.iterrows():
            lab = {
                "pe_fwd": "Forward P/E",
                "ev_ebitda": "EV/EBITDA",
                "ev_sales": "EV/Sales",
                "pb": "Price/Book",
                "div_yield": "Dividend yield",
            }.get(name, name)
            bars.append((f"Peers, {lab} (quartiles)", row["impliedQ1"], row["implied"], row["impliedQ3"]))
    bars = [b for b in bars if all(math.isfinite(_f(x)) for x in b[1:])]
    if not bars:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 0.55 * len(bars) + 0.9))
    for i, (_lab, lo, mid, hi) in enumerate(reversed(bars)):
        ax.barh(i, hi - lo, left=lo, height=0.45, color="#a0aec0")
        ax.plot([mid, mid], [i - 0.3, i + 0.3], color="#1a202c", lw=2)
    ax.set_yticks(range(len(bars)))
    ax.set_yticklabels([b[0] for b in reversed(bars)], fontsize=8)
    ax.axvline(price, color="#c53030", lw=1.4, ls="--")
    fair = _f(val.fair_value)
    if math.isfinite(fair):
        ax.axvline(fair, color="#2f855a", lw=1.4)
    ax.set_xlabel("Value per share (USD)", fontsize=8)
    ax.tick_params(axis="x", labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    handles = [plt.Line2D([0], [0], color="#c53030", ls="--", label=f"Price {money(price)}")]
    if math.isfinite(fair):
        handles.append(plt.Line2D([0], [0], color="#2f855a", label=f"Fair value {money(fair)}"))
    ax.legend(
        handles=handles, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=2
    )
    return _png(fig)


def mc_hist(val):
    mc = (val.result or {}).get("mc") or {}
    v = mc.get("values")
    if v is None or len(v) < 20:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    lo, hi = _f(mc["p5"]), _f(mc["p95"])
    ax.hist([x for x in v if lo * 0.8 <= x <= hi * 1.2], bins=40, color="#a0aec0")
    ax.axvline(val.price, color="#c53030", ls="--", lw=1.4)
    ax.axvline(_f(mc["median"]), color="#1a202c", lw=1.6)
    ax.set_xlabel("Simulated value per share (USD)", fontsize=8)
    ax.set_yticks([])
    ax.tick_params(axis="x", labelsize=8)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    return _png(fig)


# ------------------------------------------------------------------------------------------------ sections
def _head(val, st, title):
    m = (val.inputs or {}).get("meta") or {}
    return [
        Paragraph(f"{title}", st["h1"]),
        Paragraph(
            f"{val.name} ({val.ticker})&nbsp;&nbsp;|&nbsp;&nbsp;valued {m.get('valuation_date', '')}&nbsp;&nbsp;|&nbsp;&nbsp;{val.scenario} case",
            st["sub"],
        ),
    ]


def page_summary(val, st):
    vd, price = val.verdict, val.price
    call = vd.get("call", "NOT VALUED")
    colour = GOOD if call == "UNDERVALUED" else BAD if call == "OVERVALUED" else FLAT
    out = _head(val, st, "Valuation report")
    box = Table(
        [
            [
                Paragraph(
                    f"<font color='{colour.hexval().replace('0x', '#')}'><b>{call}</b></font>",
                    ParagraphStyle("v", parent=st["body"], fontSize=15, leading=18),
                ),
                Paragraph(
                    f"<b>{money(val.fair_value)}</b><br/><font size=8 color='#718096'>fair value per share</font>",
                    st["body"],
                ),
                Paragraph(f"<b>{money(price)}</b><br/><font size=8 color='#718096'>price</font>", st["body"]),
                Paragraph(
                    f"<b>{pct(val.upside, 1, True)}</b><br/><font size=8 color='#718096'>upside to fair value</font>",
                    st["body"],
                ),
            ]
        ],
        colWidths=[1.9 * inch, 1.6 * inch, 1.5 * inch, 1.8 * inch],
    )
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, INK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    out += [box, Spacer(1, 6)]
    if vd.get("confidence") == "low":
        out.append(
            Paragraph(
                "<b>Low confidence:</b> the methods disagree widely or lean on weak inputs. Treat the call as tentative.",
                st["body"],
            )
        )
    out.append(Paragraph("In plain words", st["h2"]))
    for line in S.reading(val):
        out.append(Paragraph(line, st["bullet"], bulletText="•"))
    out.append(Paragraph("Range of value by method", st["h2"]))
    img = football(val)
    if img:
        out.append(Image(img, width=6.8 * inch, height=6.8 * inch * _aspect(img)))
    W = val.result.get("W") or {}
    beta = W.get("beta") or {}
    rows = [
        ["Cost of equity", pct(W.get("ke")), "Beta used", f"{_f(beta.get('chosen')):.2f}" if beta else "n/a"],
        ["WACC", pct(W.get("wacc")), "Market risk premium", pct(W.get("mrp"))],
        [
            "Expected return at today's price",
            pct(vd.get("expectedReturn")),
            "Required return",
            pct(vd.get("requiredReturn")),
        ],
        [
            "Probability value exceeds price",
            pct((val.result.get("mc") or {}).get("probAbove"), 0),
            "Earnings quality",
            str((vd.get("quality") or {}).get("grade", "n/a")),
        ],
    ]
    out += [
        Spacer(1, 4),
        _table(rows, [2.1 * inch, 1.1 * inch, 2.1 * inch, 1.1 * inch], header=False, align_right_from=99),
    ]
    return out


def _aspect(buf):
    from PIL import Image as PILImage

    buf.seek(0)
    w, h = PILImage.open(buf).size
    buf.seek(0)
    return h / w


def page_forecast(val, st):
    inp, R = val.inputs, val.result
    units = (inp.get("meta") or {}).get("units", "")
    out = _head(val, st, "Forecast and scenarios")
    base = (R.get("scen3") or {}).get("Base") or R.get("base") or {}
    rows = base.get("rows") or []
    model = inp.get("model", "fcff")
    out.append(Paragraph(f"Base-case forecast ({units})", st["h2"]))
    if rows and model == "fcff":
        cols = [
            ("Revenue", "rev", big),
            ("Growth", "growth", pct),
            ("Operating margin", "margin", pct),
            ("EBIT", "ebit", big),
            ("Free cash flow", "fcff", big),
        ]
    elif rows and model == "excess_return":
        cols = [
            ("Return on equity", "roe", pct),
            ("Net income", "ni", big),
            ("Book value, start", "bvBegin", big),
            ("Excess return", "er", big),
            ("Book value per share", "bvps", lambda x, u=None: money(x)),
        ]
    else:
        cols = [
            ("Dividend growth", "g", pct),
            ("Dividend per share", "dps", lambda x, u=None: money(x)),
            ("Present value", "pv", lambda x, u=None: money(x)),
        ]
    if rows:
        sel = rows[:10]
        data = [["", *[str(r.get("label", i + 1)) for i, r in enumerate(sel)]]]
        for name, key, fn in cols:
            data.append([name, *[(fn(r.get(key)) if fn is pct else fn(r.get(key), units)) for r in sel]])
        out.append(_table(data, [1.45 * inch] + [0.54 * inch] * len(sel), align_right_from=1))
        out.append(
            Paragraph(
                "Figures are taken from the model's Base forecast; years are fiscal years.", st["small"]
            )
        )
    out.append(Paragraph("Scenarios", st["h2"]))
    s3 = R.get("scen3") or {}
    data = [["Case", "Value per share", "vs price", "Terminal value share"]]
    for k in ("Bear", "Base", "Bull"):
        if k in s3:
            d = s3[k]
            data.append(
                [
                    k,
                    money(d["perShare"]),
                    pct(_f(d["perShare"]) / val.price - 1, 1, True),
                    pct(d.get("tvShare"), 0),
                ]
            )
    out.append(_table(data, [1.4 * inch, 1.6 * inch, 1.4 * inch, 1.8 * inch]))
    img = mc_hist(val)
    if img:
        mc = R["mc"]
        out += [
            Paragraph("Monte Carlo", st["h2"]),
            Paragraph(
                f"{mc['n']:,} draws vary growth, margins, the discount rate and terminal assumptions together. Median {money(mc['median'])}; "
                f"10th to 90th percentile {money(mc['p10'])} to {money(mc['p90'])}. The dashed line is the price.",
                st["body"],
            ),
            Image(img, width=6.6 * inch, height=6.6 * inch * _aspect(img)),
        ]
    return out


def page_comps(val, st):
    R = val.result
    out = _head(val, st, "Comparables")
    tbl = (R.get("rel") or {}).get("table")
    names = {
        "pe_fwd": "Forward P/E",
        "ev_ebitda": "EV / EBITDA",
        "ev_sales": "EV / Sales",
        "pb": "Price / Book",
        "div_yield": "Dividend yield",
    }
    if tbl is not None and len(tbl):
        data = [["Multiple", "Peers", "Peer median", "This company", "Premium", "Implied value", "Weight"]]
        for name, r in tbl.iterrows():
            data.append(
                [
                    names.get(name, name),
                    f"{int(r['n'])}",
                    f"{_f(r['k']):.2f}",
                    f"{_f(r['own']):.2f}",
                    pct(r["premium"], 0, True),
                    money(r["implied"]),
                    pct(r["weight"], 0) if r["usable"] else "left out",
                ]
            )
        out += [
            _table(
                data, [1.2 * inch, 0.6 * inch, 0.95 * inch, 1.0 * inch, 0.8 * inch, 1.1 * inch, 0.75 * inch]
            ),
            Spacer(1, 4),
        ]
        out.append(Paragraph(f"Blended value from peers: <b>{money(R['rel']['blended'])}</b>.", st["body"]))
    peers = ((val.inputs.get("peers") or {}).get("rows")) or {}
    if peers:
        out.append(Paragraph("Peers used", st["h2"]))
        out.append(Paragraph(", ".join(sorted(peers)), st["body"]))
    reg = R.get("reg") or {}
    if math.isfinite(_f(reg.get("implied"))):
        out += [
            Paragraph("Peer regression", st["h2"]),
            Paragraph(
                f"Across {int(reg['n'])} peers the regression implies {money(reg['implied'])} per share (R-squared {_f(reg['r2']):.2f})."
                + (" The fit is weak, so it is shown but not weighted." if reg.get("weak") else ""),
                st["body"],
            ),
        ]
    return out


def page_checks(val, st):
    inp = val.inputs
    out = _head(val, st, "Checks, sources and method")
    vd = val.verdict
    notes = [w["msg"] if isinstance(w, dict) else str(w) for w in vd.get("warnings", [])]
    dq = (inp.get("data_quality") or {}).get("warnings") or []
    notes += [w["msg"] if isinstance(w, dict) else str(w) for w in dq]
    out.append(Paragraph("Notes on the data and the call", st["h2"]))
    seen = []
    for n in notes:
        if n not in seen:
            seen.append(n)
    if seen:
        out += [Paragraph(n, st["bullet"], bulletText="•") for n in seen[:14]]
    else:
        out.append(Paragraph("No warnings.", st["body"]))
    for fl in (vd.get("quality") or {}).get("flags", []):
        out.append(Paragraph(f"Earnings quality: {fl['msg']}", st["bullet"], bulletText="•"))
    prov = inp.get("provenance") or {}
    if prov:
        kinds = {}
        for p in prov.values():
            kinds[p[0]] = kinds.get(p[0], 0) + 1
        out.append(Paragraph("Where the inputs came from", st["h2"]))
        out.append(
            Paragraph(
                ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda x: -x[1]))
                + ". Sourced: read from a feed or filing. Derived: computed from sourced figures. Assumption: a modelling choice.",
                st["body"],
            )
        )
        known = (
            "Yahoo Finance quoteSummary",
            "Yahoo Finance fundamentals",
            "Yahoo Finance chart",
            "SEC EDGAR",
            "FRED",
            "Damodaran",
        )
        srcs = sorted({k for p in prov.values() if p[1] for k in known if str(p[1]).startswith(k)})
        if srcs:
            out.append(Paragraph("Sources: " + "; ".join(srcs[:10]) + ".", st["body"]))
    fs = inp.get("fundamentals_source")
    if fs:
        out.append(
            Paragraph(
                f"Financial statements: {fs.get('source', 'SEC filings')}, latest filing {fs.get('filed', '')}.",
                st["body"],
            )
        )
    out.append(Paragraph("Method", st["h2"]))
    model = inp.get("model", "fcff")
    method = {
        "fcff": "Free cash flow to the firm is forecast for ten years in three cases, discounted at the weighted average cost of capital, with a terminal value; net debt, leases and minority interests are deducted to reach equity value per share. A Monte Carlo varies the main drivers together; peer multiples and a growth-versus-multiple regression give cross-checks. The verdict blends the methods and calls the stock only when the gap to the price is wide enough to matter.",
        "excess_return": "Equity value is book value plus the present value of returns above the cost of equity: ten years of forecast return on equity, then a terminal excess return that fades with a persistence factor. Cross-checks come from peer price-to-earnings and price-to-book multiples.",
        "ddm": "Dividends per share are forecast for ten years at a growth path that settles to the terminal rate, discounted at the cost of equity, plus a Gordon value at the end. Cross-checks come from dividend yield and EV/EBITDA multiples. Funds from operations are not in the feed, so check the payout against the company's supplement.",
    }[model]
    out += [Paragraph(method, st["body"]), Spacer(1, 10), Paragraph(DISCLAIMER, st["small"])]
    return out


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.8 * inch, 0.5 * inch, "Educational research, not investment advice.")
    canvas.drawRightString(letter[0] - 0.8 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(val, compress: bool = True) -> bytes:
    """The report for one service.Valuation as PDF bytes (compress=False keeps the text readable in the raw file)."""
    if not val.ok:
        raise ValueError(f"Cannot report on {val.ticker}: {val.note}")
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.8 * inch,
        title=f"{val.name} ({val.ticker}) valuation report",
        author="Valuation engine",
        pageCompression=1 if compress else 0,
    )
    story = []
    for i, page in enumerate((page_summary, page_forecast, page_comps, page_checks)):
        if i:
            story.append(PageBreak())
        story += page(val, st)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def save_pdf(val, path) -> str:
    with open(path, "wb") as f:
        f.write(build_pdf(val))
    return str(path)


__all__ = ["KeepTogether", "build_pdf", "save_pdf"]
