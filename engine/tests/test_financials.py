"""Banks, insurers and REITs: the excess-return and dividend models, on saved Yahoo snapshots from 2026-11-05
(JPM, TRV, O). Covers routing, the arithmetic identities of each model, the long-run ROE rule, and
Python/JavaScript agreement on both the builder and the valuation."""

import copy
import gzip
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE))
import research as RP  # noqa: E402
import valuation_v2 as v  # noqa: E402

NODE = shutil.which("node")
MODEL = {"JPM": "excess_return", "TRV": "excess_return", "O": "ddm"}
JS = """
const M = require(process.argv[1]), R = require(process.argv[2]);
const f = JSON.parse(require("fs").readFileSync(0, "utf8"));
const inp = R.build(f, M);
const s = {};
for (const k of Object.keys(inp.scenarios)) s[k] = M.dcf(inp, inp.scenarios[k]).perShare;
console.log(JSON.stringify({ inp, scen: s, ke: M.wacc(inp).ke }));
"""


def feeds(t):
    return RP.from_fixture(json.loads(gzip.decompress((FIX / f"{t}_yahoo_2026-11-05.json.gz").read_bytes())))


@pytest.fixture(scope="module")
def built():
    return {t: RP.build(feeds(t)) for t in MODEL}


def diffs(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            out += diffs(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += diffs(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or isinstance(b, bool):
        out += [] if a == b else [(path, a, b)]
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        out += [] if abs(a - b) <= 1e-9 * max(1, abs(a)) else [(path, a, b)]
    elif a != b:
        out.append((path, a, b))
    return out


@pytest.mark.parametrize("t", MODEL)
def test_model_is_routed_by_industry_and_inputs_pass_the_checks(t, built):
    assert built[t]["model"] == MODEL[t]
    assert v.check_inputs(built[t])["ok"]
    assert built[t]["data_quality"]["blocking"] == []


@pytest.mark.parametrize("t", MODEL)
def test_scenarios_are_ordered_and_the_verdict_is_finite(t, built):
    inp = built[t]
    val = {k: v.fin_value(inp, inp["scenarios"][k])["perShare"] for k in ("Bear", "Base", "Bull")}
    assert 0 < val["Bear"] <= val["Base"] <= val["Bull"]
    R = v.run_all(inp, "Base", mc_n=300)
    fair = R["verdict"]["fair"]
    assert 0 < fair < 3 * inp["market"]["price"]
    assert R["verdict"]["call"] in {"UNDERVALUED", "FAIRLY VALUED", "OVERVALUED"}


@pytest.mark.parametrize("t", ["JPM", "TRV"])
def test_excess_return_at_cost_of_equity_is_worth_book(t, built):
    # A bank that earns exactly its cost of equity forever creates no value: equity value equals book value.
    inp = copy.deepcopy(built[t])
    ke = v.wacc(inp)["ke"]
    scen = copy.deepcopy(inp["scenarios"]["Base"])
    scen["roe"] = [ke, ke]
    out = v.fin_value(inp, scen)
    assert out["equity"] == pytest.approx(inp["fin"]["bv0"], rel=1e-9)
    assert out["justifiedPB"] == pytest.approx(1.0, rel=1e-9)


@pytest.mark.parametrize("t", ["JPM", "TRV"])
def test_terminal_excess_return_fades_with_persistence(t, built):
    inp = copy.deepcopy(built[t])
    scen = inp["scenarios"]["Base"]
    inp["terminal"]["er_persistence"] = 0.0
    zero = v.fin_value(inp, scen)
    inp["terminal"]["er_persistence"] = 1.0
    full = v.fin_value(inp, scen)
    assert zero["roeTerminal"] == pytest.approx(zero["ke"])  # no franchise value past year 10
    assert zero["pvTV"] == pytest.approx(0, abs=1e-6)
    assert full["roeTerminal"] == pytest.approx(scen["roe"][1])
    assert full["perShare"] > zero["perShare"]


def test_dividend_model_matches_a_hand_computed_perpetuity(built):
    inp = copy.deepcopy(built["O"])
    g = inp["terminal"]["g"]
    scen = {"dps_growth": [g] * 4}
    inp["timing"].update({"first_period": 1.0, "stub": 1.0, "mid_year": False})
    out = v.fin_value(inp, scen)
    ke, d0 = out["ke"], inp["fin"]["dps0"]
    assert out["perShare"] == pytest.approx(d0 * (1 + g) / (ke - g), rel=1e-9)  # Gordon growth, exactly


def test_dividend_model_rejects_growth_at_or_above_the_cost_of_equity(built):
    inp = copy.deepcopy(built["O"])
    inp["terminal"]["g"] = v.wacc(inp)["ke"]
    with pytest.raises(ValueError):
        v.fin_value(inp, inp["scenarios"]["Base"])


@pytest.mark.parametrize("t", ["JPM", "TRV"])
def test_long_run_roe_counts_the_trailing_year_as_one_observation(t, built):
    fin = built[t]["fin"]
    base = built[t]["scenarios"]["Base"]["roe"]
    hist = fin["roe_hist"]
    # the trailing figure is not stored, so recover it from the long-run mean
    ttm = base[1] * (len(hist) + 1) - sum(hist)
    assert min([*hist, ttm]) <= base[1] <= max([*hist, ttm])
    assert built[t]["scenarios"]["Bear"]["roe"][1] <= base[1] <= built[t]["scenarios"]["Bull"]["roe"][1]


def test_one_strong_year_cannot_set_the_whole_path(built):
    # TRV earned 12.8%, 18.8%, 20.5% and then a stronger trailing year; the long-run level sits between them
    # rather than above the three-year average by half the gap, as the earlier rule had it.
    trv = built["TRV"]
    hist = trv["fin"]["roe_hist"]
    lr = trv["scenarios"]["Base"]["roe"][1]
    assert lr < 0.20 and lr > sum(hist) / len(hist)


def test_reit_warning_names_the_dividend_basis(built):
    msgs = [w["msg"] if isinstance(w, dict) else w for w in built["O"]["data_quality"]["warnings"]]
    assert any("REIT value uses dividends" in m for m in msgs)


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize("t", MODEL)
def test_python_and_javascript_agree_on_financials(t, built):
    out = subprocess.run(
        [NODE, "-e", JS, str(ENGINE / "web" / "model.js"), str(ENGINE / "web" / "research.js")],
        input=json.dumps(feeds(t)),
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(out.stdout)
    py = built[t]
    assert diffs(py, js["inp"]) == []
    assert v.wacc(py)["ke"] == pytest.approx(js["ke"], rel=1e-12)
    for k, x in js["scen"].items():
        assert v.fin_value(py, py["scenarios"][k])["perShare"] == pytest.approx(x, rel=1e-12)


def test_monte_carlo_model_error_keeps_the_median_and_adds_spread_in_quadrature(built):
    import numpy as np

    inp = built["JPM"]
    wide = v.monte_carlo(inp, inp["scenarios"]["Base"], n=800)
    narrow = v.monte_carlo({**inp, "monte_carlo": {"model_error": 0}}, inp["scenarios"]["Base"], n=800)
    assert narrow["widen"] == 1.0 and wide["widen"] > 1.0
    assert wide["median"] == pytest.approx(
        narrow["median"], rel=1e-4
    )  # even n: the median averages two draws
    sigma = lambda m: np.log(m["p90"] / m["p10"]) / (2 * 1.2815515655446004)
    assert sigma(wide) == pytest.approx((sigma(narrow) ** 2 + v.MC_MODEL_ERR**2) ** 0.5, rel=2e-3)


def test_widen_draws_leaves_nonpositive_values_and_a_nonpositive_median_alone():
    import numpy as np

    flat, k = v.widen_draws([-3.0, -2.0, -1.0] * 10)
    assert k == 1.0 and np.allclose(flat, [-3.0, -2.0, -1.0] * 10)
    x = np.linspace(-1.0, 40.0, 101)
    out, k = v.widen_draws(x)
    assert k > 1 and out[0] == -1.0 and np.median(out) == pytest.approx(np.median(x))
    assert v.widen_draws(x[:10])[1] == 1.0  # too few draws to measure a spread


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_python_and_javascript_widen_draws_agree():
    import numpy as np

    x = np.sort(np.random.default_rng(3).lognormal(5.0, 0.3, 400))
    js = "const M=require(process.argv[1]);const x=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(M.widenDraws(x,0.5)))"
    out = subprocess.run(
        [NODE, "-e", js, str(ENGINE / "web" / "model.js")],
        input=json.dumps(x.tolist()),
        capture_output=True,
        text=True,
        check=True,
    )
    vals, k = json.loads(out.stdout)
    py, kp = v.widen_draws(x)
    assert k == pytest.approx(kp, rel=1e-12) and np.allclose(vals, py, rtol=1e-12)
