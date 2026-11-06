# Stock Valuation Engine, with Dell and Sandisk Worked Through

A valuation engine for your own stocks. Type tickers, get a fair value for each, then open any one to change the assumptions and watch the value move. Discounted cash flow, Monte Carlo and comparables run together, and every input is tagged with where it came from. The engine also produced two interactive models, for Dell Technologies and Sandisk, published below.

**Open the Dell and Sandisk models:** https://speedkelvyn.github.io/Dell-and-Sandisk-plus-valuation-engine/

> Educational research, not investment advice. Every value here is a model output built on public data, can be wrong, and is not a recommendation to buy or sell any security.

## Use the app

Windows: double-click `run_app.bat`. Mac or Linux: double-click `run_app.command` (or run `./run_app.command`). The first run sets up its own environment, which takes about a minute, and then opens the app in your browser. Python 3.10 or newer is required. Manual route:

```bash
pip install -r requirements.txt
streamlit run app.py
```

| Tab | What it does |
|---|---|
| Watchlist | Paste tickers, press Value all. A table ranks them by upside to fair value with the call, confidence, Bear/Base/Bull range and earnings-quality grade. Click a row for the full valuation. |
| Portfolio | Enter ticker, shares and (optionally) cost per share. Totals market value, model fair value, upside and gain or loss across the positions. |
| Value one stock | Value ranges (football field), cash flows and cases, Monte Carlo and comparables, what the price implies (reverse DCF), macro and sentiment, earnings-quality checks, and Excel and HTML downloads. The Change the assumptions panel moves beta, equity risk premium, risk-free rate, terminal growth, exit multiple, growth, margins, capex (or sales-to-capital for a build-out), and the AI-spending and China shocks, and re-values on the spot. |
| Macro and market | Live rates, yield curve, credit spreads, VIX, jobs, inflation, the dollar and oil from Yahoo Finance and FRED; a 12-month recession probability and what it does to valuations. |
| How it works | The method in one page. |

Pick Try the examples on the Watchlist tab to see everything without internet. Live data comes from Yahoo Finance through `yfinance` and is saved once a day in `my_data/cache`, so a second run is instant. Lists and holdings are saved in `my_data/` on your computer and excluded from git. A ticker whose data leaves a gap the model needs is listed as Needs inputs with the missing figures named; the engine never fills a gap with zero.

