# Larger stratified temporal validation study

## Diagnostic status

This study diagnoses the existing model. It does not change the production estimator, API response, transfer-time assumption, or frontend. Results do not establish production readiness.

## Study configuration

- Historical database: official BTS 2023–2025 full DuckDB
- Holdouts: 2024 and 2025
- Arrival-delay cases: 500 per holdout year
- Connection replay cases: 300 per holdout year
- Arrival lookbacks: previous 24 months and fixed all-prior training years
- Monte Carlo simulations: 2,000 per connection case
- Bootstrap replicates: 500
- Sampling/bootstrap seed: `20260810`
- Runtime: 523.11 seconds
- Machine-readable output: `data/processed/stratified_validation.json` (generated, Git-ignored)

The requested 2,000–5,000 cases per holdout were not computationally practical with the current per-case temporal cohort scans. This staged run is 10 times the previous arrival sample and 6 times the previous connection sample. Its runtime identifies temporal cohort retrieval—not Monte Carlo or bootstrap—as the major bottleneck. A 2,000+ study should first introduce a leakage-safe daily or monthly cumulative cohort cache.

## Strict temporal integrity

Every cohort query requires `flight_date < history_end`. Explicit history ends after the prediction date raise an exception. The 24-month strategy ends at each case date. The fixed all-prior strategy uses only 2023 for 2024 predictions and only 2023–2024 for 2025 predictions.

No cached aggregate was introduced in this study, so there is no alternate path around the strict cutoff.

## Stratified sample design

Arrival strata cross:

- reporting carrier
- prior carrier-route volume group
- season
- scheduled departure bucket
- realized delay-severity group

Connection strata replace severity with flight status. Route volume is calculated only from training records before the holdout year:

- `low_lt_100`: fewer than 100 prior completed carrier-route flights
- `medium_100_999`: 100–999
- `high_1000_plus`: 1,000 or more

Severities are:

- `early_or_on_time`: delay <= 0
- `moderate_1_30`: 1–30 minutes
- `severe_31_180`: 31–180 minutes
- `extreme_over_180`: more than 180 minutes

The sampler selects one deterministically hashed record from each selected stratum. All four samples contain exactly one record per selected stratum: 500 unique arrival strata and 300 unique connection strata per year. This prevents domination by major routes and intentionally oversamples rare severity/status groups.

Each case receives the within-stratum expansion weight `population_count / sampled_count`. The first-stage equal stratum-selection factor is constant within a study and cancels in normalized weighted metrics. Aggregate results below are weighted; raw sample proportions are not population estimates. Subgroup results are diagnostic and retain the applicable weights.

Raw arrival severity counts were:

| Year | Early/on time | Moderate | Severe | Extreme |
|---|---:|---:|---:|---:|
| 2024 | 129 | 119 | 144 | 108 |
| 2025 | 126 | 114 | 137 | 123 |

Raw connection status counts were deliberately non-population-like:

| Year | Completed | Cancelled | Diverted |
|---|---:|---:|---:|
| 2024 | 118 | 98 | 84 |
| 2025 | 100 | 116 | 84 |

Population-weighted metrics correct this diagnostic oversampling.

## Arrival-delay aggregate results

### 2024 holdout

| Lookback | N | Median MAE | P50 coverage | P75 coverage | P90 coverage | P50 pinball | P75 pinball | P90 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Previous 24 months | 500 | **24.57** | 0.544 | 0.683 | **0.852** | **12.29** | **14.59** | **11.95** | **20.03** |
| Fixed 2023 | 500 | 25.39 | 0.512 | 0.678 | 0.820 | 12.69 | 14.86 | 12.88 | 20.75 |

P90 bootstrap intervals:

- 24 months: 0.797–0.900
- Fixed 2023: 0.754–0.874

The fixed-history interval is entirely below nominal 0.90. The 24-month upper endpoint only reaches approximately 0.90. Upper-tail undercoverage is therefore not limited to the original 50-case 2025 sample.

### 2025 holdout

