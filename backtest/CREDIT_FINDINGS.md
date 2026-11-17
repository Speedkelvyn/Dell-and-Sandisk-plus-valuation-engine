# Credit rating backtest: does a 1-to-9 rating warn before a company fails?

Code: `engine/credit_score.py` (the rating), `engine/credit_backtest.py` (the test), `engine/tests/test_credit_score.py`.
Data: CRSP/Compustat merged monthly file, 2000 to 2024 (licensed, not in this repository). Run:
`python credit_backtest.py CCM_slim.parquet`

## What was tested

The rating scores one company-year from its annual statements and, when there is a price, its stock. It combines eight
components with weights fixed in advance from credit practice: debt to assets, interest coverage, debt to EBITDA,
current ratio, return on assets, retained earnings to assets, size, and the Merton distance to default (30% of the weight).
The score maps to grades 1 (strong) to 9 (weak) on fixed bands. **Nothing is fitted to defaults.**

Each observation is a company-year, dated four months after the fiscal year end. The label is whether the company
fails within 12 (or 24) months. Sample: 71,853 company-years, 8,556 non-financial companies with at least $50 million
of assets, 678 failures. Benchmarks: Altman Z (1968), Altman Z'' (1995), and the distance to default alone.

How a failure is identified: the file has no delisting code, so a failure is a company whose last Compustat ticker
ends in Q (the marker for trading in bankruptcy) and whose price history ends before mid-2024. See Limits.

## Results

| Within 12 months | All years | Train (to 2012) | Test (2013 on) |
|---|---|---|---|
| Rating, AUC | 0.898 | 0.901 | 0.896 |
| Altman Z, AUC | 0.808 | 0.811 | 0.812 |
| Altman Z'', AUC | 0.805 | 0.805 | 0.815 |
| Distance to default alone, AUC | 0.897 | 0.902 | 0.891 |
| Failures / base rate | 583 / 0.81% | 345 / 0.87% | 238 / 0.74% |

Within 24 months: rating 0.867, Altman Z 0.782 (test period 0.867 and 0.793).

**Default rate within 12 months, by grade (all years):** grade 1 to 5: 0.1% to 0.3%; grade 6: 0.6%; grade 7: 2.1%;
grade 8: 4.9%; grade 9: 11.1%. The rate rises with the grade at every step, and the pattern is the same in the test
years (grade 9: 9.3%).

**Flagging.** Grades 7 to 9 are 13% of company-years and contain 81% of the failures one year ahead. Altman Z below 1.81
flags 30% of company-years and catches 76%. Precision is low for every rule because failure is rare: of the
company-years flagged at grades 7 to 9, 5% failed within 12 months (6 times the base rate).

**Lead time.** For the failures that reached grade 7 to 9 (81% of failed companies), the first such statement came a
median 24 months before the exit (quartiles 10 to 59).

**Without a price (a private borrower).** Statements only, no market component: AUC 0.870 (test years 0.867) against
0.805 for Altman Z'' on the same sample. The grade table keeps its shape: grade 9 fails 8.6% within 12 months.

**Checks.** Failure labels shuffled within each year give an AUC of 0.516 (sd 0.012), so the code is not producing the
result by construction. The rating's AUC for leaving the data through a merger or other non-failure exit is 0.551,
so it is not simply picking up "about to disappear". Widening the failure label to include any exit below $1 gives 0.887.

## Well-known failures

Grade by statement date (months before the exit), with the market component:

| Company | Grades before the exit |
|---|---|
| Sears Holdings | 7 (40) → 8 (28) → 9 (16) → 9 (4) |
| Blockbuster | 4 (38) → 6 (26) → 6 (14) → 7 (2) |
| Walter Energy | 4 (38) → 8 (26) → 8 (14) → 8 (2) |
| Bed Bath & Beyond | 4 (46) → 7 (34) → 6 (22) → 8 (10) |
| Nortel | 5 (44) → 5 (32) → 5 (20) → 7 (8) |
| Circuit City | 1 (40) → 1 (28) → 1 (16) → 6 (4) |
| Enron | 5 (8), the only statement in the file |

The misses matter. Circuit City was grade 1 until four months before the exit, and Enron, whose statements overstated
its health, was grade 5. A rating built on reported statements cannot see a failure the statements hide or a collapse
that comes inside one year.

## Limits

- **The failure label is approximate.** The Q marker misses failures whose ticker was reused or changed, and the exit
  date is when the stock left the data, which is usually within a few months of the filing and sometimes years after
  (WAMUQ, Washington Mutual's bankruptcy ticker, has prices in the file until 2016). Lead times are measured to the exit and can be overstated.
- **Failures after the data ends are counted as survivors,** so false alarms among the latest years are overstated.
  Of the companies that reached grade 9, 77% never failed in the data.
- **The market component carries much of the signal.** Distance to default alone scores 0.897; the statements alone 0.870.
  The market has already priced most distress, so the rating is best read as a ranking of where trouble is, not a
  forecast the market has missed.
- **Compustat figures are the latest reported,** and a restated year uses its restated value, so there is mild look-ahead
  in the statements.
- **Nothing is fitted,** but the weights were set by the author, who knew credit practice and the broad literature. The
  train and test years show the pattern is stable, not that the weights are optimal.
- Coverage: non-financial companies of $50 million or more of assets with a price. Banks, small companies and private
  borrowers are not tested.
