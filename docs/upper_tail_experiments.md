# Upper-tail modeling experiments

## Decision status

This report evaluates experimental estimators. The production estimator, public API, transfer-time assumptions, and frontend were not changed. No candidate is promoted automatically.

## Motivation

The larger stratified validation found 24-month empirical p90 coverage below nominal in both holdouts, with weakness around several carriers, calendar periods, and evening departures. This study asks whether simple interpretable empirical-tail methods improve severe-delay calibration without damaging central accuracy, CRPS, or connection calibration.

## Exact validation acceleration

The original validation path opened a DuckDB connection per case and fetched all seven cohort levels, including very large carrier/global arrays even when a specific cohort already met the sample threshold.

`LazyTemporalStore` provides an exact accelerated path:

1. Hold one read-only DuckDB connection for a study.
2. Query cohorts lazily in hierarchy order.
3. Stop when minimum N=30 is satisfied.
4. Cache only arrays actually requested by baseline/candidates.
5. Include prediction date, history start/end, route, carrier, departure bucket, and cohort name in every cache key.

All queries retain `flight_date < history_end`; rolling histories also retain the lower bound. There is no cumulative quantile approximation.

### Fixed-seed equivalence benchmark

| Measure | Result |
|---|---:|
| Cases | 30 |
| Original runtime | 6.7467 seconds |
| Accelerated runtime | 0.3151 seconds |
| Speedup | **21.41x** |
| Maximum p50/p75/p90/p95 difference | 0.0 minutes |
| Cohort levels identical | yes |
| Tolerance | 1e-12 |
| Approximation | none |
| Accelerated benchmark cache | 1,925,544 bytes |

The full experiment used 841,805,416 cached-array bytes for the 2024 holdout and 1,068,309,160 bytes for 2025. These are temporary in-process arrays and produce no persistent cache database. Memory, not disk, is the main acceleration cost.

## Experimental design

- Historical source: official BTS 2023–2025 full DuckDB
- Lookback: strictly prior 24 months, left-truncated at 2023-01-01
- Minimum cohort size: 30
- Holdouts: 2024 and 2025
- Arrival cases: 2,000 per holdout
- Connection cases: 1,000 per holdout
- Monte Carlo simulations: 2,000 per case
- Cluster bootstrap: 300 replicates
- Cluster unit: directional route by year-month
- Fixed seed: `20260810`
- Final runtime: 577.72 seconds

Sampling is deterministic, stratified, and design-weighted as documented in `stratified_validation.md`. Every candidate uses exactly the same cases. Connection candidates use the same layover, transfer-time realization, Monte Carlo seed, and binary outcome.

Candidate rules were fixed before reading final holdout results. No final-period tuning was performed.

## Candidate methods

### A: Baseline

Unchanged 24-month empirical estimator, existing cohort hierarchy, minimum N=30, and no tail correction.

### B: Hierarchical tail pooling

Use the selected specific cohort for the center. If its upper 20% contains fewer than 100 observations and the strictly prior carrier-route-season cohort has at least 100 upper-tail observations, replace the selected distribution's sorted upper 20% with empirical quantiles from the broader upper tail. Otherwise return baseline unchanged.

### C: Recent-tail augmentation

Use the 24-month selected cohort for the center and the selected strictly prior six-month cohort as tail source. Replace the upper 20% using recent empirical tail quantiles when the recent tail has at least 30 observations. Otherwise return baseline unchanged.

### D: Tail-aware carrier/month/bucket pooling

Use the selected cohort for the center. When its upper tail is small, pool from strictly prior carrier + calendar month + departure-time-bucket upper-tail observations using the same empirical rule as B.

All candidates replace empirical quantiles; none adds a fixed number of minutes or applies global p90 inflation. Tail replacement can move a quantile down as well as up.

## Arrival-distribution results

### 2024 holdout

| Candidate | Median MAE | P50 | P75 | P90 | |P90-0.90| | P95 | P90 pinball | P95 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Baseline | 22.610 | 0.517 | 0.752 | 0.885 | 0.015 | 0.936 | 10.809 | 8.332 | 18.117 |
| B Hierarchical | 22.610 | 0.517 | 0.752 | 0.880 | 0.020 | 0.936 | 10.883 | 8.326 | 18.102 |
| C Recent tail | 22.611 | 0.517 | 0.751 | 0.883 | 0.017 | 0.936 | 10.675 | 8.308 | 18.078 |
| D Carrier/month/bucket | 22.612 | 0.517 | 0.748 | **0.897** | **0.003** | **0.939** | **10.422** | **7.972** | **17.920** |

