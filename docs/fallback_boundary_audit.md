# Minimum-Cohort Fallback Boundary Audit

## Scope and design

This is a research-only audit. Production V1 remains unchanged: the first cohort with at least 30 strictly prior observations is selected. The revised passenger-time assumptions, deterministic seeding, 24-month serving lookback, API, and frontend are not modified.

The near-boundary sample uses two strict fixed-history holdouts:

- train 2023, predict 2024;
- train 2023–2024, predict 2025.

For each holdout, eight exact cohorts were deterministically selected at each feasible target N: 20, 24, 25, 28, 29, 30, 31, 32, 35, and 40. This gives 160 cases, exactly 16 at each N. One actual holdout flight represents each cohort, and a deterministic same-day connection schedule supplies the layover. Each case compares the exact distribution, production N=30 selection, and immediate broader route/carrier/month/time-bucket distribution using the same revised-V1 simulator configuration.

This is a balanced diagnostic sample rather than a prevalence-weighted estimate of all production requests. It measures cross-sectional boundary sensitivity; it does not observe the same cohort evolving from N=29 to N=30.

Reproduce the analysis with:

```powershell
python scripts/fallback_boundary_audit.py --per-count 8 --validation-cases-per-year 50
```

Detailed case rows and metrics are written to the Git-ignored `work/fallback_boundary_audit.json`.

## Jump-size distribution

`Production displacement` is the absolute difference between the exact-cohort probability and the probability actually returned by hard N=30. It is necessarily zero for N>=30 because production selects exact. `Boundary contrast` compares exact with its immediate broader cohort on both sides of N=30 and shows the potential discontinuity hidden above the threshold.

| Population | N | Median | P90 | P95 | Maximum |
|---|---:|---:|---:|---:|---:|
| All near-boundary cases, production displacement | 160 | 0.00 pp | 6.88 pp | 11.90 pp | 27.84 pp |
| N<30 cases, production displacement | 80 | 2.64 pp | — | 14.47 pp | 27.84 pp |
| All cases, exact vs immediate-broader contrast | 160 | 2.68 pp | — | 12.34 pp | 27.84 pp |

Among the 80 N<30 cases that actually fall back:

- 22 (27.5%) differ by at least 5 percentage points;
- 13 (16.25%) differ by at least 10 percentage points;
- fallback raises probability in 37 cases, lowers it in 41, and ties in 2;
- ten cases move upward by at least 10 points and three move downward by at least 10 points.

Large displacement is not confined to N=20. Counts of >=10-point cases were: N20 five, N24 one, N25 zero, N28 two, and N29 five. In this balanced sample, N29 has a 4.58-point median and 14.79-point p95 displacement. The threshold therefore can hide a material exact-versus-broader contrast immediately below N=30, although most cases are much smaller.

## Worst examples

| Carrier/route | Prediction date | Month/bucket | Exact N | Fallback N/level | Layover | Exact P | Production P | Jump |
|---|---|---|---:|---|---:|---:|---:|---:|
| WN SAN-OAK | 2025-10-05 | Oct/evening | 20 | 146 route/carrier/month/bucket | 70 | 29.02% | 56.86% | 27.84 pp |
| AA ORD-PHX | 2024-12-27 | Dec/morning | 20 | 125 route/carrier/month/bucket | 55 | 46.32% | 67.92% | 21.61 pp |
| HA HNL-KOA | 2024-05-24 | May/morning | 28 | 201 route/carrier/month/bucket | 116 | 76.15% | 94.12% | 17.98 pp |
| WN DEN-MDW | 2025-04-29 | Apr/afternoon | 29 | 167 route/carrier/month/bucket | 60 | 20.69% | 36.67% | 15.99 pp |
| AA ABQ-DFW | 2025-03-21 | Mar/morning | 29 | 181 route/carrier/month/bucket | 105 | 78.70% | 93.09% | 14.39 pp |

The worst SAN-OAK exact cohort has median/p75/p90 delays of 17.5/40.25/60.4 minutes, versus 4/25.75/47 for the broader cohort. HNL-KOA is more tail-sensitive: exact p90 is 202.9 minutes versus 24 for the broader pool. These examples show why connection probability can move sharply even when medians are not dramatically different.

Material cases appeared across carriers and calendar periods. Among the 13 >=10-point cases, AA and WN contributed four each, HA two, and B6, YX, and DL one each. Morning, afternoon, and evening all appear. Cell sizes are too small to establish a carrier or month effect; no stable concentration was demonstrated.

