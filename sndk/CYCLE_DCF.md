# SNDK through a cycle-based DCF

Valued 2026-11-05 at $842.61 (market cap $128.3B on 152.3M diluted shares). Same facts as the main SNDK model (`SNDK_inputs.json`: price, shares, cash $5,136M, leases $178M, rf 4.87%, ERP 4.23%, consensus revenue and EPS); different method. Code: `engine/cycle_dcf.py` (the model) and `engine/value_sndk_cycle.py` (SNDK's assumptions and runner). Chart: `SNDK_cycle_dcf.png`. Full output: `SNDK_cycle_dcf.json`.

## Why a second method

The main model fades one margin from today's level to a long-run level and applies one discount rate throughout. For a memory maker that hides the questions that decide the value: how long the boom lasts, how deep the downturn goes, and what a normal year earns afterwards. Today's EBIT margin is 79.6% (last quarter, $8.8B on $11.0B); SNDK's four-year average is 10.1% and its FY23 and FY24 margins were losses. The cycle model makes each of those an input.

## What it adds

| Feature | Main model | Cycle model |
|---|---|---|
| Revenue growth | four explicit years, then a fade | cycles with a length, start rate and end rate: Street boom, downturn and recovery, mature growth |
| Margin | one straight line to a long-run margin | any number of turning points: peak, trough, mid-cycle |
| Discount rate | one rate for every year | beta glides from 2.3 to a mature level; the discount factor compounds each year's rate |
| Reinvestment | NWC share plus capex | change in revenue ÷ sales-to-capital, with invested capital and ROIC tracked each year |
| Terminal value | NOPAT × (1 − g/RONIC) | same form, with terminal ROIC = terminal WACC + a spread |
| Monte Carlo | variation in growth and margin | one distribution per input (20 of them) tied by a Gaussian copula, plus boom and downturn length |

## Assumptions (Base)

Years one and two are the Street. Everything after is judgement.

| Phase | Years | Revenue | EBIT margin |
|---|---|---|---|
| Boom (consensus) | FY27 to FY28 | $49.6B then $60.6B (+145%, +22%) | 80% then 82% |
| Downturn and recovery | FY29 to FY32 | growth −20% rising to +8%; revenue falls to $42.7B | falls to a 15% trough in FY31 |
| Mature | FY33 to FY40 | growth 9% fading to 3% | rises to a 28% mid-cycle margin by FY35 |

Base year-one NOPAT per share is $229 against consensus EPS of $228; year two is $282 against $287. Sales-to-capital 5.0 falling to 3.0 (NWC-heavy ramp, small capex because the fabs run through the Kioxia joint ventures). Terminal ROIC 3 points above WACC, about 14.6%. Tax 12.2% rising to 18% by FY31.

## Result

| | Value per share | vs $842.61 | Terminal value share of EV |
|---|---|---|---|
| Bear | $423 | −50% | 6% |
| Base | $1,030 | +22% | 21% |
| Bull | $2,963 | +252% | 33% |

Monte Carlo, 20,000 draws: 10th percentile $788, median $1,034, 90th $1,352. 83% of draws are above the price.

What moves the value, by rank correlation: trough margin +0.68, mid-cycle margin +0.53, depth of the first downturn year +0.40, FY28 growth +0.35. Discount-rate inputs matter far less (equity premium −0.22).

Reverse DCF: holding the Base boom and downturn, the price implies a mid-cycle EBIT margin of 15.7%. SNDK's four-year average is 10.1%; Base uses 28%.

Base value by mid-cycle margin: 10% $757, 15% $833, 20% $908, 28% $1,030, 35% $1,136, 45% $1,287.
Base value by extra boom years beyond the Street's two: +1 $1,198, +2 $1,351, +3 $1,490.
Base value by terminal beta: 1.2 $1,116, 1.6 $1,030, 2.3 $932.

## Against the main model

| | Main model | Cycle model |
|---|---|---|
| Base DCF | $1,769 | $1,030 |
| Monte Carlo median | $1,707 | $1,034 |
| Monte Carlo 10th to 90th | $654 to $3,341 | $788 to $1,352 |
| Bear / Bull | $716 / $2,978 | $423 / $2,963 |

The gap in Base is the cycle. The main model keeps a 41% margin for the long run with no downturn; the cycle model puts in a three-year fall and a 28% mid-cycle margin. The two Monte Carlo bands vary different inputs, so their widths are not comparable; the cycle model's Bear and Bull range is wider.

## Read this before relying on it

* The downturn depth, trough margin and mid-cycle margin are my judgement, not data. They are the inputs that decide the answer; edit `SCENARIOS` and `DISTS` in `engine/value_sndk_cycle.py`.
* No point-in-time test of this method has been run. The backtest in `backtest/REPORT.md` covers the main model only, and it found no skill in upside to fair value.
* A margin of 80% has no precedent in memory. Consensus carries it through FY28; the downturn is assumed to start in FY29, and an earlier start is not modelled.
* The notebook it draws on is a teaching example. The tests reproduce its published Chipotle valuation (operating assets $39.96B, equity $37.79B) to check the arithmetic, not to endorse its inputs.
* Opening invested capital is net working capital plus PP&E from the FY26 filing, $9.5B; goodwill and intangibles are not included, which makes the early ROIC figures high.

Valuations are illustrations built on public data, not investment advice.