| Lookback | N | Median MAE | P50 coverage | P75 coverage | P90 coverage | P50 pinball | P75 pinball | P90 pinball | CRPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Previous 24 months | 500 | **27.41** | 0.639 | **0.792** | **0.881** | **13.70** | **15.41** | **12.13** | **21.28** |
| Fixed 2023–2024 | 500 | 27.75 | 0.628 | 0.740 | 0.866 | 13.87 | 16.00 | 12.94 | 22.04 |

P90 bootstrap intervals:

- 24 months: 0.822–0.917
- Fixed 2023–2024: 0.805–0.905

Both intervals include 0.90, but point estimates remain below target. The larger sample weakens the claim that 2025 undercoverage alone is definitive; instead it shows a broader upper-tail weakness across both holdouts, with 24-month history consistently better than fixed training history.

## Fallback usage

| Year/lookback | Exact | Route/carrier/month/bucket | Seasonal | Route/carrier | Route | Carrier | Global |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024, 24 months | 3 | 238 | 208 | 26 | 20 | 5 | 0 |
| 2024, fixed | 2 | 160 | 173 | 58 | 73 | 34 | 0 |
| 2025, 24 months | 12 | 209 | 229 | 26 | 19 | 5 | 0 |
| 2025, fixed | 12 | 184 | 171 | 48 | 68 | 17 | 0 |

The rolling strategy preserves substantially more route/carrier/month/bucket specificity. Fixed history falls back more often to route, route/carrier, and carrier levels. Neither strategy reached global fallback.

## Upper-tail localization

### 2024, previous 24 months

Worst weighted p90 coverage cells with meaningful sample counts:

- Carrier `WN`: 0.721, N=34
- March: 0.697, N=44
- June: 0.714, N=42
- Evening departures: 0.726, N=120
- Spring: 0.821, N=116
- Seasonal fallback: 0.833, N=208

The fixed-2023 strategy is worse for `WN` (0.562), December (0.527), and evening departures (0.684).

### 2025, previous 24 months

- Carrier `AA`: 0.757, N=39
- Carrier `OO`: 0.790, N=34
- December: 0.706, N=54
- May: 0.787, N=43
- Evening departures: 0.834, N=121
- Spring: 0.835, N=124

The fixed-history strategy is worse for `OO` (0.725), May (0.656), December (0.658), and evening departures (0.819).

Route-volume groups alone do not explain the issue. For the 24-month strategy, p90 coverage is 0.847/0.856/0.868 across high/medium/low groups in 2024 and 0.880/0.880/0.898 in 2025. Calendar period, carrier, and evening departure appear more diagnostic than volume by itself.

Severity-stratified p90 coverage is necessarily near zero for extreme realized-delay cases because severity is defined using the outcome. These cells demonstrate that disruption cases are present; they are not unbiased population calibration estimates.

## Connection calibration

Reliability-diagram rows with sample count, population weight, mean prediction, and observed frequency are stored under each `bins` field in the JSON report.

### 2024

| Probability | Mean predicted | Observed success | Brier (95% CI) | ECE (95% CI) | Mean gap (95% CI) |
|---|---:|---:|---:|---:|---:|
| Conditional only | 0.926 | 0.867 | 0.094 (0.034–0.171) | 0.066 (0.024–0.155) | +0.059 (-0.025–0.162) |
| Status-combined | 0.912 | 0.867 | 0.093 (0.035–0.168) | 0.073 (0.029–0.160) | +0.045 (-0.039–0.148) |

### 2025

| Probability | Mean predicted | Observed success | Brier (95% CI) | ECE (95% CI) | Mean gap (95% CI) |
|---|---:|---:|---:|---:|---:|
| Conditional only | 0.886 | 0.912 | 0.103 (0.056–0.176) | 0.123 (0.066–0.219) | -0.026 (-0.112–0.076) |
| Status-combined | 0.873 | 0.912 | 0.103 (0.057–0.173) | 0.118 (0.058–0.211) | -0.039 (-0.124–0.061) |

The original 50-case suggestion of systematic 2025 overprediction is not confirmed. The weighted larger replay is mildly underconfident in 2025, while 2024 remains mildly overconfident. Every mean-gap confidence interval includes zero.

The status prototype has mixed effects:

