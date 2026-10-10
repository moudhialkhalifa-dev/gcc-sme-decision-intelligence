# CV bullets

Each bullet describes work that exists in this repository and was run. Do not add a live URL
until the AWS deployment has been done and checked.

**GCC SME Decision Intelligence** (Python, pandas, scikit-learn, statsmodels, XGBoost, FastAPI, PostgreSQL, React, TypeScript)

- Built an end-to-end analytics platform that turns a transaction CSV into KPIs, forecasts,
  customer segments, anomaly alerts, revenue diagnostics and evidence-backed recommendations,
  with a 7-page React dashboard on a FastAPI and PostgreSQL backend.
- Engineered a validated ingestion pipeline with user column mapping and a capability registry;
  on a 1.07M-row public retail dataset it classifies every calendar day (trading, closed, zero
  sales, data gap, partial) and processes the full file in about 24 seconds.
- Compared seasonal naive, SARIMA and a direct multi-horizon XGBoost model using 11 rolling
  validation origins and an untouched 90-day test window; fitted models cut validation WMAPE from
  about 41% to about 31%, and a release check withdrew the 90-day forecast after it failed on test.
- Reported negative findings: the model selected on validation was not the best on the test
  window at any horizon, and injected-shock tests showed the anomaly detector recovers only
  large deviations (68% recall for a 70% drop, 16% for a 50% drop).
- Implemented exact Shapley-based decomposition of revenue change (volume, basket value,
  customers, frequency) that reconciles to the total, plus RFM segmentation checked out of time
  (80% of High-value customers repurchased within 90 days against 12% of Dormant) and compared
  with K-Means chosen by silhouette and bootstrap stability.
- Wrote 26 automated tests covering data leakage, chronological splits, KPI definitions and
  reconciliation, and a browser test of the full upload-to-dashboard journey; prepared Docker
  Compose and CloudFormation configuration for a single-instance AWS deployment.

Shorter version (two lines):

- Built a full-stack decision-intelligence platform (FastAPI, PostgreSQL, React) that converts
  SME transaction data into forecasts, customer segments, anomaly alerts, exact revenue
  diagnostics and rule-based recommendations; evaluated on 1.07M rows of public retail data.
- Ran leakage-safe rolling-origin experiments across seasonal naive, SARIMA and XGBoost with an
  untouched test window, and documented where the models and detectors fall short.
