/* cycle.js — cycle-based FCFF DCF with copula Monte Carlo. Pure functions, no DOM.
 * Mirrors cycle_dcf.py and the SNDK assumptions in value_sndk_cycle.py; tests/test_js_parity.py checks that
 * value() and buildSpec() give the same numbers. The Monte Carlo uses its own random generator, so its
 * draws differ from NumPy's and only its statistics agree with Python.
 * Units: whatever the caller uses (USD millions for SNDK); rates as decimals.
 */
(function (root) {
  "use strict";

  // ------------------------------------------------------------ paths
  function glide(start, end, n, hold) {
    n = Math.trunc(n);
    hold = Math.max(1, Math.min(Math.trunc(hold || 1), n));
    const out = [];
    for (let i = 0; i < hold - 1; i++) out.push(start);
    const m = n - hold + 1;
    if (m === 1) out.push(start);
    else for (let i = 0; i < m; i++) out.push(start + ((end - start) * i) / (m - 1));
    return out;
  }

  function knotPath(knots, n) {
    const k = knots.map((p) => [p[0], p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const out = [];
    for (let year = 1; year <= n; year++) {
      if (year <= k[0][0]) out.push(k[0][1]);
      else if (year >= k[k.length - 1][0]) out.push(k[k.length - 1][1]);
      else {
        let j = 1;
        while (k[j][0] < year) j++;
        const [x0, y0] = k[j - 1],
          [x1, y1] = k[j];
        out.push(x1 === x0 ? y1 : y0 + ((y1 - y0) * (year - x0)) / (x1 - x0));
      }
    }
    return out;
  }

  const pathOf = (p, n) => (p.knots ? knotPath(p.knots, n) : glide(p.start, p.end, n, p.hold));
  const growthPath = (cycles) => cycles.flatMap((c) => glide(c.g0, c.g1, c.length, c.hold));
  const sum = (a) => a.reduce((s, x) => s + x, 0);

  // ------------------------------------------------------------ one scenario
  function value(spec) {
    const g = growthPath(spec.cycles),
      n = g.length;
    const rev = [];
    let r = spec.base_revenue;
    for (let i = 0; i < n; i++) {
      r *= 1 + g[i];
      rev.push(r);
    }
    const margin = pathOf(spec.margin, n),
      mtr = spec.mtr,
      tax = pathOf(spec.tax, n),
      s2c = pathOf(spec.sales_to_capital, n);
    const liq = spec.liquidation || 0;
    const ebit = rev.map((x, i) => x * margin[i]);
    const nopat = ebit.map((x, i) => x * (1 - tax[i]));
    const reinv = rev.map((x, i) => {
      const d = (x - (i ? rev[i - 1] : spec.base_revenue)) / s2c[i];
      return d > 0 ? d : d * liq;
    });
    const fcff = nopat.map((x, i) => x - reinv[i]);
    const ic = [];
    let c = spec.invested_capital;
    for (let i = 0; i < n; i++) {
      c += reinv[i];
      ic.push(c);
    }
    const roic = nopat.map((x, i) => x / ic[i]);

    const E = spec.equity,
      D = spec.debt,
      we = E / (E + D),
      wd = D / (E + D);
    const beta = pathOf(spec.beta, n).map((b) => b * (1 + ((1 - mtr) * D) / E));
    const kd = pathOf(spec.kd, n).map((k) => k * (1 - mtr));
    const ke = beta.map((b) => spec.rf + b * spec.erp);
    const w = ke.map((k, i) => we * k + wd * kd[i]);

    const tm = spec.timing || {};
    const fp = tm.first_period ?? 1,
      stub = tm.stub ?? 1,
      mid = tm.mid_year ?? false;
    const ends = [],
      starts = [];
    for (let i = 0; i < n; i++) {
      ends.push(fp + i);
      starts.push(i ? fp + i - 1 : 0);
    }
    const lw = w.map((x) => Math.log1p(x));
    const df = (time) => {
      let s = 0;
      for (let i = 0; i < n; i++) s += lw[i] * Math.min(Math.max(time - starts[i], 0), ends[i] - starts[i]);
      return Math.exp(-s);
    };
    const frac = ends.map((_, i) => (i === 0 ? stub : 1));
    const times = ends.map((e, i) => Math.max(mid ? e - 0.5 * frac[i] : e, 0));
    const dfs = times.map(df);
    const pv = fcff.map((x, i) => x * frac[i] * dfs[i]);

    const gT = spec.cycles[spec.cycles.length - 1].g1,
      wT = w[n - 1];
    if (gT >= wT) throw new Error("terminal growth must be below the terminal discount rate");
    const roicT = wT + spec.excess,
      rrT = gT < 0 ? 0 : gT / roicT;
    const taxT = spec.terminal_tax ?? mtr;
    const nopatT = rev[n - 1] * (1 + gT) * margin[n - 1] * (1 - taxT);
    const tv = (nopatT * (1 - rrT)) / (wT - gT);
    const tN = fp + n - 1;
    const pvTv = tv * df(mid ? tN - 0.5 : tN);
    const ev = sum(pv) + pvTv;
    const equity = ev + spec.cash - spec.debt - (spec.other_claims || 0);
    return {
      n,
      revenue: rev,
      growth: g,
      margin,
      ebit,
      tax,
      nopat,
      reinvestment: reinv,
      fcff,
      invested_capital: ic,
      roic,
      beta,
      wacc: w,
      df: dfs,
      pv,
      pv_explicit: sum(pv),
      pv_terminal: pvTv,
      terminal_value: tv,
      terminal_roic: roicT,
      ev,
      equity,
      per_share: spec.shares ? equity / spec.shares : null,
      tv_share: ev ? pvTv / ev : NaN,
    };
  }

  // ------------------------------------------------------------ SNDK assumptions -> spec (mirrors value_sndk_cycle.build)
  function buildSpec(x, inp) {
    const mk = inp.market,
      br = inp.bridge,
      cap = inp.capital,
      cy = inp.cycle;
    const LB = Math.round(x.LB),
      XA = Math.round(x.extA);
    const peakEnd = 2 + XA,
      troughYear = peakEnd + LB - 1,
      midYear = peakEnd + LB + 3;
    const shares = mk.shares_basic + mk.dilution;
    const n = peakEnd + LB + 8;
    return {
      base_revenue: inp.rev_y0,
      cycles: [
        { length: 2, g0: x.gA0, g1: x.gA1 },
        ...(XA ? [{ length: XA, g0: x.gP, g1: x.gP }] : []),
        { length: LB, g0: x.gB0, g1: x.gB1 },
        { length: n - peakEnd - LB, g0: x.gC0, g1: x.gT },
      ],
      margin: {
        knots: [
          [1, x.m27],
          [2, x.m28],
          [peakEnd, x.m28],
          [troughYear, x.trough],
          [midYear, x.mid],
        ],
      },
      tax: {
        knots: [
          [1, inp.operating.tax0],
          [5, inp.operating.tax_lr],
        ],
      },
      mtr: cap.tax_marginal,
      terminal_tax: inp.operating.tax_lr,
      sales_to_capital: { start: x.s2c0, end: x.s2cT },
      liquidation: 0,
      invested_capital: inp.operating.nwc0 + cy.ppe,
      equity: mk.price * shares,
      debt: br.core_debt + br.leases,
      beta: {
        knots: [
          [1, x.beta0],
          [3, x.beta0],
          [10, x.betaT],
        ],
      },
      kd: { start: cap.kd_pretax, end: cap.kd_pretax },
      rf: x.rf ?? cap.rf,
      erp: x.erp ?? cap.erp,
      excess: x.excess,
      cash: br.cash,
      other_claims: br.nci,
      shares,
      timing: inp.timing,
    };
  }

  const valueAt = (x, inp) => value(buildSpec(x, inp));

  // Mid-cycle margin at which the scenario's value per share equals the target (bisection), or null.
  function impliedMidMargin(x, inp, target) {
    let lo = -0.2,
      hi = 0.9;
    const f = (m) => valueAt({ ...x, mid: m }, inp).per_share - target;
    if (f(lo) > 0 || f(hi) < 0) return null;
    for (let i = 0; i < 60; i++) {
      const m = (lo + hi) / 2;
      if (f(m) < 0) lo = m;
      else hi = m;
    }
    return (lo + hi) / 2;
  }

  // ------------------------------------------------------------ Monte Carlo
  function rng(seed) {
    // mulberry32 + Box-Muller
    let a = seed >>> 0;
    const u = () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    return () => Math.sqrt(-2 * Math.log(1 - u())) * Math.cos(2 * Math.PI * u());
  }

  function erf(x) {
    // Abramowitz-Stegun 7.1.26 refined by one series-free correction is unnecessary here: 1.5e-7 is far below sampling noise
    const s = Math.sign(x);
    x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y =
      1 -
      ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
        t *
        Math.exp(-x * x);
    return s * y;
  }
  const phi = (z) => 0.5 * (1 + erf(z / Math.SQRT2));

  function normInv(p) {
    // Acklam's rational approximation, relative error 1.2e-9
    const a = [
      -39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716,
      2.506628277459239,
    ];
    const b = [
      -54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572,
    ];
    const c = [
      -0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968,
      2.938163982698783,
    ];
    const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
    p = Math.min(Math.max(p, 1e-12), 1 - 1e-12);
    let q, r;
    if (p < 0.02425) {
      q = Math.sqrt(-2 * Math.log(p));
      return (
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
      );
    }
    if (p > 1 - 0.02425) {
      q = Math.sqrt(-2 * Math.log(1 - p));
      return (
        -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
      );
    }
    q = p - 0.5;
    r = q * q;
    return (
      ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
      (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    );
  }

  function ppf(spec, u) {
    const k = spec[0];
    if (k === "uni") return spec[1] + (spec[2] - spec[1]) * u;
    if (k === "norm") return spec[1] + spec[2] * normInv(u);
    if (k === "tri") {
      const [lo, mode, hi] = spec.slice(1),
        c = (mode - lo) / (hi - lo);
      return u < c
        ? lo + Math.sqrt(u * (hi - lo) * (mode - lo))
        : hi - Math.sqrt((1 - u) * (hi - lo) * (hi - mode));
    }
    throw new Error("unknown distribution " + k);
  }

  function cholesky(R) {
    const n = R.length,
      L = Array.from({ length: n }, () => Array(n).fill(0));
    for (let i = 0; i < n; i++)
      for (let j = 0; j <= i; j++) {
        let s = R[i][j];
        for (let k = 0; k < j; k++) s -= L[i][k] * L[j][k];
        if (i === j) {
          if (s <= 1e-10) return null;
          L[i][i] = Math.sqrt(s);
        } else L[i][j] = s / L[j][j];
      }
    return L;
  }

  // A correlation matrix that is not valid is pulled toward the identity until it is (Python clips eigenvalues instead;
  // the two agree whenever the matrix is already valid, as it is for the SNDK assumptions).
  function copulaFactor(names, corr) {
    const idx = Object.fromEntries(names.map((k, i) => [k, i])),
      n = names.length;
    const R = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));
    for (const [a, b, r] of corr) R[idx[a]][idx[b]] = R[idx[b]][idx[a]] = r;
    for (let lam = 0; lam <= 1; lam += 0.02) {
      const M = R.map((row, i) => row.map((v, j) => (i === j ? 1 : (1 - lam) * v)));
      const L = cholesky(M);
      if (L) return L;
    }
    return cholesky(R.map((row, i) => row.map((_, j) => (i === j ? 1 : 0))));
  }

  function draw(dists, corr, size, seed) {
    const names = Object.keys(dists),
      L = copulaFactor(names, corr),
      z = rng(seed ?? 1),
      out = Object.fromEntries(names.map((k) => [k, new Array(size)]));
    const e = new Array(names.length);
    for (let s = 0; s < size; s++) {
      for (let i = 0; i < names.length; i++) e[i] = z();
      for (let i = 0; i < names.length; i++) {
        let v = 0;
        for (let j = 0; j <= i; j++) v += L[i][j] * e[j];
        out[names[i]][s] = ppf(dists[names[i]], phi(v));
      }
    }
    return out;
  }

  const quantile = (sorted, p) => {
    const h = (sorted.length - 1) * (p / 100),
      lo = Math.floor(h),
      hi = Math.ceil(h);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (h - lo);
  };

  // Scale sorted values about their median in log space so the spread becomes sqrt(own^2 + err^2); the same correction
  // as widen() in cycle_dcf.py. Returns [values, k].
  const Z90 = 1.2815515655446004;
  function widen(sorted, err) {
    const med = quantile(sorted, 50),
      pos = sorted.filter((v) => v > 0);
    if (!(med > 0) || !err || pos.length < 20) return [sorted, 1];
    const s = Math.log(quantile(pos, 90) / quantile(pos, 10)) / (2 * Z90);
    if (!(s > 1e-9)) return [sorted, 1];
    const k = Math.sqrt(1 + (err / s) ** 2);
    return [sorted.map((v) => (v > 0 ? med * (v / med) ** k : v)), k];
  }

  function monteCarlo(inp, size, seed) {
    const cy = inp.cycle,
      s = draw(cy.dists, cy.corr, size, seed);
    const per = [],
      eq = [],
      tvs = [];
    let dropped = 0;
    for (let i = 0; i < size; i++) {
      const x = {};
      for (const k of Object.keys(s)) x[k] = s[k][i];
      try {
        const v = valueAt(x, inp);
        per.push(v.per_share);
        eq.push(v.equity);
        tvs.push(v.tv_share);
      } catch (e) {
        dropped++;
      }
    }
    const [sorted, wk] = widen(
        per.slice().sort((a, b) => a - b),
        cy.model_error || 0,
      ),
      price = inp.market.price;
    return {
      widen: wk,
      n: per.length,
      dropped,
      values: sorted,
      median: quantile(sorted, 50),
      mean: sum(sorted) / sorted.length,
      percentiles: [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((p) => ({
        pct: p,
        per_share: quantile(sorted, p),
      })),
      probAbove: sorted.filter((v) => v > price).length / sorted.length,
      tv_share: sum(tvs) / tvs.length,
    };
  }

  const api = {
    widen,
    glide,
    knotPath,
    growthPath,
    value,
    buildSpec,
    valueAt,
    impliedMidMargin,
    draw,
    monteCarlo,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VCycle = api;
})(this);
