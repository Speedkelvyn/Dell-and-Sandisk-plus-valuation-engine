# Point-in-time backtest

722 US companies, quarter-end dates from 2013-06-30 to 2025-09-30. On each date the engine saw only SEC filings already filed, the price and the 10-year yield that day, and no analyst consensus (Yahoo only serves today's), then the stock's return over the next 6 and 12 months was compared with the S&P 500.

Valuations attempted 18050, valued 12218, with a full 12-month outcome 12218.
Could not value: filings too thin (2175); Debt is missing (1375); engine declined to value (data-quality gate) (1087); Last fiscal-year revenue is missing; Next-year revenue (tota (414); Operating input capex_pct is missing (271); Operating input da_pct is missing (140); Debt cannot be negative (81); Last fiscal-year revenue must be above zero; Next-year reven (61); Bear margins (total, item 1) is missing; Bear margins (total (48); Dividend per share is missing; No dividend rate in the feed; (32); Debt is missing; Operating input capex_pct is missing (24); Debt is missing; Bear margins (total, item 1) is missing; Be (20); Next-year revenue (total) is missing; Bear growth (total, it (16); Operating input da_pct is missing; Operating input capex_pct (15); Common book equity is missing; Bear roe (item 1) is missing; (13); Cash is missing (12); Debt is missing; Operating input da_pct is missing (9); Bear margins (total, item 1) is missing; Base margins (total (8); ZeroDivisionError (6); Operating input da_pct is missing; Bear margins (total, item (6); IndexError (3); Debt is missing; Operating input da_pct is missing; Operatin (2); Next-year revenue (total) is missing; Operating input da_pct (2); TypeError (2); Last fiscal-year revenue must be above zero; Debt is missing (2); Next-year revenue (total) is missing; Debt is missing; Bear  (2); Next-year revenue (total) is missing; Debt is missing; Opera (2); WACC (0.98%) must exceed terminal growth by at least 0.5pp (1); WACC (1.19%) must exceed terminal growth by at least 0.5pp (1); Operating input capex_pct is missing; Bear margins (total, i (1); WACC (1.47%) must exceed terminal growth by at least 0.5pp (1).

## 12-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: +0.009 pooled; -0.011 on average across dates, positive on 9/25 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 2448 | +9.3% | 53% |
| 2 | 2440 | +2.9% | 50% |
| 3 | 2437 | +2.4% | 49% |
| 4 | 2440 | +1.5% | 48% |
| 5 | 2453 | +3.7% | 50% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 1350 | +2.8% | 53% |
| NO CALL | 114 | +7.2% | 42% |
| OVERVALUED | 4617 | +5.3% | 49% |
| UNDERVALUED | 6137 | +3.1% | 50% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 1955 | 8% | 48% |
| 20% to 40% | 2101 | 30% | 49% |
| 40% to 60% | 2327 | 50% | 52% |
| 60% to 80% | 2960 | 70% | 49% |
| 80% to 100% | 2875 | 90% | 51% |

Brier score 0.330 against 0.250 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 12 months later (dividends reinvested) landed inside it 77% of the time, below it 10% and above it 13% (12218 valuations). The model's band implies a standard deviation of about 64% in log value; the realised gap between the later price and fair value (robust estimate) had 65%, so the band would need to be about 1.0 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 12218 | +0.009 | -0.011 |
| Earnings yield | 12218 | -0.012 | -0.015 |
| Book to price | 11759 | -0.012 | -0.023 |
| 12-1 month momentum | 12178 | +0.006 | -0.010 |

By model: fcff n=11706, IC +0.009; excess_return n=360, IC +0.105; ddm n=152, IC -0.133.

## 6-month excess return versus the S&P 500

Rank correlation of upside to fair value with the later excess return: +0.017 pooled; -0.010 on average across dates, positive on 10/25 dates. Zero means no information; 0.05 to 0.10 is typical of a useful fundamental signal.

| Upside quintile (1 = least) | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| 1 | 2448 | +3.4% | 52% |
| 2 | 2440 | +1.1% | 51% |
| 3 | 2437 | +1.2% | 50% |
| 4 | 2440 | +0.7% | 49% |
| 5 | 2453 | +1.6% | 50% |

| Verdict | Valuations | Mean excess return | Beat the market |
|---|---|---|---|
| FAIRLY VALUED | 1350 | +1.4% | 52% |
| NO CALL | 114 | +2.1% | 40% |
| OVERVALUED | 4617 | +1.6% | 49% |
| UNDERVALUED | 6137 | +1.7% | 51% |

Monte Carlo P(value > price) against how often the stock beat the market:

| Predicted band | Valuations | Mean predicted | Observed |
|---|---|---|---|
| 0% to 20% | 1955 | 8% | 50% |
| 20% to 40% | 2101 | 30% | 49% |
| 40% to 60% | 2327 | 50% | 51% |
| 60% to 80% | 2960 | 70% | 52% |
| 80% to 100% | 2875 | 90% | 51% |

Brier score 0.331 against 0.250 for always forecasting the base rate (lower is better).

The Monte Carlo 10th to 90th percentile band is meant to hold the outcome 80% of the time. The price 6 months later (dividends reinvested) landed inside it 78% of the time, below it 10% and above it 12% (12218 valuations). The model's band implies a standard deviation of about 64% in log value; the realised gap between the later price and fair value (robust estimate) had 64%, so the band would need to be about 1.0 times wider to be calibrated.

Same test for simple signals computed from the same data:

| Signal | Valuations | Rank correlation, pooled | Average across dates |
|---|---|---|---|
| Upside to fair value (this engine) | 12218 | +0.017 | -0.010 |
| Earnings yield | 12218 | -0.007 | -0.018 |
| Book to price | 11759 | -0.009 | -0.021 |
| 12-1 month momentum | 12178 | -0.031 | -0.019 |

By model: fcff n=11706, IC +0.016; excess_return n=360, IC +0.125; ddm n=152, IC -0.060.

## Read this before relying on it

* Each valuation's Monte Carlo band is widened by a model-error term of 0.5 added in quadrature to its own log spread (MC_MODEL_ERR). The size was chosen on 29 companies (before it the band held the later price 49% of the time). Coverage of the 80% band at 12 months: 84% on those 29 companies (628 valuations) and 77% on the 693 others (11590), which the choice never saw.
* P(value > price) is still not a skilled forecast of beating the market (its Brier score is above the base rate's): the band is now honest about the width of outcomes, not about their direction.
* Quarterly dates with 12-month windows overlap, so the independent sample is a fraction of the row count.
* The universe is large, liquid US companies that exist today, so it carries survivorship bias and covers one market regime.
* No consensus, peer comparables or news were available point-in-time. The live tool uses them, so this tests the model on trailing facts alone.
* Returns include dividends; the S&P 500 benchmark is the price index, which understates the benchmark by its dividend yield.
* Industry class (bank, insurer, REIT) is fixed by hand, not point-in-time.

Valuations are illustrations built on public data, not investment advice.