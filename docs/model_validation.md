# Temporal model-validation report

## Status and scope

This is a reproducible sampled validation of the empirical arrival-delay and connection-probability models. It is not evidence of production readiness. The checked-in code supports larger replay runs, while the reported run uses 50 deterministic cases per holdout to keep local development runtime practical.

- Dataset: official BTS Reporting Carrier On-Time Performance, 2023–2025
- Completed-flight modeling rows: 20,588,134
- Validation seed: `20260810`
- Holdout cases: 50 completed flights per temporal split for delay validation
- Connection cases: 50 records per split sampled from all statuses
- Transfer-time simulations per connection case: 2,000
- Report command: `python -m backend.flight_connection.validation --mode sampled --cases-per-split 50`

The machine-readable report is generated as `data/processed/validation_report.json` and is excluded from Git with the other generated data artifacts.

## Leakage prevention

Every validation cohort query contains the SQL predicate `flight_date < history_end`. The upper bound is exclusive. A caller-supplied `history_end` later than the prediction date raises an error rather than being silently truncated. Rolling windows add a lower inclusive bound. Unit tests insert an extreme future delay and verify that it cannot enter the earlier prediction distribution.

The fixed chronological splits are:

1. Training history from 2023, tested on deterministic 2024 cases. Fixed-history predictions use an exclusive `2024-01-01` end.
2. Training history from 2023–2024, tested on deterministic 2025 cases. Fixed-history predictions use an exclusive `2025-01-01` end.

Rolling 6-, 12-, and 24-month windows end at each prediction date and may use earlier records from the holdout year. This is chronological replay, not a fixed train/test split. Available data begins on 2023-01-01, so early 24-month windows are left-truncated.

## Metrics

- Median MAE is the absolute error between predicted empirical median and realized arrival delay.
- Quantile calibration is the fraction of realized delays at or below a predicted quantile. Ideal values are 0.50, 0.75, and 0.90.
- Pinball loss is the standard asymmetric quantile loss; lower is better.
- CRPS scores the full empirical distribution using an exact sorted-sample identity; lower is better.
- Brier score is probability mean squared error against binary replay outcomes; lower is better.
- Expected calibration error is the prediction-count-weighted absolute gap between mean prediction and outcome frequency across populated 0.1-wide bins.

Carrier, month, and route-volume breakdowns are included in the machine-readable output. Many carrier/month cells contain too few cases in this run for reliable interpretation. Route volume is based on strictly prior carrier-route completed flights: `<100`, `100–999`, and `1,000+`.

## Lookback-window results

### Train 2023, test 2024

| History | N | Median MAE | P50 coverage | P75 coverage | P90 coverage | P50 pinball | P75 pinball | P90 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Previous 6 months | 50 | 19.29 | 0.56 | 0.78 | 0.92 | 9.65 | 10.67 | 9.16 | 15.22 |
| Previous 12 months | 50 | 19.20 | 0.60 | 0.74 | 0.90 | 9.60 | 10.32 | 9.14 | 15.08 |
| Previous 24 months, available history | 50 | **18.60** | 0.60 | 0.82 | 0.88 | **9.30** | 10.23 | **7.34** | **14.47** |
| Fixed 2023 | 50 | 19.77 | 0.60 | 0.82 | 0.88 | 9.89 | 10.71 | 7.75 | 15.23 |

The 24-month configuration performs best in this sample, although it cannot contain a full 24 months for most 2024 predictions. P75 is over-covered at 0.82 and P90 is slightly under-covered at 0.88.

### Train 2023–2024, test 2025

| History | N | Median MAE | P50 coverage | P75 coverage | P90 coverage | P50 pinball | P75 pinball | P90 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Previous 6 months | 50 | 26.02 | 0.50 | 0.70 | 0.86 | 13.01 | 15.33 | 13.00 | 20.78 |
| Previous 12 months | 50 | 27.12 | 0.42 | 0.70 | 0.84 | 13.56 | 15.28 | 13.28 | 21.41 |
| Previous 24 months | 50 | 25.23 | 0.46 | 0.76 | 0.86 | 12.62 | **14.01** | 12.02 | 19.88 |
| Fixed 2023–2024 | 50 | **24.90** | 0.46 | 0.76 | **0.88** | **12.45** | 14.03 | **11.73** | **19.69** |

The fixed two-year and rolling 24-month histories are close. Six months outperforms 12 months but not the longer histories. All windows under-cover the 2025 upper tail; p90 coverage of 0.84–0.88 suggests that recent extreme delays are not fully represented.

## Cohort and fallback behavior

With minimum 30 observations:

| Split/window | Exact | Route/carrier/month/bucket | Route/carrier/season | Route/carrier | Route | Carrier | Global |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024, 6 months | 0 | 13 | 34 | 2 | 1 | 0 | 0 |
| 2024, 12 months | 0 | 31 | 18 | 0 | 1 | 0 | 0 |
| 2024, 24 months | 1 | 39 | 9 | 0 | 1 | 0 | 0 |
| 2024, fixed 2023 | 1 | 32 | 14 | 2 | 1 | 0 | 0 |
| 2025, 6 months | 0 | 12 | 37 | 0 | 1 | 0 | 0 |
| 2025, 12 months | 0 | 29 | 20 | 0 | 1 | 0 | 0 |
| 2025, 24 months | 3 | 38 | 8 | 0 | 1 | 0 | 0 |
| 2025, fixed 2023–2024 | 2 | 34 | 12 | 1 | 1 | 0 | 0 |

