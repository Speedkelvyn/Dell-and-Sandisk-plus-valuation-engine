# Point-in-time backtest

29 large US companies, quarter-end dates from 2020-06-30 to 2025-09-30. On each date the engine saw only SEC filings already filed, the price and the 10-year yield that day, and no analyst consensus (Yahoo only serves today's), then the stock's return over the next 6 and 12 months was compared with the S&P 500.

Valuations attempted 638, valued 587, with a full 12-month outcome 587.
Could not value: engine declined to value (data-quality gate) (20); Dividend per share is missing; No dividend rate in the feed; (16); Cash is missing (6); filings too thin (6); Debt cannot be negative (3).

## 12-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: -0.050 pooled; -0.040 on average across dates, positive on 8/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 124 | +4.2% | 52% |
| 2 | 110 | +9.0% | 48% |
| 3 | 113 | -2.5% | 42% |
| 4 | 110 | +4.3% | 59% |
| 5 | 130 | +2.6% | 49% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 76 | +6.9% | 59% |
| OVERVALUED | 237 | +4.6% | 48% |
| UNDERVALUED | 274 | +1.6% | 49% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 97 | 8% | 40% |
| 20% to 40% | 122 | 30% | 53% |
| 40% to 60% | 131 | 51% | 54% |
| 60% to 80% | 145 | 70% | 54% |
| 80% to 100% | 92 | 89% | 42% |

Brier score 0.318 against 0.250 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 12 months later (dividends reinvested) landed inside it 81% of the time, below it 5% and above it 13% (587 valuations). The model's band implies a standard deviation of about 59% in log value; the realised gap between the later price and fair value (robust estimate) had 58%, so the band would need to be about 1.0 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 587 | -0.050 | -0.040 |
| Earnings yield | 587 | -0.059 | -0.055 |
| Book to price | 527 | -0.008 | -0.004 |
| 12-1 month momentum | 587 | +0.015 | -0.052 |

By model: fcff n=455, IC -0.079; excess_return n=88, IC +0.196; ddm n=44, IC -0.077.

## 6-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: -0.028 pooled; -0.032 on average across dates, positive on 10/22 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 124 | +1.3% | 50% |
| 2 | 110 | +3.7% | 54% |
| 3 | 113 | -0.1% | 44% |
| 4 | 110 | +2.6% | 55% |
| 5 | 130 | +0.1% | 48% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 76 | +4.4% | 55% |
| OVERVALUED | 237 | +1.9% | 50% |
| UNDERVALUED | 274 | +0.2% | 49% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 97 | 8% | 43% |
| 20% to 40% | 122 | 30% | 52% |
| 40% to 60% | 131 | 51% | 53% |
| 60% to 80% | 145 | 70% | 52% |
| 80% to 100% | 92 | 89% | 47% |

Brier score 0.319 against 0.250 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 6 months later (dividends reinvested) landed inside it 84% of the time, below it 5% and above it 11% (587 valuations). The model's band implies a standard deviation of about 59% in log value; the realised gap between the later price and fair value (robust estimate) had 56%, so the band would need to be about 0.9 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 587 | -0.028 | -0.032 |
| Earnings yield | 587 | -0.026 | -0.046 |
| Book to price | 527 | +0.023 | +0.009 |
| 12-1 month momentum | 587 | +0.006 | -0.019 |

By model: fcff n=455, IC -0.053; excess_return n=88, IC +0.192; ddm n=44, IC -0.106.

## Read this before relying on it

* The Monte Carlo band is stretched by MC_WIDEN = 2.1, a factor chosen on this same sample (before the stretch the band held the later price 49% of the time), so the coverage shown above is in-sample by construction. Out-of-sample coverage is unknown.
* P(value > price) is still not a skilled forecast of beating the market (its Brier score is above the base rate's): the band is now honest about the width of outcomes, not about their direction.
* Quarterly dates with 12-month windows overlap, so the independent sample is a fraction of the row count.
* The universe is large, liquid US companies that exist today, so it carries survivorship bias and covers one market regime.
* No consensus, peer comparables or news were available point-in-time. The live tool uses them, so this tests the model on trailing facts alone.
* Returns include dividends; the S&P 500 benchmark is the price index, which understates the benchmark by its dividend yield.
* Industry class (bank, insurer, REIT) is fixed by hand, not point-in-time.

Valuations are illustrations built on public data, not investment advice.