// Copyright (c) 2026 Kelvin Afful. MIT License, see LICENSE.
// credit.js -- the credit rating of credit_score.py in the browser. The two must agree (tests/test_credit_parity.py).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Credit = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const ok = (...xs) => xs.every((x) => x !== null && x !== undefined && !Number.isNaN(x));
  const div = (a, b) => (ok(a, b) && b !== 0 ? a / b : NaN);
  const or0 = (x) => (x === null || x === undefined || Number.isNaN(x) || x === 0 ? 0 : x);

  // ----------------------------------------------------------------- spreading
  function spread(f) {
    const debt = ok(f.dlc, f.dltt) ? or0(f.dlc) + or0(f.dltt) : NaN;
    const at = f.at, ebitda = f.oibdp, xint = f.xint;
    let debtEbitda;
    if (ok(ebitda) && ebitda <= 0) debtEbitda = ok(debt) && debt > 0 ? 99.0 : 0.0;
    else debtEbitda = div(debt, ebitda);
    let cover;
    if (ok(debt) && debt <= 0) cover = 99.0;
    else if (ok(xint) && xint > 0) cover = div(ebitda, xint);
    else cover = NaN;
    return {
      debt,
      debt_assets: div(debt, at),
      debt_ebitda: debtEbitda,
      coverage: cover,
      current_ratio: div(f.act, f.lct),
      working_capital_assets: div(ok(f.act, f.lct) ? f.act - f.lct : NaN, at),
      retained_assets: div(f.re, at),
      ebit_assets: div(f.oiadp, at),
      roa: div(f.ni, at),
      ebitda_margin: div(ebitda, f.sale),
      asset_turnover: div(f.sale, at),
      book_equity_liabilities: div(f.ceq, f.lt),
      log_assets: ok(at) && at > 0 ? Math.log(at) : NaN,
    };
  }

  // ----------------------------------------------------------------- Altman
  const dot = (w, parts) => w.reduce((a, x, i) => a + x * parts[i], 0);
  function altmanZ(f, marketEquity) {
    const s = spread(f);
    const parts = [s.working_capital_assets, s.retained_assets, s.ebit_assets, div(marketEquity, f.lt), s.asset_turnover];
    return ok(...parts) ? dot([1.2, 1.4, 3.3, 0.6, 1.0], parts) : NaN;
  }
  function altmanZ2(f) {
    const s = spread(f);
    const parts = [s.working_capital_assets, s.retained_assets, s.ebit_assets, s.book_equity_liabilities];
    return ok(...parts) ? dot([6.56, 3.26, 6.72, 1.05], parts) : NaN;
  }

  // ----------------------------------------------------------------- distance to default
  function distanceToDefault(marketEquity, debtFace, sigmaE, pastReturn, horizon = 1.0) {
    if (!ok(marketEquity, debtFace, sigmaE, pastReturn) || marketEquity <= 0 || sigmaE <= 0) return NaN;
    if (debtFace <= 0) return 10.0;
    const e = marketEquity, f = debtFace;
    const sigmaD = 0.05 + 0.25 * sigmaE;
    const sigmaV = (e / (e + f)) * sigmaE + (f / (e + f)) * sigmaD;
    const dd = (Math.log((e + f) / f) + (pastReturn - 0.5 * sigmaV ** 2) * horizon) / (sigmaV * Math.sqrt(horizon));
    return Math.max(-6.0, Math.min(10.0, dd));
  }

  // ----------------------------------------------------------------- scorecard
  const SCORECARD = {
    debt_assets: [15, [[0.6, 0], [0.45, 20], [0.3, 50], [0.15, 80], [0.05, 100]]],
    coverage: [15, [[1.0, 0], [2.0, 20], [4.0, 50], [8.0, 80], [15.0, 100]]],
    debt_ebitda: [10, [[8.0, 0], [5.0, 20], [3.0, 50], [1.5, 80], [0.5, 100]]],
    current_ratio: [10, [[0.6, 0], [1.0, 30], [1.5, 60], [2.2, 90], [3.0, 100]]],
    roa: [10, [[-0.15, 0], [-0.05, 20], [0.0, 40], [0.04, 70], [0.1, 100]]],
    retained_assets: [5, [[-0.5, 0], [-0.1, 25], [0.0, 40], [0.2, 70], [0.5, 100]]],
    log_assets: [5, [[Math.log(50), 0], [Math.log(300), 40], [Math.log(1500), 70], [Math.log(10000), 100]]],
    dd: [30, [[0.0, 0], [1.0, 20], [2.0, 45], [4.0, 75], [6.0, 100]]],
  };
  const GRADE_FLOORS = [85, 75, 65, 55, 45, 35, 25, 15];
  const MIN_SHARE = 0.7;

  function points(value, table) {
    if (!ok(value)) return NaN;
    const [x0, y0] = table[0], [x1, y1] = table[table.length - 1];
    const rising = x1 > x0;
    if (rising ? value <= x0 : value >= x0) return y0;
    if (rising ? value >= x1 : value <= x1) return y1;
    for (let i = 0; i < table.length - 1; i++) {
      const [xa, ya] = table[i], [xb, yb] = table[i + 1];
      if (rising ? xa <= value && value <= xb : xb <= value && value <= xa) return ya + ((yb - ya) * (value - xa)) / (xb - xa);
    }
    return NaN;
  }

  function score(features) {
    let tot = 0, wsum = 0, offered = 0;
    for (const k of Object.keys(SCORECARD)) {
      const [w, table] = SCORECARD[k];
      if (k === "dd" && !ok(features.dd)) continue;
      offered += w;
      const p = points(features[k], table);
      if (ok(p)) { tot += w * p; wsum += w; }
    }
    return wsum >= MIN_SHARE * offered ? tot / wsum : NaN;
  }

  function grade(s) {
    if (!ok(s)) return null;
    for (let g = 0; g < GRADE_FLOORS.length; g++) if (s >= GRADE_FLOORS[g]) return g + 1;
    return 9;
  }

  function rate(f, marketEquity = null, sigmaE = null, pastReturn = null) {
    const s = spread(f);
    let dd = NaN;
    if (ok(marketEquity, sigmaE, pastReturn) && ok(s.debt)) {
      dd = distanceToDefault(marketEquity, or0(f.dlc) + 0.5 * or0(f.dltt), sigmaE, pastReturn);
    }
    const sc = score({ ...s, dd });
    return { ...s, altman_z: altmanZ(f, marketEquity), altman_z2: altmanZ2(f), dd, score: sc, grade: grade(sc) };
  }

  return { spread, altmanZ, altmanZ2, distanceToDefault, points, score, grade, rate, SCORECARD, GRADE_FLOORS, MIN_SHARE };
});
