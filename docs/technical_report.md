# GCC SME Decision Intelligence: technical report

Version 0.1 (MVP). Every number in this report is read from files in `results/`, which are
produced by the scripts in `backend/experiments/`. Section 9 lists the commands.

## 1. Problem and research question

Small and medium-sized companies record every sale but rarely have an analyst to turn those
records into decisions. This project builds one system that takes a standard transaction export
and produces four kinds of output: what happened (KPIs), where a change shows up (diagnostics),
what is likely next (forecasts), and what to consider doing (rule-based recommendations), with
customer segments and anomaly alerts alongside.

**Research question.** Can an integrated machine-learning decision-intelligence system transform
standard SME operational data into useful forecasts, customer intelligence, anomaly detection and
actionable recommendations?

Each module is evaluated separately below. The evidence comes from one public retail dataset from
the United Kingdom. It does not show that the system works for GCC companies, for other
industries, or that acting on its recommendations improves results. Section 8 states what would
be needed to claim any of that.

## 2. Relevant approaches

- **RFM segmentation** is the standard descriptive model of customer value. The dataset used here
  was published with an RFM and k-means study (Chen, Sain and Guo, 2012).
- **Forecast evaluation.** A seasonal naive forecast is the usual baseline, and rolling-origin
  evaluation is the usual way to respect time order (Hyndman and Athanasopoulos, 2021). SARIMA
  follows Box and Jenkins; gradient-boosted trees follow Chen and Guestrin (2016).
- **Explainability.** TreeSHAP gives exact Shapley attributions for tree ensembles (Lundberg and
  Lee, 2017; Lundberg et al., 2020).
- **Anomaly detection.** The modified z-score with a median/MAD scale and a 3.5 cut-off is from
  Iglewicz and Hoaglin (1993). Isolation Forest is from Liu, Ting and Zhou (2008).
- **Decomposition.** Attributing a change in a product of factors with Shapley values (Shapley,
  1953) splits interaction terms symmetrically and leaves no residual.
- **Cluster validation.** Silhouette width (Rousseeuw, 1987) and the adjusted Rand index (Hubert
  and Arabie, 1985).

## 3. Dataset and preprocessing

**Source.** UCI Machine Learning Repository, *Online Retail II* (Chen, 2012;
DOI 10.24432/C5CG6D), licence CC BY 4.0. Details and provenance are in `docs/dataset.md`.
All amounts are pounds sterling and are kept in GBP. Nothing is converted to a GCC currency.

**Canonical schema.** One row is one line item of a transaction. Fields: timestamp (required),
transaction ID, customer ID, product ID and name, quantity, unit price or amount, category,
location, channel. Uploaded files are mapped to this schema by the user; suggestions come from
column names. A capability registry decides which modules a file can support and shows the reason
when one is unavailable.

**Cleaning results on the case study** (`results/` and the Data page):

| Step | Rows | Share of file |
|---|---:|---:|
| Raw rows | 1,067,371 | 100% |
| Exact duplicate rows removed | 34,335 | 3.22% |
| Non-product lines removed (postage, fees, manual adjustments) | 5,819 | 0.55% |
| Zero quantity or zero/negative price removed | 5,963 | 0.56% |
| Kept: sales lines | 1,003,340 | 94.0% |
| Kept: return and cancellation lines | 17,914 | 1.68% |

Gross sales are GBP 19,643,862 and returns GBP 716,426 across 39,516 sale transactions and 5,852
identified customers. 22.8% of rows have no customer ID; those rows carry 13.1% of sales revenue.
The United Kingdom accounts for 85.4% of net revenue.

**Calendar handling.** A date with no rows is ambiguous, so every day receives a status. On this
data: 603 days with sales; 104 closed Saturdays (structural zero); 7 isolated zero-sales days on
trading weekdays, all UK public holidays (kept as genuine zeros); 24 days inside four gaps of
three or more trading days around Christmas and Easter (treated as missing, not as zero demand);
and one incomplete final day (9 December 2011, data stops at 12:50), which is excluded.

**Assumptions that matter.** Removing exact duplicates assumes they are export artefacts; in
point-of-sale data two identical lines on one receipt can be genuine, so the switch is exposed in
the upload form. Day statuses are inferred from the data, not from a business calendar.

## 4. Methods and experimental design

### 4.1 KPIs and diagnostics

Net revenue is sales minus returns. Transactions are distinct sale transaction IDs, never line
counts. Average transaction value is net revenue divided by transactions, so the identity
`revenue = transactions x average value` holds exactly. Customer counts cover identified
customers only. Growth compares a period with the preceding period of equal length and is not
shown when that period starts before the data does.

