# Demonstration walkthrough

Screenshots are in `docs/screenshots/` and were captured from the running application with the
case study loaded (`scripts/screenshots.py`), except `upload_mapping.png`, which shows the
synthetic test file used by the browser test.

1. **Data** (`upload_mapping.png`, `data.png`). Upload a CSV or load the case study. Confirm the
   suggested column mapping, enter the currency, process. The quality report counts every removed
   row, classifies calendar days and lists which modules the file supports.
2. **Overview** (`overview.png`). Last 28 complete days against the previous 28, weekly revenue,
   the 30-day forecast, recent unusual days and the top actions.
3. **Forecasting** (`forecast.png`). Pick 7 or 30 days. The 90-day tab is disabled with its
   reason. The table shows every model on validation and test, including where the chosen model
   was not the best.
4. **Customers** (`customers.png`). RFM segments with their rules, what each segment did in the
   following 90 days, and the K-Means comparison.
5. **Anomaly center** (`anomalies.png`). Switch metric, filter by severity, open "Evidence" for
   an alert to see volume, basket, returns and the locations and products that differed.
6. **Diagnostics** (`diagnostics.png`). Choose two periods. The change is split into volume and
   basket size, then customers, frequency and value, then by product, location, segment or weekday.
7. **Recommendations** (`recommendations.png`). Each action shows its evidence, uncertainty,
   assumptions and what information would sharpen it.
