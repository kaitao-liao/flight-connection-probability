# Revised V1 Sensitivity Audit

## Current model

The revised V1 condition is:

```text
arrival delay
+ 20-minute fixed deplaning time
+ Triangular(15, 25, 40)-minute gate-to-gate transfer time
+ 15-minute boarding cutoff
<= scheduled layover
```

Only arrival delay is empirical BTS evidence. Deplaning, gate transfer, and boarding cutoff are modeling assumptions; they are not observed passenger-movement data. V1 still uses 20,000 simulations and deterministic per-itinerary SHA-256 seeds.

The theoretical passenger requirement before arrival delay is 50 minutes minimum, 60 minutes at the transfer mode, and 75 minutes maximum.

## Updated 85-minute sensitivity trace

The representative itinerary is DL ATL-JFK-BOS on 2025-07-15 with an 85-minute scheduled layover. It selected the route/carrier/month/bucket cohort with 140 observations and seed `17079904618873731818`.

The 20,000 gate-transfer draws had the following values:

| Statistic | Minutes |
|---|---:|
| Simulated minimum | 15.079 |
| Median | 26.310 |
| P75 | 30.230 |
| P90 | 33.737 |
| P95 | 35.605 |
| Simulated maximum | 39.869 |

| Fixed arrival delay | Time after arrival delay | Gate-transfer threshold after deplaning and cutoff | Successful draws | Probability |
|---:|---:|---:|---:|---:|
| 0 min | 85 min | 50 min | 20,000 / 20,000 | 100.00% |
| 15 min | 70 min | 35 min | 18,699 / 20,000 | 93.50% |
| 30 min | 55 min | 20 min | 1,962 / 20,000 | 9.81% |
| 45 min | 40 min | 5 min | 0 / 20,000 | 0.00% |

Each value was independently reconstructed from the exact seeded RNG sequence and matched the service response. The simulator first samples the historical delays used by the overall estimate, then samples the gate-transfer array reused by all four scenarios.

There is no clipping and no conditional logic that forces 0% or 100%. The bounded triangular support explains the endpoints: a gate-transfer threshold of at least 40 minutes guarantees success, while a threshold below 15 minutes makes success impossible. The 15-minute boarding cutoff and 20-minute deplaning assumption are each applied exactly once.

## Interpretation

Under the superseded model, an 85-minute layover at +30 minutes left a 40-minute threshold for a transfer bounded above by 35, so it produced 100%. Under revised V1, the same +30 scenario must first reserve 20 minutes for deplaning; only 20 minutes remain for a gate transfer distributed from 15 to 40 minutes. The resulting probability is therefore near the triangular CDF at 20 minutes, theoretically 10%, and the simulation returns 9.81%.

The sensitivity display now clarifies that these are probabilities conditional on exact fixed arrival delays and that passenger transfer remains simulated. No implementation bug was found after the revision.
