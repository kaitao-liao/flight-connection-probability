# V1 Probability Sanity Check

## Scope and method

This is a diagnostic review of the frozen V1 estimator. It does not tune the delay model, change the transfer-time assumption, or modify the frontend. The original review identified user-visible unseeded Monte Carlo variation; production V1 now derives a deterministic seed from each canonical itinerary as a reproducibility-only follow-up.

The checks used `data/production/flights_production.duckdb` (20,588,134 completed, non-diverted BTS flights dated 2023-01-01 through 2025-12-31), a prediction date of 2025-07-15, the strict prior 24-month window, the production 20,000-simulation setting, the triangular 10/20/35-minute transfer-time assumption, and the 15-minute boarding cutoff. Fixed seed `20260811` was used only for paired logical comparisons so Monte Carlo noise could not obscure ordering. Repeated-request checks use the production SHA-256 canonical-itinerary seed.

The matrix contains 22 configured evaluations: six layovers, three very short layovers, three time-of-day cases, four cohort/fallback cases, and six carrier/route cases. Some configurations intentionally overlap. In addition, 150 direct identical-estimator reruns and 12 identical requests through `POST /api/v1/connection-risk` were made.

Reproduce the diagnostic output with:

```powershell
python scripts/probability_sanity_check.py
```

The script writes detailed machine-readable results to the Git-ignored `work/probability_sanity_results.json`.

## Results

### Layover monotonicity

The same DL MCO-ATL arrival-delay cohort was used throughout.

| Scheduled layover | Connection probability |
|---:|---:|
| 25 min | 25.01% |
| 30 min | 41.14% |
| 45 min | 69.61% |
| 60 min | 80.42% |
| 90 min | 87.14% |
| 120 min | 90.67% |

Pass. Probability increased at every step. This is also structurally expected: with the same simulated delay and transfer draws, increasing the deadline cannot turn a success into a miss. No meaningful inverse layover effect was found.

### Arrival-delay scenario sensitivity

All 22 evaluated scenario sets were non-increasing from on time to +15, +30, and +45 minutes. For the representative 60-minute layover, the values were 100.00%, 93.38%, 9.69%, and 0.00%. The sharp steps are consistent with the current bounded 10–35-minute transfer assumption and 15-minute boarding cutoff; they are not derived from airport measurements. The scenarios condition on a fixed arrival delay, so their probabilities depend on layover and the transfer assumption, not the route's historical delay cohort.

Pass. No ordering violation was found.

### Very short layovers

| Scheduled layover | Time remaining after cutoff | Probability |
|---:|---:|---:|
| 20 min | 5 min | 11.16% |
| 25 min | 10 min | 25.01% |
| 30 min | 15 min | 41.14% |

These values initially look generous, especially 41.14% at 30 minutes, but the source is identifiable. The selected 54-flight DL MCO-ATL exact cohort has median delay -4.5 minutes, p25 -10, p10 -15; 50.0% arrived at least 5 minutes early, 33.3% at least 10 minutes early, and 16.7% at least 15 minutes early. Early arrival creates time that can offset the transfer and cutoff. By contrast, the conditional on-time scenarios are 0% at 20 and 25 minutes and 9.69% at 30 minutes because the transfer minimum is 10 minutes.

This is internally consistent with the current arithmetic. It should still be treated cautiously: the exact cohort is small, transfer time is an assumption rather than measured airport data, and V1 excludes cancellations and diversions.

### Time of day

The comparison holds route, carrier, date, and 60-minute layover constant for DL MCO-ATL.

| Departure bucket | Cohort | N | Median delay | P90 delay | Probability |
|---|---|---:|---:|---:|---:|
| Morning | exact | 54 | -4.5 min | 69.1 min | 80.42% |
| Afternoon | exact | 53 | 18.0 min | 112.0 min | 58.55% |
| Evening | route/carrier/month/bucket | 125 | 65.0 min | 203.0 min | 30.15% |

This representative route is directionally consistent with the prior validation finding that evening operations have a worse upper delay tail. The evening p90 is materially higher and the connection probability lower. The evening case also falls back one level, so this single comparison does not isolate time of day causally; it is a consistency check, not a carrier schedule study.

### Cohort and fallback behavior

| Carrier/route | Selected cohort | N | 60-minute probability |
|---|---|---:|---:|
| HA HNL-OGG | exact | 62 | 99.38% |
| AA DFW-LAS | route/carrier/month/bucket | 171 | 78.95% |
| AA CLT-BNA | route/carrier/season | 880 | 61.58% |
| UA PBI-ORD | route/carrier | 868 | 83.06% |