**SEC filings.** For US companies the financial statements can come straight from the SEC (10-K and 10-Q data, free, no key) instead of Yahoo: in the sidebar open "Where the financial statements come from" and enter your name and email (the SEC asks every program to identify itself), or set `SEC_USER_AGENT='Your Name you@example.com'` and run `python engine/value_ticker.py AAPL --sec`. Quarters are rebuilt from year-to-date filings, impairment and restructuring-impairment charges are added back to operating income as Yahoo does (the figure as filed is kept as `OperatingIncomeReported`), and operating leases stay out of debt. On saved filings for Dell, Sandisk and ON the SEC and Yahoo agree to within 1% on every shared line except operating income, where Yahoo makes further adjustments; base-case values move by up to about 5%. The SEC series is also a quarter fresher for some companies (ON's September quarter was filed on 2 November). Foreign companies that file 20-F are not covered and fall back to Yahoo.

To put the app on the web (for example on Streamlit Community Cloud: repository `Speedkelvyn/Dell-and-Sandisk-plus-valuation-engine`, branch `main`, main file `app.py`), nothing else is needed: a shared copy detects itself, keeps no watchlist or holdings on the server, and shows the examples even if Yahoo Finance refuses the server's requests. Run it on your own computer for live data and saved lists.

**Macro.** A 12-month recession probability (the New York Fed yield-curve model, adjusted for the Sahm rule, unemployment, jobless claims, high-yield spreads and financial conditions) sets the Bear/Base/Bull weights of a probability-weighted DCF shown next to the fair value; VIX against its five-year median widens or narrows the Monte Carlo. Two stress overlays, AI data-center spending and Chinese competition, move growth and long-run margins by the company's exposure. None of this changes the fair value itself.

**Sentiment.** Estimate revisions, analyst actions, the price trend, headline tone (Loughran-McDonald word lists when `Loughran-McDonald_MasterDictionary*.csv` is placed in `my_data/`, else a built-in headline list) and short interest are scored from −100 to +100 and shown beside the valuation, with the price levels a recovery would have to clear. Sentiment never enters the fair value.

The same functions are available without the app: `from service import value_ticker, value_many` (in `engine/`).

## Worked examples: Dell and Sandisk

| | Dell Technologies (DELL) | Sandisk (SNDK) |
|---|---|---|
| Valued | 2026-11-05 (09:46 CT) | 2026-11-05 (09:46 CT), before the 8-for-1 split |
| Price | $326.48 | $842.61 |
| Fair value | $278.74 | $1,478.93 |
| Call (±10% band around price) | Overvalued | Undervalued, low confidence |
| Bear to Bull DCF | $151.10 – $428.06 | $715.82 – $2,978.08 |
| WACC / cost of equity | 11.30% / 12.11% | 14.60% / 14.62% |
| Beta used / daily regression | 1.71 / 2.06 | 2.30 / 2.95 |
| Comparables | $249.12 | $939.12 |
| Earnings quality | Grade B | Grade C |
| Previous valuation | 2026-10-16: $311.65 against $473.39, overvalued | 2026-10-20: $1,315.59 against $1,365.38, fairly valued |

Dell's price fell 31% after Intel forecast PC units down 12% in 2026 and 10% in 2027 and chip stocks sold off; it now sits inside the DCF range, with fair value 15% below the price against 34% below in October. The value moved less: the segment model and consensus are unchanged, peer multiples fell with the sector, and the daily beta rose to 2.06. Today's price needs revenue growth about 2 points a year above consensus, or a 10.5% discount rate instead of 11.3%. Results for the third fiscal quarter come on 24 November.

Sandisk's price fell 38% even as a record fiscal first quarter (revenue $11.0 billion, up 378%, and guidance of $12.2–12.6 billion) lifted consensus earnings: Kioxia said NAND price increases are slowing to flat, and memory stocks sold off after the election. The model's value rose; the price now assumes long-run operating margins near 11%, close to the four-year average, against the model's 41%. The call carries low confidence: the Beneish M-score of −0.72 sits in the manipulation zone and the memory cycle makes the Bull–Bear range wide. The shares split 8-for-1 on 20 November; divide per-share figures by 8 from then.

Fair value blends the DCF, the Monte Carlo median, the comparables and the peer regression (the regression is left out when its R² is below 0.30). Valuations are illustrations built on public data, not investment advice.

## How the valuation works

1. **History and earnings quality.** Five years of statements screened with the Beneish M-score, Sloan accruals and cash conversion.
2. **Future cash flows.** Free cash flow to the firm for operating companies, excess returns for banks and insurers, dividends for REITs. Dell is built from segments, with its captive-finance arm valued at net book. Capex and D&A start at the last four quarters' share of sales and fade to the median of the annual years by year 5; a company spending more than half its sales on capex (a build-out) reinvests by growth instead: each year's added revenue divided by sales-to-capital.
3. **Discount rate.** CAPM cost of equity with beta regressed on two years of daily returns against the S&P 500, then blended into WACC.
4. **Discount.** A ten-year explicit forecast plus a Gordon terminal value with return on new invested capital, cross-checked against an exit multiple.
5. **Price.** Enterprise value bridged to equity value and divided by diluted shares.
6. **Verdict.** DCF, Monte Carlo, comparables and regression blended into a call and an expected alpha. A method that gives a value at or below zero, or above 20 times the price, is left out and named; when none is usable the stock is reported as not valued. The Monte Carlo varies long-run margins by the company's own margin history.

Every input carries a tag: sourced (read from a feed), derived (computed from sourced numbers, formula given) or assumption (a modelling choice that can be changed). Missing debt, cash or yield data stops the model rather than defaulting to zero.

## Command line

```bash
pip install -r requirements.txt

python engine/run_valuation.py dell                      # print the full valuation
python engine/run_valuation.py sndk --scenario Bull
python engine/run_valuation.py dell --excel dell.xlsx    # also write the live-formula workbook

python engine/value_ticker.py AAPL --peers MSFT GOOGL META   # any listed company, from Yahoo Finance
python engine/value_ticker.py --snapshot AAPL_yahoo_2026-10-21.json   # rerun on saved data, no network
```

`value_ticker.py` writes a folder next to `dell/` and `sndk/` with the inputs file, the raw Yahoo snapshot, the interactive page and the Excel workbook, and with `--pdf` a four-page client report (verdict, range of value by method, forecast and scenarios, comparables, checks and sources). The app's Save tab offers the same three downloads. It exits with status 2 when the data leaves a gap the model needs; the files are still written, so the gap can be filled by hand and the page rebuilt.

The Python Monte Carlo uses NumPy's random generator rather than the page's, so its median, and with it the blended fair value, can differ from the page by a fraction of a percent (Dell: $278.17 in Python, $278.74 on the page). The DCF, WACC, comparables and regression match exactly.

To rebuild a page from its inputs file:

```bash
python engine/web/build_page.py dell/dell_inputs.json dell/index.html
```

## Layout

| Path | Contents |
|---|---|
| `app.py`, `run_app.bat`, `run_app.command`, `requirements.txt` | The Streamlit app and the launchers that set it up and open it |
| `engine/service.py` | The engine as a plain API for the app: valuing one ticker or many, assumption overrides, macro data and overlays, reverse DCF, sensitivity grids, portfolio totals, exports |
| `engine/macro.py`, `engine/sentiment.py` | The macro backdrop (data, indicators, recession probability, scenario weights, overlays) and the sentiment score; `engine/data/` holds the 5 November 2026 macro readings for offline use |
| `dell/`, `sndk/` | Inputs file, interactive page and Excel workbook for each company, valued on 5 November 2026; Dell's football-field chart; Sandisk's notebook and the Yahoo snapshot it runs on |
| `engine/valuation_v2.py` | The engine: segments, margins, FCFF, WACC, DCF, equity bridge, reverse DCF, sensitivity, Monte Carlo, comparables, verdict |
| `engine/web/` | The same engine in JavaScript (`model.js`), the research module (`research.js`), the page template and its builder |
| `engine/research.py`, `engine/fetch_yahoo.py` | Turn Yahoo Finance feeds into an inputs file with provenance tags |
| `engine/report_pdf.py` | The client report as a PDF, for any valuation, including banks, insurers and REITs (needs reportlab and matplotlib) |
| `engine/edgar.py` | SEC filings (XBRL company facts) turned into the same annual and quarterly series, with quarters rebuilt from year-to-date figures and a cross-check against Yahoo |
| `engine/beta_daily.py` | Daily CAPM beta against the index, saved into an inputs file |
| `engine/excel_model.py` | The workbook with live formulas and a scenario switch |
| `engine/framework.py` | The valuation one step at a time, with every formula shown with its numbers |
| `engine/run_valuation.py`, `engine/value_ticker.py` | Command-line entry points |
| `engine/backtest.py` | Runs the valuation as it would have looked on past quarter-ends (only filings already filed, that day's price and yield, no consensus) and compares it with the next 6 and 12 months. Data and results are in `backtest/`; the report is `backtest/REPORT.md` |
| `engine/tests/` | The Python engine reproduces the published page figures and agrees with the JavaScript engine on both companies; `value_ticker.py` writes every output offline from a saved snapshot; the PDF report builds for a company, a bank and a REIT;  `service.py` moves values the right way for each assumption and isolates a failing ticker; the app runs end to end on the examples; on live snapshots of SNDK, DELL, ON and NBIS (`engine/tests/fixtures/`), plus JPM, TRV and O for the bank, insurer and REIT models, both builders and both engines agree to the last digit, capex fades from today's level, build-outs switch to sales-to-capital, peers are filtered, and values at or below zero never reach the blend; macro and sentiment readings, weights, overlays and caching |

## Tests

```bash
pip install -r requirements.txt pytest
npm install jstat@1.9.6          # used by the JavaScript parity tests
NODE_PATH=$PWD/node_modules pytest -q engine/tests
```

Part of [Kelvin Afful's portfolio](https://speedkelvyn.github.io/Speedy.github.io/).
