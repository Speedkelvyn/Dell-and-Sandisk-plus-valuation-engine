"""Client PDF report: one report per model type, built offline from saved data, with the figures the valuation gave."""

import gzip
import json
import pathlib
import subprocess
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE))
import report_pdf  # noqa: E402

import research as RP  # noqa: E402
import run_valuation  # noqa: E402
import service  # noqa: E402
import valuation_v2 as v  # noqa: E402


def valuation(t):
    f = RP.from_fixture(json.loads(gzip.decompress((FIX / f"{t}_yahoo_2026-11-05.json.gz").read_bytes())))
    inp = RP.build(f)
    return service.Valuation(t, "Base", inputs=inp, result=v.run_all(inp, "Base", mc_n=300), source="fixture")


def text(pdf: bytes, tmp_path) -> str:
    p = tmp_path / "r.pdf"
    p.write_bytes(pdf)
    return subprocess.run(
        ["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("t", ["ON", "JPM", "O"])
def test_report_is_a_four_page_pdf_carrying_the_verdict_and_fair_value(t, tmp_path):
    val = valuation(t)
    pdf = report_pdf.build_pdf(val)
    assert pdf[:5] == b"%PDF-"
    out = text(pdf, tmp_path)
    assert out.count("\f") == 4  # four pages
    assert val.verdict["call"] in out
    assert f"${val.fair_value:,.2f}" in out and f"${val.price:,.2f}" in out
    assert "not investment advice" in out
    assert val.name.split()[0] in out


def test_report_for_the_published_dell_page_matches_the_engine(tmp_path):
    inp = run_valuation.load("dell")
    val = service.Valuation(
        "DELL", "Base", inputs=inp, result=v.run_all(inp, "Base", mc_n=300), source="published"
    )
    out = text(service.pdf_bytes(val), tmp_path)
    for k in ("Bear", "Base", "Bull"):
        assert f"${val.result['scen3'][k]['perShare']:,.2f}" in out


def test_report_refuses_a_valuation_that_did_not_run():
    val = service.Valuation("X", "Base", error="Yahoo returned nothing")
    with pytest.raises(ValueError):
        report_pdf.build_pdf(val)


def test_value_ticker_writes_the_pdf_with_the_flag(tmp_path):
    import value_ticker

    snap = tmp_path / "ON_yahoo_2026-11-05.json"
    snap.write_bytes(gzip.decompress((FIX / "ON_yahoo_2026-11-05.json.gz").read_bytes()))
    _, R, written = value_ticker.value(
        snapshot=snap, out=tmp_path / "on", mc=200, excel=False, page=False, pdf=True
    )
    assert R["checks"]["ok"] and written["report"].read_bytes()[:5] == b"%PDF-"
    _, _, written = value_ticker.value(snapshot=snap, out=tmp_path / "on2", mc=200, excel=False, page=False)
    assert "report" not in written
