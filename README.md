# Dell and Sandisk Valuations, and the Engine Behind Them

Two interactive discounted-cash-flow models, for Dell Technologies and Sandisk, and the valuation engine that produced them. One set of formulas runs in three places: the browser, Python and an Excel workbook with live formulas. Tests hold the three to the same numbers.

**Open the models:** https://speedkelvyn.github.io/Dell-and-Sandisk-plus-valuation-engine/

## Results

| | Dell Technologies (DELL) | Sandisk (SNDK) |
|---|---|---|
| Valued | 2026-10-16 | 2026-10-20 |
| Price | $473.39 | $1,365.38 |
| Fair value | $311.65 | $1,315.59 |
| Call (±10% band around price) | Overvalued | Fairly valued, low confidence |
| Bear to Bull DCF | $145.59 – $411.88 | $627.47 – $2,367.26 |
| WACC / cost of equity | 11.58% / 12.14% | 14.58% / 14.59% |
| Beta used / daily regression | 1.67 / 1.99 | 2.24 / 2.85 |
| Comparables | $369.41 | $1,153.35 |
| Earnings quality | Grade B | Grade C |

Dell's price sits above every intrinsic estimate, including the Bull case; a daily-return beta near 2 lifts the cost of equity to 12.1%. Sandisk's price falls inside the fair-value band, but the Beneish M-score of −0.71 sits in the manipulation zone and NAND margin swings widen the Bull–Bear range, so the call carries low confidence.

Fair value blends the DCF, the Monte Carlo median, the comparables and the peer regression (the regression is left out when its R² is below 0.30). Valuations are illustrations built on public data, not investment advice.

## How the valuation works

1. **History and earnings quality.** Five years of statements screened with the Beneish M-score, Sloan accruals and cash conversion.
2. **Future cash flows.** Free cash flow to the firm for operating companies, excess returns for banks and insurers, dividends for REITs. Dell is built from segments, with its captive-finance arm valued at net book.
3. **Discount rate.** CAPM cost of equity with beta regressed on two years of daily returns against the S&P 500, then blended into WACC.
4. **Discount.** A ten-year explicit forecast plus a Gordon terminal value with return on new invested capital, cross-checked against an exit multiple.
5. **Price.** Enterprise value bridged to equity value and divided by diluted shares.
6. **Verdict.** DCF, Monte Carlo, comparables and regression blended into a call and an expected alpha.

Every input carries a tag: sourced (read from a feed), derived (computed from sourced numbers, formula given) or assumption (a modelling choice that can be changed). Missing debt, cash or yield data stops the model rather than defaulting to zero.

## Run it

```bash
pip install -r engine/requirements.txt

python engine/run_valuation.py dell                      # print the full valuation
python engine/run_valuation.py sndk --scenario Bull
python engine/run_valuation.py dell --excel dell.xlsx    # also write the live-formula workbook

python engine/value_ticker.py AAPL --peers MSFT GOOGL META   # any listed company, from Yahoo Finance
python engine/value_ticker.py --snapshot AAPL_yahoo_2026-10-21.json   # rerun on saved data, no network
```

`value_ticker.py` writes a folder next to `dell/` and `sndk/` with the inputs file, the raw Yahoo snapshot, the interactive page and the Excel workbook. It exits with status 2 when the data leaves a gap the model needs; the files are still written, so the gap can be filled by hand and the page rebuilt.

The Python Monte Carlo uses NumPy's random generator rather than the page's, so its median, and with it the blended fair value, can differ from the page by a fraction of a percent (Dell: $311.10 in Python, $311.65 on the page). The DCF, WACC, comparables and regression match exactly.

To rebuild a page from its inputs file:

```bash
python engine/web/build_page.py dell/dell_inputs.json dell/index.html
```

## Layout

| Path | Contents |
|---|---|
| `dell/`, `sndk/` | Inputs file, interactive page and Excel workbook for each company; Dell's football-field chart; Sandisk's notebook |
| `engine/valuation_v2.py` | The engine: segments, margins, FCFF, WACC, DCF, equity bridge, reverse DCF, sensitivity, Monte Carlo, comparables, verdict |
| `engine/web/` | The same engine in JavaScript (`model.js`), the research module (`research.js`), the page template and its builder |
| `engine/research.py`, `engine/fetch_yahoo.py` | Turn Yahoo Finance feeds into an inputs file with provenance tags |
| `engine/beta_daily.py` | Daily CAPM beta against the index, saved into an inputs file |
| `engine/excel_model.py` | The workbook with live formulas and a scenario switch |
| `engine/framework.py` | The valuation one step at a time, with every formula shown with its numbers |
| `engine/run_valuation.py`, `engine/value_ticker.py` | Command-line entry points |
| `engine/tests/` | The Python engine reproduces the published page figures, agrees with the JavaScript engine on both companies, and `value_ticker.py` writes every output offline from a saved snapshot |

## Tests

```bash
npm install jstat@1.9.6          # used by the JavaScript parity tests
NODE_PATH=$PWD/node_modules pytest -q engine/tests
```

Part of [Kelvin Afful's portfolio](https://speedkelvyn.github.io/Speedy.github.io/).
