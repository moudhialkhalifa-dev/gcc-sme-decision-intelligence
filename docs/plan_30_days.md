# Thirty-day plan

A solo-developer plan with dependencies and acceptance criteria. Status shows what the first
build session completed. "Done" means implemented and checked as described; "Open" needs you.

## Week 1: data foundation

| Day | Work | Depends on | Acceptance criterion | Status |
|---|---|---|---|---|
| 1 | Scope, MVP boundary, single-company decision | - | Scope and deferred list written in README | Done |
| 2 | Choose and obtain dataset; record licence and provenance | 1 | Row count matches the source; `docs/dataset.md` complete | Done |
| 3 | Repository, canonical schema, column mapping | 1 | Mapping suggestions correct for case-study headers (test) | Done |
| 4 | Validation and cleaning with counted steps | 3 | Removed + kept rows = raw rows (test) | Done |
| 5 | Daily table with day status (closed, zero, gap, partial) | 4 | Saturdays closed, holidays zero, Christmas gap on case study; test on synthetic data | Done |
| 6 | KPI module and period comparison | 4, 5 | Hand-computed KPI test passes; no growth when previous period predates data | Done |
| 7 | Capability registry; exploratory checks of the case study | 4 to 6 | Unsupported modules return a reason | Done |

## Week 2: models

| Day | Work | Depends on | Acceptance criterion | Status |
|---|---|---|---|---|
| 8 | Forecast features and leakage test | 5 | Changing data after an origin leaves features unchanged (test) | Done |
| 9 | Seasonal naive, SARIMA, XGBoost; split plan | 8 | All validation targets precede the test window (test) | Done |
| 10 | Rolling-origin experiment, grids, saved results | 9 | `results/forecast_experiments.json` reproducible from one command | Done |
| 11 | Intervals, coverage check, horizon release rule | 10 | Coverage reported; failing horizon disabled with a reason | Done |
| 12 | RFM, rule segments, out-of-time check | 6 | Hand-computed RFM test passes; repurchase table produced | Done |
| 13 | K-Means comparison with stability | 12 | k chosen by the written rule; diagnostics saved | Done |
| 14 | Anomaly detector, Isolation Forest, injected-shock evaluation | 5 | `results/anomaly_eval.json`; recall reported by shock size | Done |

## Week 3: product

| Day | Work | Depends on | Acceptance criterion | Status |
|---|---|---|---|---|
| 15 | Diagnostics (Shapley decomposition, dimensions) | 6 | Reconciles to total within 1e-6 (test) | Done |
| 16 | Recommendation rules | 10 to 15 | Evidence equals inputs; nothing produced without a trigger (test) | Done |
| 17 | Database models, pipeline job, saved outputs | 4 to 16 | Case study processed through the API on PostgreSQL | Done |
| 18 | API endpoints, input validation, access key, limits | 17 | API journey test and rejection tests pass | Done |
| 19 | Frontend shell, data and quality page | 18 | Upload, map, process, quality report in browser | Done |
| 20 | Overview, forecasting, customers pages | 18 | Pages show saved backend outputs only | Done |
| 21 | Anomaly, diagnostics, recommendations pages; states | 18 | Browser test of the whole journey passes | Done |

## Week 4: release

| Day | Work | Depends on | Acceptance criterion | Status |
|---|---|---|---|---|
| 22 | Dockerfile, Compose, environment documentation | 17 to 21 | `docker compose config` valid | Done (image not yet built) |
| 23 | Build and run the image locally | 22, Docker | `/api/health` returns 200 from the container | Open |
| 24 | AWS stack: create, check health, load case study | 23, AWS account | Dashboard reachable from your IP; budget alert email confirmed | Open |
| 25 | TLS and domain (only if the link will be shared) | 24 | HTTPS URL works; API key no longer sent in clear | Open |
| 26 | Fixes from deployment; re-run all tests | 24 | 26 tests and browser test pass against the deployed build | Open |
| 27 | Technical report | 10 to 16 | Every number traceable to `results/` | Done |
| 28 | README, dataset and deployment docs, screenshots | 21, 27 | A new reader can run the project from the README | Done |
| 29 | Demonstration walkthrough (5-minute screen recording) | 24 | Recording covers upload to recommendation | Open |
| 30 | CV bullets and portfolio page; add live URL once verified | 24, 29 | Bullets match completed, verified work | Draft done; URL open |

## After the MVP (not scheduled)

A second case study from another industry; a GCC holiday calendar; a forecast combination
evaluated on fresh data; supervised churn; multi-company accounts.
