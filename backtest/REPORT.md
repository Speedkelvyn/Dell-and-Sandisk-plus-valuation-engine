# Point-in-time backtest

132 large US companies, quarter-end dates from 2020-06-30 to 2025-09-30. On each date the engine saw only SEC filings already filed, the price and the 10-year yield that day, and no analyst consensus (Yahoo only serves today's), then the stock's return over the next 6 and 12 months was compared with the S&P 500.

Valuations attempted 2904, valued 2714, with a full 12-month outcome 2714.
Could not value: engine declined to value (data-quality gate) (77); Operating input capex_pct is missing (44); filings too thin (43); Dividend per share is missing; No dividend rate in the feed; (16); Last fiscal-year revenue is missing; Next-year revenue (tota (4); Debt cannot be negative (3); Next-year revenue (total) is missing; Bear growth (total, it (3).

## 12-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: -0.046 pooled; -0.052 on average across dates, positive on 8/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 546 | +12.9% | 52% |
| 2 | 540 | +0.3% | 41% |
| 3 | 536 | -0.2% | 46% |
| 4 | 540 | +0.3% | 49% |
| 5 | 552 | -2.1% | 42% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 312 | +1.2% | 50% |
| NO CALL | 4 | -9.7% | 25% |
| OVERVALUED | 1076 | +5.8% | 46% |
| UNDERVALUED | 1322 | -0.4% | 45% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 482 | 8% | 44% |
| 20% to 40% | 511 | 30% | 45% |
| 40% to 60% | 528 | 50% | 47% |
| 60% to 80% | 538 | 70% | 47% |
| 80% to 100% | 655 | 90% | 46% |

Brier score 0.338 against 0.248 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 12 months later (dividends reinvested) landed inside it 75% of the time, below it 11% and above it 14% (2714 valuations). The model's band implies a standard deviation of about 60% in log value; the realised gap between the later price and fair value (robust estimate) had 66%, so the band would need to be about 1.1 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 2714 | -0.046 | -0.052 |
| Earnings yield | 2714 | -0.033 | -0.009 |
| Book to price | 2537 | +0.038 | +0.045 |
| 12-1 month momentum | 2714 | +0.031 | -0.041 |

By model: fcff n=2234, IC -0.060; excess_return n=326, IC +0.159; ddm n=154, IC -0.213.

## 6-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: -0.017 pooled; -0.039 on average across dates, positive on 9/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 546 | +3.8% | 51% |
| 2 | 540 | +0.8% | 49% |
| 3 | 536 | +0.2% | 47% |
| 4 | 540 | +0.9% | 47% |
| 5 | 552 | -1.1% | 42% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 312 | +0.8% | 53% |
| NO CALL | 4 | +9.1% | 50% |
| OVERVALUED | 1076 | +1.2% | 47% |
| UNDERVALUED | 1322 | +0.7% | 46% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 482 | 8% | 47% |
| 20% to 40% | 511 | 30% | 46% |
| 40% to 60% | 528 | 50% | 49% |
| 60% to 80% | 538 | 70% | 45% |
| 80% to 100% | 655 | 90% | 49% |

Brier score 0.341 against 0.249 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 6 months later (dividends reinvested) landed inside it 76% of the time, below it 11% and above it 12% (2714 valuations). The model's band implies a standard deviation of about 60% in log value; the realised gap between the later price and fair value (robust estimate) had 66%, so the band would need to be about 1.1 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 2714 | -0.017 | -0.039 |
| Earnings yield | 2714 | +0.001 | -0.000 |
| Book to price | 2537 | +0.046 | +0.043 |
| 12-1 month momentum | 2714 | +0.003 | -0.012 |

By model: fcff n=2234, IC -0.029; excess_return n=326, IC +0.164; ddm n=154, IC -0.138.

## Read this before relying on it

* Each valuation's Monte Carlo band is widened by a model-error term of 0.5 added in quadrature to its own log spread (MC_MODEL_ERR). The size was chosen on 29 companies (before it the band held the later price 49% of the time). Coverage of the 80% band at 12 months: 82% on those 29 companies (580 valuations) and 73% on the 103 others (2134), which the choice never saw.
* P(value > price) is still not a skilled forecast of beating the market (its Brier score is above the base rate's): the band is now honest about the width of outcomes, not about their direction.
* Quarterly dates with 12-month windows overlap, so the independent sample is a fraction of the row count.
* The universe is large, liquid US companies that exist today, so it carries survivorship bias and covers one market regime.
* No consensus, peer comparables or news were available point-in-time. The live tool uses them, so this tests the model on trailing facts alone.
* Returns include dividends; the S&P 500 benchmark is the price index, which understates the benchmark by its dividend yield.
* Industry class (bank, insurer, REIT) is fixed by hand, not point-in-time.

Valuations are illustrations built on public data, not investment advice.