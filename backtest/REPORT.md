# Point-in-time backtest

132 large US companies, quarter-end dates from 2020-06-30 to 2025-09-30. On each date the engine saw only SEC filings already filed, the price and the 10-year yield that day, and no analyst consensus (Yahoo only serves today's), then the stock's return over the next 6 and 12 months was compared with the S&P 500.

Valuations attempted 2904, valued 2520, with a full 12-month outcome 2520.
Could not value: TypeError (102); engine declined to value (data-quality gate) (97); Operating input capex_pct is missing (67); Cash is missing (50); Dividend per share is missing; No dividend rate in the feed; (38); filings too thin (21); ZeroDivisionError (6); Debt cannot be negative (3).

## 12-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: -0.015 pooled; -0.005 on average across dates, positive on 9/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 511 | +7.7% | 48% |
| 2 | 497 | -1.0% | 41% |
| 3 | 501 | -1.4% | 44% |
| 4 | 497 | +1.6% | 49% |
| 5 | 514 | -2.5% | 43% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 276 | -0.1% | 49% |
| OVERVALUED | 1102 | +2.8% | 44% |
| UNDERVALUED | 1142 | -0.7% | 45% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 595 | 8% | 40% |
| 20% to 40% | 456 | 30% | 46% |
| 40% to 60% | 426 | 50% | 47% |
| 60% to 80% | 494 | 70% | 47% |
| 80% to 100% | 549 | 90% | 46% |

Brier score 0.332 against 0.247 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 12 months later (dividends reinvested) landed inside it 74% of the time, below it 9% and above it 17% (2520 valuations). The model's band implies a standard deviation of about 60% in log value; the realised gap between the later price and fair value (robust estimate) had 70%, so the band would need to be about 1.2 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 2520 | -0.015 | -0.005 |
| Earnings yield | 2520 | -0.015 | +0.010 |
| Book to price | 2363 | +0.039 | +0.047 |
| 12-1 month momentum | 2520 | +0.008 | -0.063 |

By model: fcff n=2124, IC -0.024; excess_return n=264, IC +0.175; ddm n=132, IC -0.281.

## 6-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: +0.019 pooled; +0.007 on average across dates, positive on 9/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 511 | +1.5% | 48% |
| 2 | 497 | -0.6% | 45% |
| 3 | 501 | +0.3% | 46% |
| 4 | 497 | +0.4% | 49% |
| 5 | 514 | -0.1% | 45% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 276 | +1.4% | 52% |
| OVERVALUED | 1102 | +0.1% | 46% |
| UNDERVALUED | 1142 | +0.2% | 46% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 595 | 8% | 43% |
| 20% to 40% | 456 | 30% | 49% |
| 40% to 60% | 426 | 50% | 47% |
| 60% to 80% | 494 | 70% | 45% |
| 80% to 100% | 549 | 90% | 50% |

Brier score 0.335 against 0.249 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 6 months later (dividends reinvested) landed inside it 75% of the time, below it 9% and above it 16% (2520 valuations). The model's band implies a standard deviation of about 60% in log value; the realised gap between the later price and fair value (robust estimate) had 70%, so the band would need to be about 1.2 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 2520 | +0.019 | +0.007 |
| Earnings yield | 2520 | +0.014 | +0.011 |
| Book to price | 2363 | +0.042 | +0.040 |
| 12-1 month momentum | 2520 | -0.014 | -0.028 |

By model: fcff n=2124, IC +0.010; excess_return n=264, IC +0.192; ddm n=132, IC -0.166.

## Read this before relying on it

* Each valuation's Monte Carlo band is widened by a model-error term of 0.5 added in quadrature to its own log spread (MC_MODEL_ERR). The size was chosen on 29 companies (before it the band held the later price 49% of the time). Coverage of the 80% band at 12 months: 80% on those 29 companies (587 valuations) and 73% on the 103 others (1933), which the choice never saw.
* P(value > price) is still not a skilled forecast of beating the market (its Brier score is above the base rate's): the band is now honest about the width of outcomes, not about their direction.
* Quarterly dates with 12-month windows overlap, so the independent sample is a fraction of the row count.
* The universe is large, liquid US companies that exist today, so it carries survivorship bias and covers one market regime.
* No consensus, peer comparables or news were available point-in-time. The live tool uses them, so this tests the model on trailing facts alone.
* Returns include dividends; the S&P 500 benchmark is the price index, which understates the benchmark by its dividend yield.
* Industry class (bank, insurer, REIT) is fixed by hand, not point-in-time.

Valuations are illustrations built on public data, not investment advice.