Cluster-aware p90 95% intervals:

- A: 0.862–0.906
- B: 0.859–0.901
- C: 0.861–0.902
- D: 0.874–0.913

CRPS intervals strongly overlap: baseline 16.466–19.817 and D 16.251–19.594.

### 2025 holdout

| Candidate | Median MAE | P50 | P75 | P90 | |P90-0.90| | P95 | P90 pinball | P95 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Baseline | 24.628 | 0.516 | 0.746 | 0.887 | 0.013 | 0.927 | 11.214 | 8.548 | **19.357** |
| B Hierarchical | 24.628 | 0.516 | 0.742 | 0.883 | 0.017 | 0.934 | 11.424 | 8.567 | 19.397 |
| C Recent tail | 24.628 | 0.516 | 0.742 | **0.891** | **0.009** | **0.934** | **11.161** | **8.445** | 19.321 |
| D Carrier/month/bucket | **24.622** | 0.516 | 0.739 | 0.884 | 0.016 | 0.927 | 11.538 | 8.702 | 19.387 |

Cluster-aware p90 95% intervals:

- A: 0.865–0.908
- B: 0.861–0.904
- C: 0.868–0.910
- D: 0.862–0.904

CRPS intervals again overlap materially. Candidate C has the best point CRPS and tail scores in 2025, but the differences are small.

## Upper-tail diagnosis

Baseline severe-delay population-weighted frequency was 12.5% in 2024 and 14.7% in 2025.

### Cohort size

| Year | Cohort size | N | P90 coverage | Severe-delay frequency |
|---|---|---:|---:|---:|
| 2024 | 30–99 | 1,372 | 0.890 | 0.123 |
| 2024 | 100–499 | 505 | 0.873 | 0.129 |
| 2024 | 500+ | 123 | 0.929 | 0.097 |
| 2025 | 30–99 | 1,278 | 0.893 | 0.141 |
| 2025 | 100–499 | 597 | 0.875 | 0.157 |
| 2025 | 500+ | 125 | 0.944 | 0.112 |

Undercoverage is not simply “small cohort = bad.” Medium 100–499 cohorts are worse than 30–99 cohorts, while 500+ cohorts are best. This suggests pooling/composition and temporal shift matter alongside sample size.

### Recent severe-delay frequency

| Year | Prior six-month severe frequency | N | P90 coverage | Realized severe frequency |
|---|---|---:|---:|---:|
| 2024 | Under 10% | 796 | 0.880 | 0.087 |
| 2024 | 10–20% | 800 | 0.894 | 0.132 |
| 2024 | Over 20% | 404 | 0.878 | 0.203 |
| 2025 | Under 10% | 624 | 0.900 | 0.084 |
| 2025 | 10–20% | 870 | 0.865 | 0.171 |
| 2025 | Over 20% | 506 | 0.907 | 0.212 |

The relationship is non-monotonic. In 2025 the 10–20% group is worst, while the over-20% group is adequately covered. A simple “more recent disruption means inflate tail” rule is not supported.

### Time and season

Evening remains the stable problem:

- 2024 evening p90: 0.839; severe frequency 0.199
- 2025 evening p90: 0.847; severe frequency 0.228

Morning coverage is 0.919/0.895 and afternoon 0.868/0.902. Seasonal patterns change across years: 2024 winter is lowest at 0.873, while 2025 fall is lowest at 0.871. This instability helps explain why broad global seasonal pooling is not consistently beneficial.

## Subgroup candidate behavior

Candidate D addresses the intended 2024 weakness:

- 2024 evening p90 improves by 0.027 to 0.866.
- 2024 `WN` improves by 0.032 to 0.907.
- 2024 route/carrier/month/bucket cohorts improve by 0.013 to 0.902.

But it is unstable in 2025:

- August drops by 0.069 to 0.817.
- December drops by 0.045 to 0.822.
- `MQ` drops by 0.041.
- Route/carrier cohorts drop by 0.043.

