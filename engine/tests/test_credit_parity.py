# Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
"""The browser rating (web/credit.js) and the Python rating (credit_score.py) must give the same numbers."""

import json
import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import credit_score as cs  # noqa: E402

NODE = shutil.which("node")
JS = """
const C = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = cases.map((c) => C.rate(c.f, c.me, c.se, c.pr));
console.log(JSON.stringify({ out, card: C.SCORECARD, floors: C.GRADE_FLOORS, share: C.MIN_SHARE }));
"""
FIELDS = ["at", "lt", "sale", "ni", "oibdp", "oiadp", "xint", "act", "lct", "dlc", "dltt", "re", "ceq"]


def cases(n=500, seed=11):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        at = float(np.exp(rng.normal(6.5, 1.5)))
        f = {
            "at": at, "lt": at * rng.uniform(0.2, 1.3), "sale": at * rng.uniform(0.1, 2.5),
            "ni": at * rng.normal(0.02, 0.08), "oibdp": at * rng.normal(0.08, 0.1),
            "oiadp": at * rng.normal(0.05, 0.1), "xint": at * rng.uniform(0.0, 0.05),
            "act": at * rng.uniform(0.1, 0.6), "lct": at * rng.uniform(0.05, 0.5),
            "dlc": at * rng.uniform(0.0, 0.2), "dltt": at * rng.uniform(0.0, 0.5),
            "re": at * rng.normal(0.1, 0.3), "ceq": at * rng.normal(0.3, 0.3),
        }  # fmt: skip
        for k in FIELDS:
            r = rng.random()
            if r < 0.07:
                f[k] = None
            elif r < 0.09:
                f[k] = 0.0
        if rng.random() < 0.15:
            f["dlc"], f["dltt"] = 0.0, 0.0
        has_market = rng.random() < 0.6
        out.append({
            "f": f,
            "me": float(at * rng.uniform(0.02, 3.0)) if has_market else None,
            "se": float(rng.uniform(0.1, 1.4)) if has_market else None,
            "pr": float(rng.uniform(-0.9, 1.5)) if has_market else None,
        })  # fmt: skip
    return out


def py_rate(c):
    r = cs.rate(c["f"], c["me"], c["se"], c["pr"])
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_python_and_javascript_ratings_agree():
    cs_in = cases()
    res = subprocess.run(
        [NODE, "-e", JS, str(ENGINE / "web" / "credit.js")],
        input=json.dumps(cs_in), capture_output=True, text=True, check=True,
    )  # fmt: skip
    js = json.loads(res.stdout)
    graded = 0
    for c, j in zip(cs_in, js["out"], strict=True):
        p = py_rate(c)
        assert p.keys() == j.keys()
        for k in p:
            if p[k] is None or j[k] is None:
                assert p[k] is None and j[k] is None, (k, p[k], j[k], c)
            else:
                assert j[k] == pytest.approx(p[k], rel=1e-12, abs=1e-12), (k, c)
        graded += p["grade"] is not None
    assert graded > 300  # the cases exercise real ratings, not only gaps
    assert js["floors"] == cs.GRADE_FLOORS and js["share"] == cs.MIN_SHARE
    for k, (w, table) in cs.SCORECARD.items():
        assert js["card"][k][0] == w
        assert [x for r in js["card"][k][1] for x in r] == pytest.approx([x for r in table for x in r])
