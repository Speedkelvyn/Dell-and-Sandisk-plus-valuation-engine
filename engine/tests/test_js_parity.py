"""The browser engine (web/model.js) and the Python engine must agree."""

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import run_valuation  # noqa: E402
import valuation_v2 as v  # noqa: E402

NODE = shutil.which("node")
JS = """
const M = require(process.argv[1]);
const I = JSON.parse(require("fs").readFileSync(0, "utf8"));
const R = M.runAll(I, "Base", { n: 200, seed: 7 });
const s = {};
for (const k of Object.keys(R.scen3)) s[k] = R.scen3[k].perShare;
console.log(JSON.stringify({ wacc: R.W.wacc, ke: R.W.ke, base: R.base.perShare, scen: s, comps: R.rel.blended }));
"""


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize("company", ["dell", "sndk"])
def test_python_and_javascript_engines_agree(company):
    inp = run_valuation.load(company)
    out = subprocess.run(
        [NODE, "-e", JS, str(ENGINE / "web" / "model.js")],
        input=json.dumps(inp),
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(out.stdout)
    py = v.run_all(inp, "Base", mc_n=200)
    assert py["W"]["wacc"] == pytest.approx(js["wacc"], rel=1e-12)
    assert py["W"]["ke"] == pytest.approx(js["ke"], rel=1e-12)
    assert py["base"]["perShare"] == pytest.approx(js["base"], rel=1e-12)
    for k, val in js["scen"].items():
        assert py["scen3"][k]["perShare"] == pytest.approx(val, rel=1e-12)
    assert py["rel"]["blended"] == pytest.approx(js["comps"], rel=1e-12)


CYCLE_JS = """
const C = require(process.argv[1]);
const I = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = { scen: {}, implied: C.impliedMidMargin(I.cycle.scenarios.Base, I, I.market.price) };
for (const k of Object.keys(I.cycle.scenarios)) {
  const v = C.valueAt(I.cycle.scenarios[k], I);
  out.scen[k] = { per_share: v.per_share, ev: v.ev, tv_share: v.tv_share, wacc: v.wacc, fcff: v.fcff, roic: v.roic };
}
const mc = C.monteCarlo(I, 4000, 1);
out.mc = { median: mc.median, probAbove: mc.probAbove, p10: mc.percentiles[1].per_share, p90: mc.percentiles[9].per_share };
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_cycle_model_python_and_javascript_agree():
    import value_sndk_cycle as vs

    import cycle_dcf as cd

    inp = json.loads((ENGINE.parent / "sndk" / "SNDK_inputs.json").read_text())
    inp["cycle"] = vs.cycle_block()  # the page reads the assumptions from the inputs file
    out = subprocess.run(
        [NODE, "-e", CYCLE_JS, str(ENGINE / "web" / "cycle.js")],
        input=json.dumps(inp),
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(out.stdout)
    for k, x in vs.SCENARIOS.items():
        py = cd.value(vs.build(x, inp))
        assert py["per_share"] == pytest.approx(js["scen"][k]["per_share"], rel=1e-9)
        assert py["ev"] == pytest.approx(js["scen"][k]["ev"], rel=1e-9)
        assert py["tv_share"] == pytest.approx(js["scen"][k]["tv_share"], rel=1e-9)
        assert list(py["wacc"]) == pytest.approx(js["scen"][k]["wacc"], rel=1e-9)
        assert list(py["fcff"]) == pytest.approx(js["scen"][k]["fcff"], rel=1e-9)
        assert list(py["roic"]) == pytest.approx(js["scen"][k]["roic"], rel=1e-9)
    assert vs.implied_mid_cycle_margin(inp, vs.SCENARIOS["Base"], inp["market"]["price"]) == pytest.approx(
        js["implied"], abs=1e-9
    )
    # different random generators: the statistics agree, the draws do not
    mc = vs.run(inp, size=4000, seed=1)
    assert mc["percentiles"][5]["per_share"] == pytest.approx(js["mc"]["median"], rel=0.03)
    assert mc["percentiles"][1]["per_share"] == pytest.approx(js["mc"]["p10"], rel=0.05)
    assert mc["percentiles"][9]["per_share"] == pytest.approx(js["mc"]["p90"], rel=0.05)
    assert mc["p_above_price"] == pytest.approx(js["mc"]["probAbove"], abs=0.04)