Candidate C is more stable but mixed:

- 2025 `DL` improves by 0.021; June improves by 0.029.
- 2024 `WN` improves by 0.023.
- 2024 evening drops by 0.016 and June drops by 0.037.

Candidate B reduces aggregate p90 coverage in both years and has large month-specific regressions, including November 2024 (-0.113) and October 2025 (-0.080).

## Connection-probability impact

### 2024

| Candidate | Mean P | Observed | Brier | ECE | Mean gap |
|---|---:|---:|---:|---:|---:|
| A Baseline | 0.9149 | 0.9186 | 0.0701 | **0.0086** | -0.0037 |
| B Hierarchical | 0.9140 | 0.9186 | **0.0694** | 0.0230 | -0.0046 |
| C Recent tail | 0.9146 | 0.9186 | 0.0718 | 0.0241 | -0.0040 |
| D Carrier/month/bucket | 0.9196 | 0.9186 | 0.0703 | 0.0162 | **0.0011** |

Baseline Brier 95% CI is 0.043–0.111 and all candidate intervals overlap it. Candidate C worsens medium-layover Brier by 0.0025 and ECE by 0.0317; its evening ECE increases by 0.0503. Candidate D has mixed small subgroup effects.

### 2025

| Candidate | Mean P | Observed | Brier | ECE | Mean gap |
|---|---:|---:|---:|---:|---:|
| A Baseline | 0.9011 | 0.8897 | 0.0931 | 0.0320 | 0.0113 |
| B Hierarchical | 0.9018 | 0.8897 | **0.0905** | **0.0301** | 0.0120 |
| C Recent tail | 0.9000 | 0.8897 | 0.0932 | 0.0317 | 0.0103 |
| D Carrier/month/bucket | 0.8924 | 0.8897 | 0.0940 | 0.0489 | **0.0027** |

All Brier, ECE, and gap intervals overlap. Candidate D improves short-layover Brier/ECE but worsens medium-layover Brier and aggregate ECE. Candidate C does not materially change Brier and has mixed ECE changes.

Reliability-diagram data, weighted subgroup metrics for layover/time/season/status, and all cluster intervals are stored in `data/processed/upper_tail_experiments.json`.

## Cluster-aware uncertainty

The bootstrap resamples directional-route × year-month clusters, preserving observations that share a route and temporal block. It uses 300 fixed-seed replicates and retains design weights. Intervals are reported for p90 coverage, CRPS, Brier score, ECE, and mean calibration gap.

This is more appropriate than independent row bootstrap but does not capture every dependence source, such as carrier-wide operational disruptions spanning multiple routes/months.

## Model-selection decision

No candidate consistently improves the baseline across 2024 and 2025 while preserving arrival and connection metrics:

- B worsens p90 in both years.
- C is the most plausible alternative: it slightly improves CRPS in both years and 2025 p90/p95, but worsens 2024 p90 and 2024 connection Brier/ECE. Differences are small relative to uncertainty.
- D is strongest in 2024 but regresses across multiple 2025 metrics and subgroups.

Therefore no experimental candidate should replace the current estimator.

## V1 recommendation

Retain Candidate A—the unchanged 24-month empirical estimator—as the V1 control. “Retain” does not mean validated for production; it means none of the tested tail modifications earned promotion.

Before exposing the number as a validated passenger-facing probability:

1. Investigate a dedicated, interpretable evening-flight cohort or tail pool using an earlier nested development period.
2. Validate candidate C on an additional future holdout when 2026 data is complete; do not tune it on 2024/2025.
3. Reduce cache memory with compact sorted arrays or date-partitioned reusable storage while preserving exact cutoffs.
4. Add carrier-wide temporal disruption clusters to uncertainty analysis.
5. Keep cancellation/diversion outside this experiment and outside the public formula.

The project can proceed to a clearly labeled experimental frontend, but the probability should not be described as production-calibrated.

## Limitations

- Only two annual holdouts exist.
- Tail rules use fixed heuristic support thresholds; they were not nested-tuned.
- The exact cache can exceed 1 GB for 2,000/1,000 studies.
- Synthetic connections and assumed transfer times are model-consistency validation, not passenger observations.
- Cluster bootstrap intervals remain conditional on this sampling design.
- No weather, FAA, gate, or real-time data is used.

