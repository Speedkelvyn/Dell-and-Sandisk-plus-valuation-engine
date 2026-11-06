# Out-of-sample test of model changes

129 large US companies. Dates were split before any result was seen: train as-of 2020-06-30 to 2021-12-31, 2022 dropped so no training outcome overlaps a test date, test as-of 2023-03-31 to 2025-09-30. The outcome is the stock's next 12-month return over the S&P 500; the signal is upside to fair value. As in `REPORT.md` the engine sees only filings already made, with no analyst consensus.

Valuations attempted 11316 (four growth variants per company and date).

## Train dates: used to choose

| Variant | Scored valuations | Dates | Mean rank correlation | Newey-West t | 95% interval | Top minus bottom fifth | t |
|---|---|---|---|---|---|---|---|
| growth weight 1.0 | 769 | 7 | +0.048 | +0.49 | -0.128 to +0.249 | -0.1% | -0.01 |
| growth weight 0.25 | 772 | 7 | +0.051 | +0.53 | -0.130 to +0.249 | +1.0% | +0.19 |
| growth weight 0.5 | 770 | 7 | +0.054 | +0.54 | -0.132 to +0.255 | +1.5% | +0.25 |
| growth weight 0.75 | 770 | 7 | +0.049 | +0.49 | -0.134 to +0.251 | +1.8% | +0.28 |
| grades A | 618 | 7 | +0.021 | +0.19 | -0.177 to +0.247 | -3.1% | -0.40 |
| grades A-B | 660 | 7 | +0.018 | +0.15 | -0.198 to +0.266 | -2.4% | -0.29 |
| grades A-C | 678 | 7 | +0.024 | +0.20 | -0.189 to +0.270 | -1.6% | -0.19 |
| fcff | 654 | 7 | +0.010 | +0.08 | -0.200 to +0.253 | -2.4% | -0.30 |
| excess_return | 73 | 0 | not scored: fewer than 12 names on every date | | | | |
| ddm | 42 | 0 | not scored: fewer than 12 names on every date | | | | |

Chosen on the train dates: growth weight 0.5, quality grades A-C.

## Test dates: scored once

| Variant | Scored valuations | Dates | Mean rank correlation | Newey-West t | 95% interval | Top minus bottom fifth | t |
|---|---|---|---|---|---|---|---|
| growth weight 1.0 | 1284 | 11 | -0.041 | -0.82 | -0.128 to +0.095 | -18.2% | -2.06 |
| growth weight 0.25 | 1293 | 11 | -0.065 | -1.61 | -0.141 to +0.043 | -21.0% | -2.23 |
| growth weight 0.5 | 1293 | 11 | -0.059 | -1.33 | -0.138 to +0.059 | -20.4% | -2.20 |
| growth weight 0.75 | 1288 | 11 | -0.050 | -1.02 | -0.136 to +0.079 | -19.6% | -2.21 |
| grades A | 1084 | 11 | -0.056 | -1.33 | -0.140 to +0.060 | -18.8% | -1.94 |
| grades A-B | 1111 | 11 | -0.058 | -1.33 | -0.141 to +0.060 | -19.0% | -1.87 |
| grades A-C | 1136 | 11 | -0.061 | -1.42 | -0.143 to +0.057 | -18.7% | -1.97 |
| fcff | 1075 | 11 | -0.040 | -0.90 | -0.125 to +0.079 | -18.4% | -1.76 |
| excess_return | 143 | 11 | +0.144 | +2.48 | +0.086 to +0.264 | +0.3% | +0.08 |
| ddm | 66 | 0 | not scored: fewer than 12 names on every date | | | | |
| growth weight 0.5 with grades A-C | 1145 | 11 | -0.081 | -2.35 | -0.154 to +0.015 | -23.0% | -2.36 |

## Does the chosen change beat the baseline on the test dates?

| Comparison | Dates | Mean difference in rank correlation | Newey-West t | 95% interval |
|---|---|---|---|---|
| Growth weight 0.5 against baseline, same companies | 11 | -0.018 | -1.87 | -0.038 to -0.002 |
| Grades A-C only, against all companies | 11 | -0.020 | -1.83 | -0.044 to -0.007 |
| Both changes together against baseline | 11 | -0.040 | -2.26 | -0.082 to -0.019 |

## Reading the result

* Baseline on the test dates: rank correlation -0.041 (interval -0.128 to +0.095). The interval includes zero: no detectable skill.
* Chosen change (growth weight 0.5 with grades A-C) on the test dates: -0.081 (interval -0.154 to +0.015); difference from baseline -0.040, interval below zero: worse than the baseline out of sample.
* The top fifth by upside minus the bottom fifth returned -18.2% a year on the test dates (Newey-West t -2.06): the stocks the model liked most did worse than the ones it liked least.
* Eleven test dates with overlapping 12-month windows are few independent observations, so intervals are wide. A change that does not clear them is not rejected, only unproven.
* Every variant tried is listed above. The three families were fixed before the run, so there are at most three comparisons against the baseline, not a search over many; the intervals are not adjusted for that.
* Same caveats as `REPORT.md`: survivorship in today's large companies, no point-in-time consensus, one market regime and industry class fixed by hand.

Valuations are illustrations built on public data, not investment advice.