The hierarchy selected the first cohort meeting the documented 30-observation minimum in every case. One material boundary sensitivity was found, but not an implementation error: AA CLT-BNA had only 25 month/bucket observations, whose counterfactual probability was 87.92%; falling back to the 880-observation seasonal cohort produced 61.58%, a 26.34 percentage-point difference. The rejected four-observation exact cohort was still less reliable. AA DFW-LAS showed only a 1.23-point change between its rejected 27-observation exact cohort (77.73%) and selected 171-observation month/bucket cohort (78.95%). UA PBI-ORD had no observations in the narrower time-specific cohorts.

The CLT-BNA discontinuity is worth future validation around the hard minimum-N boundary. Current behavior follows the frozen hierarchy exactly, so it is a model limitation/sensitivity rather than a code bug. No silent use of future data or failure to apply the minimum was observed.

### Carrier/route comparison

| Carrier | Representative inbound route | Cohort | N | Median | P90 | 60-minute probability |
|---|---|---|---:|---:|---:|---:|
| AA | LAX-DFW | exact | 49 | -9.0 | 19.8 | 89.29% |
| DL | MCO-ATL | exact | 54 | -4.5 | 69.1 | 80.42% |
| UA | LGA-ORD | exact | 50 | -17.5 | 0.5 | 96.16% |
| WN | PHX-DEN | exact | 44 | -6.0 | 9.5 | 96.44% |
| AS | ANC-SEA | exact | 59 | 0.0 | 21.2 | 89.85% |
| B6 | BOS-DCA | exact | 42 | -10.5 | 13.8 | 92.92% |

No internal inconsistency was found: higher probabilities align with more favorable empirical distributions in these selected cohorts. These are different routes and small exact cohorts, so the table must not be interpreted as a controlled airline ranking or proof of a carrier effect.

### Repeated identical calculations

| Path/case | Runs | Mean | Minimum | Maximum | Std. dev. | Range |
|---|---:|---:|---:|---:|---:|---:|
| Direct, DL MCO-ATL, 30 min | 50 | 40.10% | 40.10% | 40.10% | 0.000 pp | 0.00 pp |
| Direct, DL MCO-ATL, 60 min | 50 | 79.93% | 79.93% | 79.93% | 0.000 pp | 0.00 pp |
| Direct, B6 BOS-DCA, 90 min | 50 | 99.76% | 99.76% | 99.76% | 0.000 pp | 0.00 pp |
| Actual API, DL MCO-ATL, 60 min | 12 | 79.93% | 79.93% | 79.93% | 0.000 pp | 0.00 pp |

`pp` means percentage points. Before deterministic serving, the original check observed 39.58%–41.51% for the 30-minute direct case, 79.51%–80.70% for the 60-minute direct case, and 79.85%–80.52% through the actual API. Those ranges were visible at one-decimal display precision. After deterministic per-itinerary seeding, every repeated case has zero range and one unique displayed value. This improves reproducibility and UX only; no historical or simulation assumption changed.

## Overall assessment

- Layover monotonicity passed all five adjacent increases in the six-point series.
- Scenario sensitivity monotonicity passed in every evaluated scenario set.
- Short-layover values are explainable by frequent early arrivals in the selected empirical cohort plus the existing transfer/cutoff arithmetic; no hidden airport-specific transfer data is present.
- The representative evening case has a substantially worse p90 and lower probability, consistent with prior upper-tail findings.
- One large fallback-boundary sensitivity (AA CLT-BNA, 26.34 percentage points) should be investigated in a future model-validation task, but the implementation selected the documented cohort correctly.
- No carrier result contradicted its selected empirical distribution; cross-carrier values are confounded by route, time, and small cohort size.
- Deterministic per-itinerary seeding reduces identical-request variation to zero at both raw probability and displayed precision.
- No probability-model bug was found. The frozen V1 behavior remains internally consistent under its documented assumptions, with fallback discontinuity as the main model-validation issue to evaluate before redesign.

## Deterministic serving follow-up

V1 canonicalizes model version, carrier, origin, connection, final destination, travel date, first departure, first arrival, and connecting departure. Codes are uppercase, the date uses ISO `YYYY-MM-DD`, times use zero-padded `HH:MM`, and JSON keys and separators are fixed. SHA-256 is calculated over UTF-8 canonical JSON, and the first eight digest bytes become an unsigned 64-bit NumPy seed. Python's process-randomized `hash()` is not used. Changing the model version intentionally changes the seed.

The simulation still samples the same empirical delay distribution and the same triangular transfer distribution 20,000 times. Only the reproducibility of those draws changed.
