"""The Python engine must reproduce the figures shown on the web pages."""

import math
import pathlib
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import run_valuation  # noqa: E402
import valuation_v2 as v  # noqa: E402

# Base-case figures published on the site, 5-Nov-2026 (computed by the page's JavaScript engine)
PAGE = {
    "dell": {
        "dcf": 295.69114207282894,
        "bear": 151.0965705629695,
        "bull": 428.0552961672145,
        "wacc": 0.11301519918390183,
        "ke": 0.12105948836958878,
        "comps": 249.11734435260684,
        "call": "OVERVALUED",
        "fair": 278.74,
    },
    "sndk": {
        "dcf": 1769.2302024429746,
        "bear": 715.8192219794827,
        "bull": 2978.083398518934,
        "wacc": 0.14602440993300494,
        "ke": 0.14616712353524744,
        "comps": 939.118280096095,
        "call": "UNDERVALUED",
        "fair": 1478.93,
    },
}


@pytest.fixture(scope="module", params=sorted(PAGE))
def case(request):
    inp = run_valuation.load(request.param)
    return request.param, inp, v.run_all(inp, "Base", mc_n=1500)


def test_inputs_pass_checks(case):
    _, _, R = case
    assert R["checks"]["ok"]


def test_dcf_and_wacc_match_page_exactly(case):
    name, _, R = case
    P = PAGE[name]
    assert R["base"]["perShare"] == pytest.approx(P["dcf"], rel=1e-9)
    assert R["scen3"]["Bear"]["perShare"] == pytest.approx(P["bear"], rel=1e-9)
    assert R["scen3"]["Bull"]["perShare"] == pytest.approx(P["bull"], rel=1e-9)
    assert R["W"]["wacc"] == pytest.approx(P["wacc"], rel=1e-9)
    assert R["W"]["ke"] == pytest.approx(P["ke"], rel=1e-9)
    assert R["rel"]["blended"] == pytest.approx(P["comps"], rel=1e-9)


def test_verdict_matches_page(case):
    name, _, R = case
    assert R["verdict"]["call"] == PAGE[name]["call"]
    # Monte Carlo sampler differs from the page's, so allow a small gap on the blend
    assert R["verdict"]["fair"] == pytest.approx(PAGE[name]["fair"], rel=0.01)


def test_cli_runs(capsys):
    assert run_valuation.main(["sndk", "--mc", "500"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT" in out and "Sandisk" in out


def test_missing_input_blocks_valuation():
    inp = run_valuation.load("dell")
    inp["capital"]["rf"] = None
    R = v.run_all(inp)
    assert not R["checks"]["ok"]
    assert math.isnan(R["verdict"].get("fair", float("nan"))) or R["verdict"]["call"] in ("NOT VALUED", None)
