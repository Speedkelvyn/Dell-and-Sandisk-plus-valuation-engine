import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import backtest_cycle as bc

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA, CSV = ROOT / "backtest" / "data", ROOT / "sndk" / "MEMORY_CYCLE_HISTORY.csv"


def test_history_uses_only_fiscal_years_that_had_ended_and_been_filed_by_the_origin():
    h = bc.history(CSV, "MU", "2018-11-15")
    assert h[-1][0] == "2018-08"  # FY2018 ended 30 Aug 2018 and was filed in October
    assert all(row[0] <= "2018-08" for row in h)
    assert [r[0] for r in bc.history(CSV, "MU", "2018-09-30")][-1] == "2017-08"  # FY2018 not yet 60 days old


@pytest.mark.skipif(not (DATA / "MU_edgar.json.gz").exists(), reason="backtest data not present")
def test_the_rule_values_micron_at_every_origin_and_the_flat_baseline_ignores_the_cycle():
    rows = bc.run(str(DATA), "MU", str(CSV))
    ok = [r for r in rows if "error" not in r]
    assert len(ok) >= 7
    for r in ok:
        assert r["cycle"] and r["cycle"] > 0 and r["flat"] and r["flat"] > 0
    peak = next(
        r for r in ok if r["asof"] == "2018-11-15"
    )  # 49% margin: the largest in the history to that date
    assert (
        peak["cycle"] < peak["flat"] * 1.2
    )  # a peak origin is not valued above the no-cycle baseline by much