Short windows frequently require seasonal fallback. No case reached carrier-only or global fallback. A finer cohort is not automatically better: accepting many small exact cohorts at threshold 10 reduced aggregate accuracy.

## Minimum-sample threshold comparison

### Fixed 2023 history, 2024 holdout

| Minimum N | Median MAE | CRPS | Exact uses | Route/carrier/month/bucket | Seasonal uses |
|---:|---:|---:|---:|---:|---:|
| 10 | 20.46 | 15.59 | 9 | 36 | 3 |
| 30 | 19.77 | **15.23** | 1 | 32 | 14 |
| 60 | **19.25** | 15.35 | 0 | 9 | 37 |

### Fixed 2023–2024 history, 2025 holdout

| Minimum N | Median MAE | CRPS | Exact uses | Route/carrier/month/bucket | Seasonal uses |
|---:|---:|---:|---:|---:|---:|
| 10 | 26.81 | 20.84 | 22 | 19 | 7 |
| 30 | 24.90 | 19.69 | 2 | 34 | 12 |
| 60 | **24.84** | **19.62** | 0 | 26 | 22 |

Threshold 10 is not supported. Thresholds 30 and 60 are close: 60 slightly improves 2025 aggregate scores but sacrifices specificity, while 30 has the best 2024 CRPS. The production threshold remains unchanged pending a larger stratified replay and uncertainty estimates.

## Route-volume findings

Route-volume cells are unbalanced. High-volume routes generally have better median MAE than medium-volume routes in 12/24-month and fixed-history configurations, but low-volume groups contain only one to three cases in several cells. No volume-specific lookback rule should be adopted yet. A larger replay should deliberately stratify cases by prior route volume.

## Extreme-delay sensitivity

Across all completed flights, the 0.1th and 99.9th percentiles are -50 and 762 minutes. Both all-retained and winsorized distributions have median -6, p75 10, and p90 44 minutes.

Using a deterministic 1,000,000-delay reservoir and 50,000 simulations per layover:

| Layover | All retained | Winsorized | Absolute difference |
|---:|---:|---:|---:|
| 45 minutes | 0.72700 | 0.72700 | 0.00000 |
| 85 minutes | 0.90786 | 0.90786 | 0.00000 |
| 120 minutes | 0.95050 | 0.95050 | 0.00000 |

This does not prove extremes never matter for unusual cohorts or higher quantiles. No record was excluded as impossible because no defensible rule yet distinguishes data error from genuine disruption.

## Cancellation and diversion prototype

| Holdout | Cancellation probability | Diversion probability |
|---|---:|---:|
| 2024 | 0.01141 | 0.00164 |
| 2025 | 0.01303 | 0.00251 |

The prototype is `P(original itinerary succeeds) = P(inbound completed) × P(make connection | inbound completed)`. Cancellations and diversions are failures of the original itinerary. Status cohorts use strictly prior records and fall back from carrier-route to route, carrier, then global. The public API is intentionally unchanged.

## Connection-probability model-consistency replay

The replay pairs an inbound with a same-date outbound schedule from the connection airport where available, with a deterministic 45–180 minute fallback otherwise. This historical validation predates the revised passenger-time model and used the then-current triangular 10/20/35-minute transfer assumption without explicit deplaning. Its connection-calibration metrics must not be presented as validation of current production V1. Current V1 uses 20 fixed deplaning minutes, Triangular(15,25,40) gate-transfer minutes, and a 15-minute boarding cutoff; a future validation should rerun connection calibration under those assumptions. Arrival-delay metrics in this report are unaffected.

This is not passenger-level validation because BTS does not observe gates, passenger walking time, boarding, reaccommodation, or whether a passenger attempted the connection.

| Holdout | N | Mean conditional P | Mean combined P | Brier | Expected calibration error |
|---|---:|---:|---:|---:|---:|
| 2024 | 50 | 0.90965 | 0.89771 | 0.04938 | 0.06229 |
| 2025 | 50 | 0.90513 | 0.89115 | 0.13758 | 0.09115 |

Calibration-bin data suitable for reliability diagrams is stored in the JSON report. The 2025 sample overpredicts in several populated bins, but counts are small; for example, the 0.7–0.8 bin contains four cases. These are internal model-consistency diagnostics, not observed passenger calibration.

## Recommendation

1. Do not choose a three-year window merely because three years are stored.
2. Retain minimum 30 provisionally. Threshold 10 performed worse; 60 is competitive but loses specificity.
3. Use 24-month or all-available-prior history as the baseline for the next larger validation. They were most stable across holdouts, while 6/12-month results varied.
4. Expand replay with explicit route-volume, carrier, month, and disruption-severity strata. Fifty cases per split is insufficient for subgroup decisions.
5. Add bootstrap confidence intervals for metric and calibration differences.
6. Investigate 2025 upper-tail undercoverage before changing the estimator. Candidates for later review include coarser tail cohorts, volume-dependent windows, and explicit disruption/status modeling—not automatic recency weighting.
7. Keep status adjustment outside the public API until it receives larger all-status holdout validation.
8. Do not remove extreme delays without an auditable invalid-record rule.

## Remaining limitations

- Only two annual holdouts are possible with the current three-year dataset.
- Sampled validation has wide, unreported uncertainty and sparse subgroup/calibration bins.
- The test sample is not explicitly balanced by route volume or delay severity.
- Rolling windows may use earlier records in the same holdout year; fixed-history results are the conventional static splits.
- Synthetic schedules do not identify actual passenger itineraries.
- Transfer time is assumed, so connection calibration is a model-consistency diagnostic.
- No weather, airport layout, gate, reaccommodation, or real-time information is modeled.
- The model is not production-ready.
