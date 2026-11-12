# Event study: results against our forecast

Script: `engine/event_study.py`. Universe: the 722 names in `backtest/data_wide` (today's S&P 500 and S&P 400 members excluding financials and real estate, plus the 132 original names). Events from 2012.

**Status: first pass, dated by the 10-Q/10-K filing day.** The earnings release usually comes first, so these dates run late (see the pre-event row). Run the workflow `fetch-filing-dates.yml`, then `python engine/event_study.py backtest/data_wide` to redo it with the 8-K Item 2.02 release date and time.

## Method (fixed before the results)
For each event, the forecast rule from `research.py` is applied to the quarter being reported, using only earlier quarters:
- revenue forecast = same quarter a year earlier x (1 + 6.5% + 0.25 x (last quarter's year-on-year growth - 6.5%))
- operating income forecast = revenue forecast x the same quarter's margin a year earlier
- surprise = reported against forecast (revenue as a ratio; operating income in points of revenue)

Reaction = abnormal return (stock minus S&P 500, total-return closes) on day 0 and day 1. Drift = day 2 to day 60. Scored by calendar month: rank correlation of surprise with the return (mean, Newey-West t over months) and the top third minus the bottom third. "Beat" means reported above forecast.

## Results (29,101 events, 718 firms, 143 to 151 months)
| surprise | window | IC (t) | top - bottom third | beat / miss |
|---|---|---|---|---|
| revenue | pre -5..-2 | 0.037 (3.1) | 0.32% | +0.14% / -0.06% |
| revenue | reaction 0..1 | 0.068 (6.9) | 0.71% (t 5.9) | +0.44% / -0.10% |
| revenue | drift 2..60 | 0.032 (1.9) | 0.90% (t 1.5) | -0.07% / -0.57% |
| operating income | pre -5..-2 | 0.052 (5.7) | 0.57% | +0.17% / -0.11% |
| operating income | reaction 0..1 | 0.103 (10.8) | 1.12% (t 8.4) | +0.59% / -0.32% |
| operating income | drift 2..60 | 0.000 (0.0) | 0.15% (t 0.3) | -0.18% / -0.43% |

Placebo (surprises shuffled within each month): every IC t-statistic is between -2.0 and 1.0.

Drift on revenue surprise, by period: 2012-2018 spread 1.40% (t 2.0); 2019-2025 spread 0.39% (t 0.4). Operating-income drift is zero in both.

## What this says
1. The market reacts in the direction of our surprise. Beating the mechanical forecast on operating income earned +0.59% over two days against -0.32% for a miss. The forecast rule is a usable stand-in for what moves prices on results day, even though it is plain extrapolation.
2. Little of the move continues. There is no drift after operating-income surprises, and the revenue drift that existed through 2018 is not distinguishable from zero since.
3. The pre-event row is not zero. A filing-day date is later than the release for many firms, so the reaction here is understated and the drift window starts too late. This is the reason for the 8-K rerun; the conclusions on drift should be treated as provisional until it is done.
4. A reaction is not an edge. It measures how the market prices news after the fact. Using the forecast before the release would need a forecast better than consensus, which this test cannot assess (no consensus data).

## Limits
- Mechanical forecast, not analyst consensus. The market's surprise is against its own expectation.
- Today's index members only, so survivorship flatters levels (it matters little for a two-day reaction).
- Reported figures are the latest filed; a restated quarter uses its restated value.
- Not covered: 8-K events other than earnings, and the Sandisk contract announcement (no event-date record is stored for it).