## Why the jumps occur

The exact cohort adds day of week to carrier, directional route, calendar month, and departure bucket. Its immediate broader cohort removes day of week. The contrast therefore combines:

1. sampling variability from only 20–29 exact observations;
2. genuine weekday-specific or schedule-period differences;
3. pooling across more flights and weekdays in the broader cohort.

The audit cannot cleanly identify those components from one realization. Evidence points to all three: directions are nearly balanced, while worst-case median and upper-tail statistics sometimes differ substantially. This is not evidence of a selection-code bug; it is the expected discontinuity of a hard empirical threshold.

## Temporal threshold comparison

The threshold validation uses 50 deterministic completed-flight cases per holdout (100 total), identical cases and synthetic revised-V1 connection outcomes for every method. Lower CRPS, Brier, and ECE are better. Calibration targets are 0.50, 0.75, and 0.90. The sample is deliberately local-development sized, so small metric differences are not decisive.

### 2024 holdout

| Method | CRPS | P50 | P75 | P90 | Brier | ECE | Exact uses | Broader than month/bucket |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hard N20 | 19.606 | 0.58 | 0.78 | 0.86 | 0.0917 | 0.1011 | 2/50 | 10/50 |
| Hard N30 | 19.659 | 0.64 | 0.80 | 0.86 | 0.0915 | 0.0842 | 1/50 | 20/50 |
| Hard N40 | **19.458** | 0.64 | 0.80 | 0.86 | 0.0858 | 0.0801 | 0/50 | 31/50 |
| Hard N60 | 19.597 | 0.66 | 0.78 | 0.86 | **0.0837** | 0.0927 | 0/50 | 37/50 |
| Research blend | 19.605 | 0.64 | 0.80 | 0.86 | 0.0907 | **0.0665** | blended | blended |

### 2025 holdout

| Method | CRPS | P50 | P75 | P90 | Brier | ECE | Exact uses | Broader than month/bucket |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hard N20 | 19.340 | 0.62 | 0.80 | 0.88 | 0.1048 | **0.0918** | 7/50 | 11/50 |
| Hard N30 | 18.867 | 0.62 | 0.80 | 0.88 | 0.0968 | 0.0942 | 4/50 | 16/50 |
| Hard N40 | 18.851 | 0.62 | 0.78 | **0.90** | 0.1006 | 0.1151 | 1/50 | 20/50 |
| Hard N60 | **17.828** | 0.60 | 0.78 | **0.90** | **0.0964** | 0.0994 | 0/50 | 28/50 |
| Research blend | 19.057 | 0.60 | 0.82 | 0.88 | 0.0984 | 0.1171 | blended | blended |

N20 retains more exact cohorts but is generally worse on connection Brier and does not improve p90 calibration. N40/N60 improve some metrics, especially 2025 CRPS, but substantially sacrifice cohort specificity and are mixed on ECE and Brier. N30 is not uniformly best, but it remains competitive without the specificity loss of N40/N60.

## Research blending experiment

The fixed candidate uses exact weight `min(exact N / 60, 1)` and places the remaining weight on the first broader cohort meeting N=30. Mixture distributions are constructed deterministically from empirical quantiles. The formula was declared before examining results and was not tuned per case.

Blending helped slightly in 2024: Brier moved from 0.0915 to 0.0907 and ECE from 0.0842 to 0.0665, while CRPS changed from 19.659 to 19.605. It did not replicate in 2025: CRPS worsened from 18.867 to 19.057, Brier from 0.0968 to 0.0984, and ECE from 0.0942 to 0.1171. Therefore this simple blend did not provide robust out-of-sample improvement.

## Recommendation

**Recommendation 1: keep the current hard N=30 fallback for V1.**

The audit confirms real and occasionally large discontinuities, including five >=10-point cases at N=29. However, threshold validation does not establish a consistently better alternative: higher thresholds trade away specificity, N20 is generally weaker, and the simple blend helps one holdout but hurts the other. Changing production on this 100-case validation would be premature.

The next research step should use the existing leakage-safe cohort cache work to run at least 2,000 cases per holdout, report cluster-aware uncertainty, and evaluate pre-registered hierarchical shrinkage methods. Any threshold or blending change should be introduced only in a future model version, not silently within V1.
