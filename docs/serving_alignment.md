# Frozen V1 serving alignment

## Decision

The public API now serves the validated frozen V1 strategy:

- previous 24 calendar months;
- inclusive lower bound: `flight_date >= prediction_date - 24 months`;
- strict exclusive upper bound: `flight_date < prediction_date`;
- minimum cohort size 30;
- unchanged empirical cohort hierarchy;
- unchanged empirical delay sampling, Monte Carlo simulator, boarding cutoff, and transfer-time assumption.

Serving and validation call `temporal_delay_cohorts` in `delay_model.py`. There is no separate production implementation of temporal cohort selection.

## Boundary semantics

Calendar-month subtraction clamps invalid month-end dates. For example, 2024-02-29 minus 24 months is 2022-02-28. The lower boundary is included and the prediction date is always excluded. Tests cover lower/upper boundaries, leap dates, future prediction dates, and API-level leakage prevention.

For a prediction after the latest available BTS record, the estimator still applies the same 24-month lower bound and uses only rows actually present before the prediction date. It does not extend, repeat, or fabricate history.

## Historical coverage metadata

The API model metadata exposes:

- available BTS start and end dates;
- requested prediction date;
- effective inclusive history start and end dates;
- strict exclusive cutoff date;
- lookback length;
- a freshness warning when the prediction is more than 90 days after the latest available record.

The backend logs the same dates internally. The frontend displays only a concise data-coverage notice plus the detailed dates in the existing expandable methodology panel.

Current full-data coverage is 2023-01-01 through 2025-12-31.

## Production database

The production artifact retains these eight columns:

1. `flight_date`
2. `month`
3. `day_of_week`
4. `reporting_carrier`
5. `origin`
6. `destination`
7. `departure_time_bucket`
8. `arrival_delay_minutes`

It retains all 20,588,134 rows in source order. No row is filtered, aggregated, sampled, or approximated during artifact construction.

| Artifact | Bytes | MiB |
|---|---:|---:|
| Full research DuckDB | 712,519,680 | 679.5 |
| Temporal production DuckDB | 113,782,784 | 108.5 |

Production artifact SHA-256:

```text
6d1b144fd7f7d7a7db742503b60019eb91bdc9c8a1336e9d2b0ff32c9d18776b
```

## Full/production equivalence

The comparison used 50 deterministic representative cases, random seed 20260810, and 2,000 simulations per case. Both databases were queried through the same frozen-V1 24-month estimator.

```json
{
  "maximum_probability_difference": 0.0,
  "maximum_p50_difference_minutes": 0.0,
  "maximum_p75_difference_minutes": 0.0,
  "maximum_p90_difference_minutes": 0.0,
  "cohort_mismatches": 0,
  "sample_size_mismatches": 0,
  "exact_match": true
}
```

## Temporal revalidation

The sampled temporal replay was rerun with seed 20260810 and 50 cases per holdout. Its 24-month results reproduce the previously documented values.

| Holdout | Cases | Median MAE | P50 coverage | P75 coverage | P90 coverage | P50 pinball | P75 pinball | P90 pinball | Mean CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024 | 50 | 18.60 | 0.60 | 0.82 | 0.88 | 9.30 | 10.23 | 7.34 | 14.47 |
| 2025 | 50 | 25.23 | 0.46 | 0.76 | 0.86 | 12.62 | 14.01 | 12.02 | 19.88 |

Fallback counts also reproduce the validated report:

- 2024: exact 1, route/carrier/month/bucket 39, seasonal 9, route 1.
- 2025: exact 3, route/carrier/month/bucket 38, seasonal 8, route 1.

The generated full replay output is written to `work/temporal_revalidation_24m.json` and remains Git-ignored.

## Deployment gate

The estimator/validation mismatch is resolved. Model behavior and the production database are aligned and exact. Public deployment may proceed only after the prepared Docker image is built and its local container smoke tests pass on a Docker-capable machine, as described in `docs/deployment.md`.
