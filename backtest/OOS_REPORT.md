# Out-of-sample test of model changes

128 large US companies. Dates were split before any result was seen: train as-of 2020-06-30 to 2021-12-31, 2022 dropped so no training outcome overlaps a test date, test as-of 2023-03-31 to 2025-09-30. The outcome is the stock's next 12-month return over the S&P 500; the signal is upside to fair value. As in `REPORT.md` the engine sees only filings already made, with no analyst consensus.

Valuations attempted 11228 (four growth variants per company and date).

## Train dates: used to choose

| Variant | Scored valuations | Dates | Mean rank correlation | Newey-West t | 95% interval | Top minus bottom fifth | t |
|---|---|---|---|---|---|---|---|
| growth weight 1.0 | 762 | 7 | +0.048 | +0.48 | -0.129 to +0.249 | -0.3% | -0.05 |
| growth weight 0.25 | 765 | 7 | +0.050 | +0.51 | -0.132 to +0.249 | +1.3% | +0.23 |
| growth weight 0.5 | 763 | 7 | +0.055 | +0.54 | -0.132 to +0.255 | +2.1% | +0.33 |
| growth weight 0.75 | 763 | 7 | +0.049 | +0.49 | -0.135 to +0.251 | +1.8% | +0.28 |
| fcff | 647 | 7 | +0.010 | +0.08 | -0.202 to +0.253 | -2.5% | -0.30 |
| excess_return | 73 | 0 | not scored: fewer than 12 names on every date | | | | |
| ddm | 42 | 0 | not scored: fewer than 12 names on every date | | | | |

Chosen on the train dates: growth weight 0.5.

The quality filter could not be tested. The earnings-quality grade needs operating cash flow, cost of sales, SG&A, current assets and liabilities and net PP&E, and the stored SEC filings for this run do not carry those lines, so every company grades n/a. Refetching the filings with those tags is the step that would make this test possible.

## Test dates: scored once

| Variant | Scored valuations | Dates | Mean rank correlation | Newey-West t | 95% interval | Top minus bottom fifth | t |
|---|---|---|---|---|---|---|---|
| growth weight 1.0 | 1273 | 11 | -0.037 | -0.77 | -0.122 to +0.096 | -18.4% | -2.05 |
| growth weight 0.25 | 1282 | 11 | -0.061 | -1.55 | -0.134 to +0.045 | -20.6% | -2.18 |
| growth weight 0.5 | 1282 | 11 | -0.055 | -1.27 | -0.133 to +0.061 | -21.2% | -2.29 |
| growth weight 0.75 | 1277 | 11 | -0.046 | -0.97 | -0.130 to +0.080 | -20.2% | -2.27 |
| fcff | 1064 | 11 | -0.035 | -0.82 | -0.116 to +0.081 | -17.5% | -1.69 |
| excess_return | 143 | 11 | +0.144 | +2.48 | +0.086 to +0.264 | +0.3% | +0.08 |
| ddm | 66 | 0 | not scored: fewer than 12 names on every date | | | | |

## Does the chosen change beat the baseline on the test dates?

| Comparison | Dates | Mean difference in rank correlation | Newey-West t | 95% interval |
|---|---|---|---|---|
| Growth weight 0.5 against baseline, same companies | 11 | -0.018 | -1.82 | -0.038 to -0.001 |

## Reading the result

* Baseline on the test dates: rank correlation -0.037 (interval -0.122 to +0.096). The interval includes zero: no detectable skill.
* Chosen change (growth weight 0.5) on the test dates: -0.055 (interval -0.133 to +0.061); difference from baseline -0.018, interval below zero: worse than the baseline out of sample.
* The top fifth by upside minus the bottom fifth returned -18.4% a year on the test dates (Newey-West t -2.05): the stocks the model liked most did worse than the ones it liked least.
* Eleven test dates with overlapping 12-month windows are few independent observations, so intervals are wide. A change that does not clear them is not rejected, only unproven.
* Every variant tried is listed above. The three families were fixed before the run, so there are at most three comparisons against the baseline, not a search over many; the intervals are not adjusted for that.
* Same caveats as `REPORT.md`: survivorship in today's large companies, no point-in-time consensus, one market regime and industry class fixed by hand.

Valuations are illustrations built on public data, not investment advice.