A change between two periods is attributed with Shapley values to (a) transactions and average
value, and (b) active customers, transactions per customer and value per transaction, plus the
change in revenue with no customer ID. Both reconcile to the total change; a test asserts this to
1e-6. Contributions by product, location, customer segment and weekday are each a separate split
of the same total and are never added together.

### 4.2 Forecasting

- **Target:** daily net revenue in GBP.
- **Models:** seasonal naive (last observed value on the same weekday); SARIMA with weekly
  seasonality fitted on trading days with missing days handled by the state-space model;
  XGBoost.
- **Multi-step strategy.** XGBoost is trained directly on (origin, horizon) pairs. Features are
  either known at the origin (recent level ratios, same-weekday averages, the same day and week
  one year earlier relative to that year's level) or calendar properties of the target day. The
  target is the future value divided by the 28-day level at the origin. No predicted value is fed
  back as a lag, and a test changes all data after an origin and asserts the features do not
  move.
- **Splits.** The last 90 days (10 September to 8 December 2011) are a final test. Before it, 11
  rolling validation origins from 12 January to 11 June 2011, 15 days apart, each forecast 90
  days. Hyper-parameters (12 SARIMA configurations, 18 XGBoost configurations) and the model for
  each horizon are chosen on validation only. In the test window the frozen configuration is
  re-fitted at each origin: 12 origins for 7 days, 3 for 30 days, 1 for 90 days.
- **Metrics.** MAE and RMSE per trading day; WMAPE = sum of absolute errors / sum of actuals
  (null if actuals sum to zero). Closed weekdays and gap days are excluded; holiday zeros are
  included as real outcomes.
- **Intervals.** 80% ranges from empirical quantiles of out-of-sample relative errors by horizon
  bucket, calibrated on validation and checked for coverage on test.
- **Release rule.** A horizon is shown only if it has at least 3 validation origins, validation
  WMAPE of the selected model is at most 40%, and that model's test WMAPE is at most 40%. The
  test window never chooses the model; it can only switch a horizon off.

### 4.3 Customer intelligence

Recency, frequency and monetary value are computed from rows before the cutoff (9 December 2011).
Scores of 1 to 5 come from percentile ranks, with ties sharing a score. Six segments follow
written rules (see the Customers page). K-Means is run on log-transformed, standardised RFM for
k = 2 to 7; k is the highest-silhouette value of at least 3 whose bootstrap stability (mean ARI
between the base clustering and 10 re-fits on resampled customers) is at least 0.85.

An out-of-time check labels customers using data up to 10 September 2011 and then measures what
each segment did in the following 90 days.

### 4.4 Anomaly detection

For each metric (net revenue, transactions, active customers) the expected value is the median of
the previous 8 observed values on the same weekday. The score is the log ratio of actual to
expected divided by 1.4826 x MAD of that ratio over the previous 56 days. Alerts start at
|z| = 3.5. Isolation Forest on the three log ratios is the comparison method. Because the data
has no anomaly labels, the only quantitative evaluation is recall on injected shocks: 30 seeds, 10
randomly chosen normal days per seed, value multiplied by a known factor.

### 4.5 Recommendations

Eight transparent rules read computed outputs and copy the triggering numbers into the
recommendation as evidence, with a priority, the uncertainty, assumptions and missing
information. No language model is involved. A test checks that the evidence equals the inputs and
that no recommendation is produced when no rule fires.

## 5. Results

### 5.1 Forecasting (`results/forecast_experiments.json`)

WMAPE on daily net revenue. "Selected" is the model chosen on validation.

| Horizon | Model | Validation WMAPE | Test WMAPE | Test MAE (GBP) | Test RMSE (GBP) |
|---|---|---:|---:|---:|---:|
| 7 days | Seasonal naive | 43.1% | 33.4% | 15,612 | 21,079 |
| 7 days | SARIMA | 34.3% | **28.3%** | 13,237 | 18,268 |
| 7 days | XGBoost (selected) | **31.7%** | 38.0% | 17,739 | 21,574 |
| 30 days | Seasonal naive | 40.5% | 34.6% | 16,485 | 21,183 |
| 30 days | SARIMA | 31.0% | **31.5%** | 15,000 | 20,905 |
| 30 days | XGBoost (selected) | **29.3%** | 32.5% | 15,492 | 19,278 |
| 90 days | Seasonal naive | 40.8% | 44.0% | 20,955 | 26,268 |
| 90 days | SARIMA (selected) | **31.8%** | 46.2% | 21,994 | 27,437 |
| 90 days | XGBoost | 32.3% | **27.6%** | 13,159 | 18,387 |

Selected configurations: SARIMA(1,0,1)(0,1,1,7) on log revenue; XGBoost with depth 2, learning
rate 0.03 and 150 trees, which is the most regularised corner of the grid.

What the results support:

1. Both fitted models beat the seasonal naive baseline on validation at every horizon, by roughly 9 to 11
   percentage points of WMAPE.
2. **The model chosen on validation was not the best on test at any horizon.** At 7 days the
   selected XGBoost model was the worst of the three on test, behind the baseline. Validation
   differences between SARIMA and XGBoost were small (0.5 to 2.6 points), and the test window
   (the pre-Christmas build-up) is a different regime from the January-to-September validation
   period. The selection is therefore unstable and should not be read as "XGBoost is better".
3. **The 90-day horizon is switched off.** The selected model reached 46.2% WMAPE on test, worse
   than the baseline, and its 80% range covered only 37.7% of test days. XGBoost did much better
   there (27.6%), plausibly because it has last-year features, but that is one test origin and it
   was not the validated choice, so it is reported and not deployed.
4. Interval coverage on test was 83.3% (7 days, n = 72) and 83.1% (30 days, n = 77) against a
   target of 80%.
5. Daily errors near 30% of revenue are large. The series is noisy: a wholesaler with a few very
   large orders. Totals are more predictable than days: the average absolute error of the 30-day
   total was 22.6% for the selected model across 3 test windows.

### 5.2 Explainability

Mean absolute TreeSHAP share for the selected XGBoost model: week of year 34.3%, day of week
23.8%, same day last year 9.3%, average of the last 8 same weekdays 9.2%, 28-day trend 5.5%,
month 5.5%. The model leans mostly on calendar position. With two years of data, "week of year"
has been seen at most twice, so this dependence is a generalisation risk and not evidence of a
stable seasonal effect. Importance describes the model, not what causes sales.

### 5.3 Customer segments (`results/segmentation.json`)

| RFM segment | Customers | Share of historical revenue | Bought again within 90 days* |
|---|---:|---:|---:|
| High-value | 1,246 | 68.8% | 80.1% |
| Loyal | 1,286 | 15.2% | 51.5% |
| New | 368 | 1.3% | 58.5% |
| Occasional | 1,261 | 3.6% | 27.6% |
| At-risk | 778 | 9.4% | 30.5% |
| Dormant | 912 | 1.7% | 12.4% |

*For customers labelled on 10 September 2011 (segment sizes on that date differ from column 2).

The rule-based labels separate later behaviour at the extremes (80% against 12%). At-risk and
Occasional customers returned at similar rates (30.5% and 27.6%), so "At-risk" describes past
value more than likelihood of returning. Of 2,200 customers who were High-value or Loyal on 10
September, 310 (14.1%) were At-risk or Dormant 90 days later.

K-Means: silhouette is highest at k = 2 (0.436). Under the stated rule k = 4 is chosen
(silhouette 0.360, stability ARI 0.927). Agreement between the four clusters and the six RFM
segments is ARI 0.40: related, but not the same grouping. Silhouette values around 0.35 indicate
weak cluster structure; customers lie on a continuum and any segmentation is a convenient cut.
The product uses the RFM rules because each label has a definition a business owner can read.

### 5.4 Anomaly detection (`results/anomaly_eval.json`)

On the unmodified data the statistical detector flags 18 of 555 scored days and Isolation Forest
18; 11 are common (Jaccard 0.44). That is agreement between two unlabelled methods, not accuracy.
The highest-severity alerts are the seven public-holiday zero-sales days.

Recall on injected shocks applied to all three metrics:

| Value multiplied by | 0.3 | 0.5 | 0.7 | 1.5 | 2.0 | 3.0 |
|---|---:|---:|---:|---:|---:|---:|
| Statistical detector | 68% | 16% | 2% | 8% | 30% | 67% |
| Isolation Forest | 34% | 7% | 2% | 6% | 27% | 59% |

When only revenue is shocked and the other metrics are left unchanged, recall is far lower (24%
for a tripling, 23% for a 70% drop with the statistical detector). The detector reliably finds
only very large deviations. This follows from the data: day-to-day variation is so large that a
halving is within the normal range at a 3.5 threshold. Precision on real incidents is unknown.

### 5.5 System checks

26 automated tests pass (cleaning counts, KPI definitions, day statuses, RFM by hand, leakage,
split order, reconciliation, recommendation evidence, API journey, input rejection, access key).
A browser test uploads a synthetic file, maps columns, processes it and opens every page. The
full pipeline on the 1,067,371-row case study took 24 seconds and about 1.05 GB of memory on a
2-core machine.

## 6. Answer to the research question

Partly. On this dataset the system produces consistent KPIs, exact diagnostics, customer segments
whose extremes behave differently afterwards, and 7- and 30-day forecasts that beat a naive
baseline on validation with honest error bands. It also shows clear limits: forecast model
selection did not transfer to the test period, the 90-day forecast failed its release check, and
anomaly detection only catches very large shocks. Whether the recommendations are *useful* to a
business has not been tested at all; that needs users and outcomes, which this project does not
have.

## 7. System architecture

```
CSV upload -> column mapping -> validation and cleaning -> cleaned line items (Parquet)
           -> daily table with day status -> KPIs | forecasting | segments | anomalies
           -> diagnostics -> rule-based recommendations -> FastAPI -> React dashboard
```

- **Backend:** Python, pandas, scikit-learn, statsmodels, XGBoost, FastAPI, SQLAlchemy.
- **Storage:** PostgreSQL holds dataset metadata, job status, the daily metric table and each
  module's saved output (JSON). Cleaned line items are a Parquet file per dataset.
- **Jobs:** cleaning, back-testing, model fitting and clustering run once per dataset in a
  background worker and are saved. Requests read saved results or run cheap filters; nothing is
  retrained on page load.
- **Separation:** experiment scripts (`backend/experiments`) call the same library code as the
  application but search wider grids and write to `results/`.
- **Scope:** single-company demonstration. Datasets are separated by ID; an optional API key
  protects every endpoint. Customer IDs are never returned by the API.
- **Deviation from the preferred stack:** Plotly was not used; Recharts fits the React frontend.
  Line items are not stored in PostgreSQL because the analysis reads them column-wise.

## 8. Limitations and future work

- One dataset, one industry, one country, 2009 to 2011. No GCC data has been tested. GCC
  specifics that the system does not yet handle: Friday/Saturday weekends are inferred from data
  (this works in tests on synthetic data), but Ramadan, Eid and national holidays move each year
  and need a holiday calendar with Hijri dates.
- No holiday, promotion, price or stock information is used anywhere.
- Two years of history give at most two observations of any annual pattern.
- Forecast selection is unstable; a simple average of SARIMA and XGBoost is an obvious next
  experiment, to be evaluated on new data and not on the test window already examined here.
- Anomaly detection is not validated against real incidents.
- Segments are descriptive. There is no churn model and no predicted lifetime value.
- Recommendations have not been evaluated with users or outcomes.
- Single worker process; a restart fails a running job (it is reported as failed, not lost).

Roadmap: holiday calendars; supervised churn with a proper observation and prediction window;
probabilistic CLV; inventory-aware recommendations when stock and lead-time fields exist; a
bounded "Ask Your Business" interface that maps questions to existing endpoints; multi-company
accounts; database migrations with Alembic; a second case study from another industry.

## 9. Reproducing the results

```
python scripts/convert_raw_xlsx.py <online_retail_II.xlsx> data/raw/online_retail_II.csv   # or: gunzip -k data/raw/online_retail_II.csv.gz
python backend/experiments/run_forecast_experiments.py   # -> results/forecast_experiments.json, forecast_backtests.csv
python backend/experiments/run_segmentation.py           # -> results/segmentation.json
python backend/experiments/run_anomaly_eval.py           # -> results/anomaly_eval.json
cd backend && python -m pytest                           # 26 tests
```

Random seeds are fixed. Package versions used are in `backend/requirements.lock`.

## References

- Box, G. E. P. and Jenkins, G. M. (1970). *Time Series Analysis: Forecasting and Control*. Holden-Day.
- Chen, D. (2012). Online Retail II [dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D
- Chen, D., Sain, S. L. and Guo, K. (2012). Data mining for the online retail industry: a case study of RFM model-based customer segmentation using data mining. *Journal of Database Marketing and Customer Strategy Management*, 19(3), 197-208.
- Chen, T. and Guestrin, C. (2016). XGBoost: a scalable tree boosting system. *KDD '16*.
- Hubert, L. and Arabie, P. (1985). Comparing partitions. *Journal of Classification*, 2, 193-218.
- Hyndman, R. J. and Athanasopoulos, G. (2021). *Forecasting: Principles and Practice* (3rd ed.). OTexts.
- Iglewicz, B. and Hoaglin, D. C. (1993). *How to Detect and Handle Outliers*. ASQC Quality Press.
- Liu, F. T., Ting, K. M. and Zhou, Z.-H. (2008). Isolation forest. *ICDM 2008*.
- Lundberg, S. M. and Lee, S.-I. (2017). A unified approach to interpreting model predictions. *NeurIPS 2017*.
- Lundberg, S. M. et al. (2020). From local explanations to global understanding with explainable AI for trees. *Nature Machine Intelligence*, 2, 56-67.
- Rousseeuw, P. J. (1987). Silhouettes: a graphical aid to the interpretation and validation of cluster analysis. *Journal of Computational and Applied Mathematics*, 20, 53-65.
- Shapley, L. S. (1953). A value for n-person games. In *Contributions to the Theory of Games II*. Princeton University Press.
