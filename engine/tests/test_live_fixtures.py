"""The engine fixes, checked on live Yahoo snapshots of four stocks taken on 2026-11-05 (SNDK, DELL, ON, NBIS).

Both builders (research.py and web/research.js) must produce the same inputs, warnings and provenance, and both
engines the same values; capex must start at what the company spends today; a build-out must switch to
growth-linked reinvestment; Yahoo's peers must be filtered; a value at or below zero must never reach the blend.
"""

import copy
import gzip
import itertools
import json
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE))
import research as RP  # noqa: E402
import run_valuation  # noqa: E402
import valuation_v2 as v  # noqa: E402

TICKERS = ["SNDK", "DELL", "ON", "NBIS"]
NODE = shutil.which("node")
JS = """
const M = require(process.argv[1]), R = require(process.argv[2]);
const f = JSON.parse(require("fs").readFileSync(0, "utf8"));
const inp = R.build(f, M);
const s = {};
for (const k of Object.keys(inp.scenarios)) s[k] = M.dcf(inp, inp.scenarios[k]).perShare;
console.log(JSON.stringify({ inp, scen: s, wacc: M.wacc(inp).wacc }));
"""


def snapshot(t):
    return json.loads(gzip.decompress((FIX / f"{t}_yahoo_2026-11-05.json.gz").read_bytes()))


@pytest.fixture(scope="module")
def built():
    return {t: RP.build(RP.from_fixture(snapshot(t))) for t in TICKERS}