- 2024 Brier improves slightly, but ECE worsens.
- 2025 Brier is effectively unchanged and ECE improves slightly, while mean underconfidence grows.

This is not sufficient evidence to change the public probability formula.

## Connection subgroup diagnostics

Selected weighted combined-probability findings:

- 2024 overprediction is concentrated in the 60–89 minute layover bucket (gap +0.176), summer (+0.362), and June (+0.848). These cells can still be small or heavily weighted.
- 2025 overprediction is strongest in 90–119 minute layovers (+0.128), spring (+0.086), and May (+0.176).
- Carrier subgroup estimates are unstable: for example, 2024 `F9` has a +0.775 gap from only 16 sampled records. Such cells require confidence intervals or larger carrier-specific samples before interpretation.

The raw calibration sample deliberately oversamples cancellations and diversions. Weighted aggregate results—not raw failure counts—are the population-facing diagnostics.

## Error-case analysis

The highest-probability failures are dominated by cancellation/diversion cases with long nominal layovers. Examples include:

| Date | Inbound | Connection | Synthetic outbound | P(success) | Status | Layover | Cohort |
|---|---|---|---|---:|---|---:|---|
| 2024-01-13 | AS ANC–LAX | LAX | LAX–XNA | 0.997 | cancelled | 173 | route/carrier/month/bucket |
| 2024-08-10 | AS SLC–ANC | ANC | ANC–SEA | 0.992 | cancelled | 127 | route |
| 2024-10-17 | HA HNL–KOA | KOA | KOA–LAX | 0.989 | diverted | 167 | route/carrier/month/bucket |
| 2025-07-15 | YX DCA–PNS | PNS | PNS–PHL | 0.997 | cancelled | 91 | route/carrier |
| 2025-09-20 | AA FCA–ORD | ORD | ORD–MSP | 0.996 | diverted | 164 | route/carrier/month/bucket |

The cohort-level completion adjustment is approximately 1–2% on average. It cannot make an individual, unpredictable cancellation probability close to zero, so high conditional connection probability can coexist with realized itinerary failure.

One high-probability completed-flight failure in the top 20 was `G4 MDT–BNA` on 2024-05-17: predicted 0.984, realized delay 187 minutes, layover 142 minutes, seasonal fallback. This illustrates the upper-tail problem directly.

Strong underconfidence examples generally have short layovers but early/on-time realized arrivals, such as:

- 2024 B6 BOS–PBI, 60-minute layover, predicted 0.461, realized 14 minutes early
- 2025 AS SAN–SFO, 60-minute layover, predicted 0.478, realized 1 minute early

These examples are diagnostic only and were not used for tuning.

## Bootstrap interpretation

Intervals use 500 fixed-seed ordinary row bootstrap replicates while retaining design weights. They quantify case-sampling uncertainty conditional on the selected design. They do not account for temporal dependence, route clustering, or uncertainty from selecting strata. Subgroup intervals are not reported because many cells remain small.

## Conclusions and next step

1. The earlier claim of systematic 2025 connection overprediction is not supported by the larger weighted replay. Its mean gap changes sign and all gap intervals include zero.
2. Arrival-delay p90 undercoverage is real enough to investigate and appears in both holdouts. It is concentrated in particular carriers, months, spring periods, and evening departures—not solely low-volume routes.
3. Previous 24 months outperforms fixed all-prior training on every aggregate arrival metric in both holdouts and preserves finer cohorts.
4. Status combination is directionally sensible but does not consistently improve calibration and should remain outside the API.
5. The next validation should implement leakage-safe cumulative cohort caches, then run at least 2,000 cases per holdout with cluster-aware bootstrap intervals.

## Recommendation before frontend development

Modify and revalidate the modeling layer before presenting the probability as a validated user-facing estimate. Specifically:

- investigate calendar/carrier/evening upper-tail undercoverage;
- design a tail-aware but still interpretable empirical approach without arbitrary inflation;
- implement efficient temporal caches and repeat at the target 2,000–5,000 scale;
- validate cancellation/diversion probabilities separately before API integration.

A frontend shell could be built as an engineering exercise, but the numerical result should remain clearly experimental until those modeling issues are addressed.

