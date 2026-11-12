# Wide-universe backtest: what 12,218 valuations say

Run from the `expand-backtest` workflow: 722 downloaded US companies (the 132 earlier ones plus today's S&P 500 and S&P 400 members, excluding financials and real estate), 25 dates from June 2013 to September 2025 every second quarter end, 12,218 valuations with a 12-month outcome from 615 companies. Full report: `REPORT_wide.md`; every valuation: `records_wide.json`.

## Result

Rank correlation of each signal with the next 12 months' return in excess of the S&P 500, computed date by date. Dates are six months apart, so 12-month windows overlap and about half are independent; the t-statistics use half the number of dates.

| Signal | All dates | 2013 to 2018 | 2019 to 2025 | Dates positive | t-statistic |
|---|---|---|---|---|---|
| DCF upside to fair value | -0.011 | -0.007 | -0.014 | 36% | -0.26 |
| Earnings yield | -0.015 | -0.040 | +0.009 | 52% | -0.47 |
| Book to price | -0.023 | -0.051 | +0.003 | 40% | -0.45 |
| 12-1 month momentum | -0.010 | -0.008 | -0.012 | 56% | -0.19 |

None of the signals, including the engine's, predicts relative returns on this sample. The negative signs seen on the earlier 132 names were noise: with about four times the valuations they disappear. A rank correlation of 0.05 to 0.10 is typical of a useful signal, and the standard error here is about 0.04, so an edge of that size could be present and unseen; an edge above about 0.08 is ruled out.

## What did hold up

* **The range is calibrated.** The Monte Carlo 10th to 90th percentile band contained the price 12 months later 77% of the time (target 80%, below it 10%, above it 13%). The model-error widening added earlier does its job; the realised spread was 65% in log value against 64% in the band.
* **Revenue growth forecasts improved.** Pulling growth toward the 6.5% median when there is no consensus cut the average error from 15 points to 11 on 937 firm-years (`engine/forecast_error.py`).

## What did not

* **The probability and the call carry no information about relative return.** Valuations given 0% to 20% probability of being worth more than the price beat the market 48% of the time; those given 80% to 100% beat it 51% of the time. UNDERVALUED averaged +3.1% excess and OVERVALUED +5.3%. The app now says so beside the call.
* **The cycle method** has no demonstrated skill (Micron test in `sndk/CYCLE_DCF.md`).
* **Bank and REIT models:** +0.105 (360 valuations, few banks) and -0.133 (152, 6 REITs). Too few companies to read.

## Limits of this test

* **Survivorship.** The universe is today's index members, so companies that fell out or failed are missing. That flatters value and momentum in the early years, which makes the null result for them more notable, not less.
* **No analyst consensus** exists for past dates, so every valuation uses the history-only growth rule.
* **Coverage is 68%.** 5,832 of 18,050 attempts could not be valued: filings too thin early in the sample (2,175), missing debt tags (1,375; some are debt-free, some such as AES and DHI have debt filed under tags the engine does not read), the data-quality gate (1,087), missing next-year revenue (414).
* **Large and mid caps only.** Small caps, where published predictors tend to work better, are not covered.
* **One 12-year window** that includes a long growth-stock run.

## What would make a prediction method reliable

The test above says price targets from this engine are not a signal. Methods with published out-of-sample evidence, and the McLean and Pontiff finding that published predictors lose about 58% of their return after publication, point to three directions that can be tested next: a larger and survivorship-free universe (needs point-in-time index membership data), forecasting fundamentals (growth and margins) and trading only on forecast error against consensus, and combining the engine's value with profitability and momentum in one score, tested out of sample.