def _diffs(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            out += _diffs(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += _diffs(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or isinstance(b, bool):
        out += [] if a == b else [(path, a, b)]
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        out += [] if abs(a - b) <= 1e-9 * max(1, abs(a)) else [(path, a, b)]
    elif a != b:
        out.append((path, a, b))
    return out


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize("t", TICKERS)
def test_python_and_javascript_builders_and_engines_agree(t, built):
    f = RP.from_fixture(snapshot(t))
    out = subprocess.run(
        [NODE, "-e", JS, str(ENGINE / "web" / "model.js"), str(ENGINE / "web" / "research.js")],
        input=json.dumps(f),
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(out.stdout)
    py = built[t]
    assert _diffs(py, js["inp"]) == []  # inputs, notes, provenance and every warning, byte for byte
    assert v.wacc(py)["wacc"] == pytest.approx(js["wacc"], rel=1e-12)
    for k, x in js["scen"].items():
        assert v.dcf(py, py["scenarios"][k])["perShare"] == pytest.approx(x, rel=1e-12)


def test_capex_starts_at_trailing_spend_and_fades_to_the_cycle_median(built):
    on = built["ON"]["operating"]
    assert on["capex_pct"] == pytest.approx(0.028, abs=0.001)  # last four quarters
    assert on["capex_lr_pct"] == pytest.approx(0.111, abs=0.001)  # median of the annual years
    rows = v.project(built["ON"], built["ON"]["scenarios"]["Base"])
    pct = [r["capex"] / r["rev"] for r in rows]
    assert pct[0] == pytest.approx(on["capex_pct"]) and pct[4] == pytest.approx(on["capex_lr_pct"])
    assert all(a <= b + 1e-12 for a, b in itertools.pairwise(pct))  # rises steadily, then flat
    # year 1 free cash flow lands near ON's reported 28% margin instead of the old 3-year-average capex
    assert rows[0]["fcff"] / rows[0]["rev"] > 0.2


def test_build_out_switches_to_growth_linked_reinvestment(built):
    nb = built["NBIS"]
    op = nb["operating"]
    assert op["capex_pct"] > 1 and op["sales_to_capital"] == pytest.approx(1.059, abs=0.001)
    assert v.check_inputs(nb)["ok"]
    rows = v.project(nb, nb["scenarios"]["Base"])
    for i, r in enumerate(rows[:-1]):
        added = rows[i + 1]["rev"] - r["rev"]
        assert r["capex"] - r["da"] == pytest.approx(max(added, 0) / op["sales_to_capital"])
    assert np.isfinite(v.dcf(nb, nb["scenarios"]["Base"])["perShare"])
    msgs = [w["msg"] for w in nb["data_quality"]["warnings"]]
    assert any("build-out" in m for m in msgs) and any("floored at B" in m for m in msgs)
    assert nb["scenarios"]["Base"]["margins"]["total"][1] == pytest.approx(
        0.20
    )  # no profitable peer: assumption


def test_hypergrowth_fades_faster(built):
    g = built["NBIS"]["scenarios"]["Base"]["growth"]["total"]
    assert g[0] > 2 and g[1] == pytest.approx(g[0] * 0.4) and g[3] == pytest.approx(0.195)
    sndk = built["SNDK"]["scenarios"]["Base"]["growth"]["total"]  # ordinary growth keeps the old fade
    assert sndk[1] == pytest.approx(sndk[0] * 0.75 + 0.01)


def test_yahoo_peers_are_filtered_by_sector_industry_and_size(built):
    assert set(built["NBIS"]["peers"]["core_set"]) == {"CRWV", "APLD", "CIFR"}  # RKLB, OKLO, IREN, WULF out
    assert "NBIS" not in built["SNDK"]["peers"]["core_set"]
    assert "LRCX" not in built["ON"]["peers"]["core_set"]
    f = RP.from_fixture(snapshot("NBIS"))
    f["peer_source"] = "user"  # peers the user names are kept as given
    assert len(RP.build(f)["peers"]["core_set"]) == 7


def test_monte_carlo_margin_spread_comes_from_history(built):
    assert built["SNDK"]["monte_carlo"]["sd_margin"] == 0.20  # a memory cycle: capped
    assert built["DELL"]["monte_carlo"]["sd_margin"] == 0.01  # steady hardware margins: floored
    assert built["NBIS"]["monte_carlo"]["sd_growth"] == 0.05


def test_margin_shock_widens_the_simulation():
    inp = run_valuation.load("dell")
    narrow = v.monte_carlo(inp, inp["scenarios"]["Base"], n=400)
    wide_inp = copy.deepcopy(inp)
    wide_inp["monte_carlo"] = {"sd_margin": 0.05}
    wide = v.monte_carlo(wide_inp, wide_inp["scenarios"]["Base"], n=400)
    assert wide["p90"] - wide["p10"] > 1.5 * (narrow["p90"] - narrow["p10"])
    scaled_inp = copy.deepcopy(wide_inp)
    scaled_inp["monte_carlo"]["vol_scale"] = 1.5
    scaled = v.monte_carlo(scaled_inp, scaled_inp["scenarios"]["Base"], n=400)
    assert scaled["p90"] - scaled["p10"] > wide["p90"] - wide["p10"]


def test_published_inputs_are_untouched_by_the_new_options():
    inp = run_valuation.load("dell")
    same = copy.deepcopy(inp)
    same["operating"]["capex_lr_pct"] = same["operating"]["capex_pct"]
    same["operating"]["da_lr_pct"] = same["operating"]["da_pct"]
    a, b = v.dcf(inp, inp["scenarios"]["Base"]), v.dcf(same, same["scenarios"]["Base"])
    assert a["perShare"] == b["perShare"]


def test_values_at_or_below_zero_or_absurd_never_enter_the_blend():
    inp = run_valuation.load("dell")
    p = inp["market"]["price"]
    vd = v.verdict(inp, {"dcf": -50.0, "mc": 300.0, "comps": 25 * p, "regression": float("nan")})
    assert set(vd["dropped"]) == {"dcf", "comps", "regression"}
    assert vd["fair"] == pytest.approx(300.0)
    msgs = " ".join(w["msg"] for w in vd["warnings"])
    assert "do not repay the investment" in msgs and "model failure" in msgs
    none = v.verdict(inp, {"dcf": -1.0, "mc": 0.0, "comps": float("nan"), "regression": -3.0})
    assert none["call"] == "NOT VALUED"


def test_capex_above_revenue_without_sales_to_capital_is_blocked():
    inp = run_valuation.load("dell")
    inp["operating"]["capex_pct"] = 2.0
    q = v.check_inputs(inp)
    assert not q["ok"] and any("build-out" in b["msg"] for b in q["blocking"])
    inp["operating"]["sales_to_capital"] = 0.8
    assert v.check_inputs(inp)["ok"]
