# Value, profitability and momentum on a survivorship-free sample

Script: `engine/factor_test.py`. Data: CRSP monthly returns merged with Compustat annual fundamentals (licensed through WRDS, so not in this repository). Signals at month t predict the return in month t+1, February 2002 to November 2024 (274 months).

## Why this test
The wide backtest (`WIDE_FINDINGS.md`) used today's index members, so failed and dropped firms were missing. This sample keeps delisted firms and uses point-in-time S&P 500 membership. It also has monthly returns for every firm, so the test has about 270 independent months where the valuation backtest had 25 dates.

## Design (fixed before the results)
- Fundamentals used 6 months after the fiscal year end, never older than 18 months. Financials excluded. Price at least $5.
- Universes: the 1,000 largest firms by market value each month (293,000 firm-months), and S&P 500 members at that month (114,000).
- Signals: book-to-market, earnings yield (income before extraordinary items over market value), operating profitability (operating income after depreciation over assets), momentum (months t-11 to t-1). Value is the mean percentile rank of the first two. The composite is the mean percentile rank of value, profitability and momentum.
- Scores: monthly rank correlation with next-month return (IC) with a Newey-West t-statistic, top minus bottom quintile return, and the four-factor (market, size, value, momentum) alpha of that spread.
- Checks run: shuffling next-month returns within each month gives mean IC t of 0.02 over 20 shuffles (sd 0.86); momentum matches a direct calculation; no fundamental is used earlier than 6 months after its fiscal year end. A first run that started in 2001 was thrown out because the extract covered only 100 to 300 of the 1,000 names before February 2002.

## Results, top 1,000 by size, equal-weighted quintiles
| signal | IC | t | months IC>0 | Q5-Q1 %/mo | t | 4-factor alpha %/mo | t |
|---|---|---|---|---|---|---|---|
| book-to-market | -0.005 | -0.5 | 47% | -0.06 | -0.2 | -0.09 | -0.4 |
| earnings yield | 0.014 | 1.9 | 51% | 0.19 | 0.9 | 0.22 | 1.0 |
| value (both) | 0.007 | 0.7 | 48% | 0.12 | 0.4 | 0.11 | 0.4 |
| profitability | 0.018 | 2.9 | 59% | 0.23 | 1.2 | 0.28 | 1.5 |
| momentum | 0.009 | 0.9 | 55% | 0.25 | 0.8 | 0.25 | 0.8 |
| composite | 0.017 | 2.2 | 53% | 0.28 | 1.2 | 0.28 | 1.3 |

S&P 500 members: every IC t-statistic is at most 1.3 and every four-factor alpha t-statistic is at most 1.1 in absolute value. The composite earns -0.03%/month (t -0.1).

## By period (top 1,000, equal-weighted)
| signal | IC 2002-02..2012-12 (t) | IC 2013-01..2024-11 (t) |
|---|---|---|
| value | 0.019 (1.8) | -0.004 (-0.3) |
| profitability | 0.009 (1.0) | 0.026 (3.2) |
| momentum | 0.004 (0.2) | 0.014 (1.2) |
| composite | 0.016 (1.4) | 0.017 (1.6) |

Value-weighted quintiles (weighting by size) shrink the composite spread to 0.00%/month; no value-weighted spread in this universe has |t| above 1.0.

## What this says
1. Profitability is the one signal with a stable positive rank correlation: positive in both halves, 59% of months above zero, t 2.9 in the large-cap universe. The return spread behind it is small (0.2 to 0.3% a month, t about 1.3) and does not survive cap-weighting or the S&P 500 restriction.
2. Value worked in 2002 to 2012 (S&P 500 earnings yield spread 0.51%/month, t 2.0) and did nothing after 2013. This matches the published decay of value premia.
3. Momentum has no reliable edge here.
4. The composite adds nothing over profitability alone. Across the six signals, two universes, three windows and two weightings (72 cells), a handful of |t| above 2 is what chance produces.
5. This agrees with the wide valuation backtest: none of these ingredients predicts relative returns on large and mid caps strongly enough to rely on. Fundamental screens are a weak tilt, not a call.

## Limits
- CRSP delisting returns are not in the extract. Firms that disappear for cause lose their last (often large negative) return, which flatters the cheap, low-profit quintiles slightly. 0.7% of top-1,000 firm-months have no next-month return.
- No cash item in the extract, so enterprise value and EBIT/EV were not tested. No gross profit or accruals either.
- No transaction costs. Quintile portfolios here are rebalanced monthly.
- The extract ends with fiscal years through September 2024, so the last signals use year-ends no later than that.
- The data are licensed, so the result files and the extract stay out of the repository; only the code and these summary statistics are published.

## Do noise-driven moves reverse? (`engine/noise_test.py`)
Rumours and news flow move prices away from value. If a month's move was noise it should reverse next month; heavy volume marks news and attention. Signal: minus the month-t return, split by terciles of abnormal volume (month volume over the firm's own mean of the prior 12 months; terciles taken across all firms each month). Same sample, universes and scoring as above.

| universe, volume | IC (t) | Q5-Q1 %/mo (t) | 4-factor alpha %/mo (t) |
|---|---|---|---|
| top 1,000, all | 0.009 (1.3) | 0.15 (0.8) | 0.20 (0.9) |
| top 1,000, low volume | 0.024 (2.5) | 0.53 (2.0) | 0.76 (2.4) |
| top 1,000, high volume | 0.006 (0.7) | 0.01 (0.1) | 0.05 (0.2) |
| S&P 500, all | 0.011 (1.4) | 0.19 (1.1) | 0.25 (1.1) |
| S&P 500, high volume | 0.000 (0.0) | 0.08 (0.3) | 0.11 (0.4) |

- One-month reversal is weak among large and mid caps in this sample, and there is no reversal after high-volume moves. Those moves neither bounce back nor continue.
- The only cell above |t| 2 is low-volume moves in the top 1,000, which reverse: 0.89%/month (t 2.2) through 2012 and 0.20%/month (t 0.6) since. That pattern is the one liquidity-provision explanations predict (a price move on thin volume is largely a temporary pressure), but it is one cell out of 24, it fades after 2012, and low-volume terciles contain few S&P 500 names in many months.
- Reading: noise is real but is not a tradeable signal at this horizon in large caps. Its place in the valuation framework is the width of the range, which already calibrates (77% held against an 80% target).
- Twitter and other social-media text are not tested: no historical feed is available here.
