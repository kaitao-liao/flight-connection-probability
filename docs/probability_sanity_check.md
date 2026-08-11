# Revised V1 Probability Sanity Check

## Scope

This validation uses the production DuckDB, prediction date 2025-07-15, the strict prior 24-month empirical arrival-delay model, minimum cohort N=30, 20,000 simulations, and deterministic per-itinerary seeding. Passenger time uses the revised V1 assumptions: 20 fixed deplaning minutes, Triangular(15, 25, 40) gate-transfer minutes, and a 15-minute boarding cutoff.

The passenger-transfer values are modeling assumptions, not BTS measurements.

## Layover monotonicity

The same fixed-seed DL MCO-ATL delay cohort was used for the paired comparison.

| Scheduled layover | Probability |
|---:|---:|
| 20 min | 0.00% |
| 25 min | 0.00% |
| 30 min | 0.00% |
| 45 min | 11.16% |
| 60 min | 54.44% |
| 75 min | 74.18% |
| 90 min | 82.67% |
| 120 min | 87.23% |

Pass: all seven adjacent changes are non-decreasing.

The on-time fixed-delay scenario is 0% below the 50-minute theoretical minimum. The overall 45-minute probability is nonzero only because the empirical cohort contains early arrivals that create extra actual connection time. At 30 minutes, even the early-arrival tail in this selected cohort is insufficient, so the overall result is naturally 0%.

Around the 60-minute modal passenger requirement, the on-time scenario was 40.23% and the overall probability was 54.44%; historical early and late arrivals move the overall estimate around the conditional on-time value. At 75 minutes, every on-time gate-transfer draw fits because 75 is the maximum pre-delay passenger requirement. At 90 minutes, on-time and +15 scenarios are 100%, while +30 is 40.23% and +45 is 0%.

## Old versus revised assumptions

These paired values use the same empirical delay cohort, 20,000 simulations, and seed; only the explicitly requested passenger-time assumptions differ.

| Layover | Superseded model | Revised V1 |
|---:|---:|---:|
| 45 min | 69.61% | 11.16% |
| 60 min | 80.42% | 54.44% |
| 75 min | 85.63% | 74.18% |
| 90 min | 87.14% | 82.67% |

No tuning was performed in response to these values.

## Fixed-delay sensitivity

Every tested scenario set satisfied `on time >= +15 >= +30 >= +45`. For the representative 85-minute DL ATL-JFK-BOS case, the updated values are 100.00%, 93.50%, 9.81%, and 0.00%. See `docs/sensitivity_audit.md` for the draw-level trace.

## Time of day

The same DL MCO-ATL route and 60-minute layover produced:

| Bucket | Cohort | N | Empirical delay P90 | Probability |
|---|---|---:|---:|---:|
| Morning | exact | 54 | 69.1 min | 54.44% |
| Afternoon | exact | 53 | 112.0 min | 34.29% |
| Evening | route/carrier/month/bucket | 125 | 203.0 min | 13.90% |

The relationship remains consistent with the empirical upper-tail finding: the later buckets have worse delay tails and lower connection probabilities. As before, the evening case also uses a broader cohort, so this is a consistency check rather than a causal time-of-day estimate.

## Cohorts, carriers, and API stability

Exact, route/carrier/month/bucket, seasonal, and route/carrier fallback examples all selected the intended first cohort meeting N=30. The previously flagged CLT-BNA boundary comparison is smaller under the revised passenger model: 41.62% for the rejected 25-observation month/bucket cohort versus 36.38% for the selected 880-observation seasonal cohort. This remains a model-validation consideration, not an implementation failure.

Representative AA, DL, UA, WN, AS, and B6 results remained ordered consistently with their selected empirical distributions; they are not controlled carrier rankings.

Determinism passed. Fifty repeats of each direct case and 12 identical API requests all had standard deviation and range exactly zero. The repeated production-path 60-minute DL MCO-ATL response was always 53.335% (displayed as 53.3%).

## Assessment

- Layover monotonicity passed.
- Fixed-delay sensitivity ordering passed.
- The 50/60/75-minute minimum/mode/maximum interpretation is reflected correctly.
- Time-of-day behavior remains logically consistent with empirical arrival-delay tails.
- Cohort selection, fallback, timezone validation, response structure, and deterministic serving remain intact.
- No unexpected implementation regression was found.
