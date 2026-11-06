"""oos.py: the statistics and the growth-shrinkage step, on synthetic data."""

import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import oos


def _inp(growth, model="fcff"):
    sc = {s: {"growth": {"total": list(growth)}} for s in ("Bear", "Base", "Bull")}
    return {"model": model, "scenarios": sc}


def test_shrink_pulls_growth_toward_the_median_and_leaves_the_median_alone():
    inps = {f"T{i}": _inp([0.02 * i, 0.01 * i]) for i in range(1, 8)}  # median name is T4
    oos.shrink_growth(inps, 0.5)
    assert inps["T4"]["scenarios"]["Base"]["growth"]["total"] == [0.08, 0.04]
    assert abs(inps["T7"]["scenarios"]["Base"]["growth"]["total"][0] - (0.5 * 0.14 + 0.5 * 0.08)) < 1e-12
    assert abs(inps["T1"]["scenarios"]["Bull"]["growth"]["total"][1] - (0.5 * 0.01 + 0.5 * 0.04)) < 1e-12


def test_shrink_with_lam_one_changes_nothing_and_skips_other_models_and_small_sets():
    inps = {f"T{i}": _inp([0.02 * i]) for i in range(1, 8)}
    before = {k: v["scenarios"]["Base"]["growth"]["total"][:] for k, v in inps.items()}
    oos.shrink_growth(inps, 1.0)
    assert {k: v["scenarios"]["Base"]["growth"]["total"] for k, v in inps.items()} == before
    few = {f"T{i}": _inp([0.1 * i]) for i in range(1, 4)}
    oos.shrink_growth(few, 0.0)
    assert few["T1"]["scenarios"]["Base"]["growth"]["total"] == [0.1]
    mixed = {f"T{i}": _inp([0.02 * i]) for i in range(1, 7)}
    mixed["BANK"] = _inp([0.5], model="excess_return")
    oos.shrink_growth(mixed, 0.0)
    assert mixed["BANK"]["scenarios"]["Base"]["growth"]["total"] == [0.5]


def test_newey_west_t_matches_the_plain_t_for_uncorrelated_data_and_widens_for_autocorrelated():
    rng = np.random.default_rng(0)
    iid = rng.normal(0.3, 1.0, 400)
    plain = iid.mean() / (iid.std() / len(iid) ** 0.5)
    assert abs(oos.nw_t(iid, lag=3) - plain) < 0.25 * abs(plain)
    ar = np.zeros(400)
    for i in range(1, 400):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    ar += 0.3
    assert abs(oos.nw_t(ar, lag=6)) < abs(ar.mean() / (ar.std() / 20))
    assert np.isnan(oos.nw_t([1.0, 2.0, 3.0]))


def test_block_ci_brackets_the_mean_and_is_reproducible():
    x = np.random.default_rng(1).normal(0.1, 0.2, 40)
    lo, hi = oos.block_ci(x)
    assert lo < x.mean() < hi
    assert (lo, hi) == oos.block_ci(x)
    assert all(np.isnan(oos.block_ci([1.0, 2.0])))


def _recs(slope, dates=6, names=15, seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for d in range(dates):
        for _n in range(names):
            up = float(rng.normal())
            out.append(
                {
                    "asof": f"2023-{d + 1:02d}-28",
                    "upside": up,
                    "excess12": slope * up + float(rng.normal(0, 0.5)),
                }
            )
    return out


def test_information_coefficient_has_the_sign_of_the_relationship_and_needs_enough_names():
    assert np.mean(list(oos.by_date_ic(_recs(+1.0)).values())) > 0.5
    assert np.mean(list(oos.by_date_ic(_recs(-1.0)).values())) < -0.5
    assert oos.by_date_ic(_recs(1.0, names=oos.MIN_NAMES - 1)) == {}


def test_paired_difference_is_positive_for_a_better_signal_and_zero_against_itself():
    good, bad = _recs(+1.0), _recs(0.0, seed=9)
    assert oos.paired_diff(good, bad)["mean"] > 0.3
    same = oos.paired_diff(good, good)
    assert same["mean"] == 0 and same["dates"] == 6


def test_report_says_so_when_the_quality_grade_was_never_computed():
    recs = []
    for r in _recs(0.0, dates=8):
        for lam in (1.0, 0.25, 0.5, 0.75):
            recs.append(
                {**r, "lam": lam, "model": "fcff", "grade": "n/a", "ticker": "X", "asof": "2023-03-31"}
            )
    # spread the dates over the test window
    for i, r in enumerate(recs):
        r["asof"] = [
            "2023-03-31",
            "2023-06-30",
            "2023-09-30",
            "2023-12-31",
            "2024-03-31",
            "2024-06-30",
            "2024-09-30",
            "2024-12-31",
        ][i // 60 % 8]
    res = oos.analyse(recs)
    assert res["grade_testable"] is False and res["best_grade"] is None
    text = oos.report(recs, res, ["X"])
    assert "could not be tested" in text and "grades A-B" not in text and "nan" not in text
