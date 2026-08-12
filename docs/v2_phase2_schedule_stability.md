# V2 Phase 2: Historical Flight-Number and Schedule Stability Research

## Objective and scope

This research prototype asks whether the 2023–2025 BTS observations can support a
small historical flight-number lookup artifact and whether schedules can be represented
as date ranges without hiding route, operating-day, or scheduled-time changes. It does
not change V1, provide future schedules, or use an external API.

The source is the Phase 1 `v2_flight_records` table: 20,928,579 records from
2023-01-01 through 2025-12-31. One record has a null flight number and is intentionally
absent from flight-number lookup artifacts. Generated databases and the JSON summary
remain under `data/processed/`, which is Git-ignored.

## Reproduce the analysis

From the repository root, after building the Phase 1 research database:

```powershell
python -m backend.flight_connection.v2_schedule_analysis --benchmark-repetitions 200
```

This creates:

- `data/processed/flights_v2_lookup.duckdb`
- `data/processed/flights_v2_schedule_patterns.duckdb`
- `data/processed/v2_phase2_summary.json`

The build is deterministic. The measured end-to-end runtime on the development machine
was 183.824 seconds. Runtime and latency numbers are machine-specific.

## Historical lookup behavior

`lookup_exact()` accepts carrier, flight date, and flight number, plus optional origin,
destination, and exact scheduled-departure minute. Inputs are normalized, validated,
and queried against `exact_flights`. Results are ordered by scheduled departure,
scheduled arrival, origin, and destination and classified as `no_match`,
`unique_match`, or `multiple_segments`. The function never guesses among segments.

The audited example `DL + 1234 + 2025-06-15` is a unique match:

| Origin | Destination | Scheduled departure | Scheduled arrival |
| --- | --- | ---: | ---: |
| ATL | MIA | 20:52 | 22:47 |

`WN + 39 + 2025-09-15` returns six segments in deterministic order: GSP–BWI,
BWI–ABQ, ABQ–DAL, DAL–HOU, HOU–STL, and STL–MSP. Supplying origin `BWI` (or its
09:50 scheduled departure) returns only BWI–ABQ. This confirms that route and time can
disambiguate a through-flight number without suppressing valid candidates.

## Route stability

The unit is carrier plus flight number, across the complete three-year observation
window. Route counts are distinct origin–destination pairs. The dominant-route share
is the number of dates on which that route appears divided by all operating dates. A
through flight may therefore have multiple routes whose individual shares are 100%.

| Measure | Result |
| --- | ---: |
| Carrier/flight-number combinations | 37,374 |
| Always one observed route | 5,359 (14.339%) |
| Multiple observed routes | 32,015 (85.661%) |
| Dominant route share >=90% | 9,119 (24.399%) |
| Dominant route share >=95% | 7,817 (20.916%) |
| Dominant route share >=99% | 6,481 (17.341%) |
| Dominant route share 100% | 5,816 (15.562%) |
| Same-date multi-segment operation | 18,582 (49.719%) |
| Median / P90 distinct routes | 9 / 38 |
| Median / P90 route-set changes | 12 / 128 |

These results make a single permanent route per flight number unsafe. The high
multi-route rate is partly explained by legitimate same-date through service, but route
reuse over months and years is also substantial.

To describe, not assert, the character of changing assignments, a diagnostic heuristic
was applied to route assignments belonging to multi-route flight numbers. A route was
called temporary/sparse when it had at most 30 dates or a span of at most 60 days;
seasonally recurring when it appeared in at least two years but six or fewer calendar
months; and persistent when it had at least 180 dates over at least one year. This yielded
85.897% temporary/sparse, 3.834% seasonally recurring, 2.688% persistent, and 7.580%
indeterminate. These labels are descriptive and sensitive to the three-year window;
they are not schedule truth.

Examples:

- Highly stable: UA 1192 was IAH–SJU on all 1,096 operating dates.
- Route-changing: G4 1600 used 33 routes over 550 dates; AUS–PVU was the largest at
  63 dates (11.455%), with 427 observed daily route-set changes.
- Multi-segment: AS 67 had four segments on every one of 1,094 operating dates. Its
  dominant route KTN–SIT therefore has a 100% date share without implying a one-route
  flight number.

## Scheduled-time stability

The unit is carrier, flight number, origin, and destination. Scheduled departure and
arrival are stored as local minute-of-day values. Deviations use circular clock distance,
so 23:55 and 00:05 are 10 minutes apart, not 1,430 minutes apart.

| Measure | Result |
| --- | ---: |
| Flight-number/route combinations | 551,612 |
| One scheduled departure/arrival pair | 307,893 (55.817%) |
| Multiple scheduled time pairs | 243,719 (44.183%) |
| Modal pair share >=90% | 326,297 (59.153%) |
| Modal pair share >=95% | 316,658 (57.406%) |
| Modal pair share >=99% | 307,912 (55.820%) |
| Median / P90 distinct time pairs | 1 / 5 |
| Median of route-level median departure deviation | 0 minutes |
| P90 of route-level P90 departure deviation | 170 minutes |
| Median / P90 observed schedule changes | 0 / 20 |

The modal values are the most frequent departure/arrival pair, with deterministic
minute ordering for ties. WN 311 DAL–ECP is a deliberately unstable example: 1,025
records, 77 time pairs, modal 14:10–15:55 with only 4.390% share, median circular
departure deviation 215 minutes, P90 310 minutes, and 598 date-to-date time changes.

A global modal time pair reproduced 41.577% of source observations. Conditioning the
mode on weekday raised this to 47.620% (+6.043 percentage points), month to 67.030%
(+25.454 points), and year to 47.380% (+5.803 points). Weekday masks are useful, but
month/date-period schedule changes explain much more variation than weekday alone.
A single permanent scheduled time is therefore unsafe.

## Operating-day and seasonal patterns

The analysis estimates active weekdays by requiring operation on at least half of the
eligible weekdays between a route's first and last dates. Classification then uses date
span, density, active months, and maximum gap. Counts are at the
carrier/flight-number/route level:

| Pattern | Count | Share |
| --- | ---: | ---: |
| Daily | 8,521 | 1.545% |
| Weekdays only | 76 | 0.014% |
| Weekends only | 7 | 0.001% |
| Selected weekdays | 1,899 | 0.344% |
| Seasonal | 293,746 | 53.252% |
| Irregular | 247,363 | 44.844% |

The large seasonal/irregular shares reflect very fine route-level combinations and
flight-number reuse; they should not be read as percentages of passenger flights.
They demonstrate why periods must not bridge arbitrary gaps.

## Deterministic segmentation

### Exact periods

Each observation is assigned to its Monday-based week. Observations with identical
carrier, flight number, route, scheduled departure, and scheduled arrival form a weekly
signature containing the exact ISO-weekday mask. Adjacent weeks merge only when both
the complete signature and mask are identical. Any time, route, or weekday-mask change
starts a new period, and a missing week prevents merging. Isolated operations remain
one-date periods. This representation is lossless.

### Pattern-oriented periods

The pattern prototype groups identical carrier/flight/route/time signatures while the
gap between consecutive observations is at most 21 days, then uses the union weekday
mask over the resulting range. A gap over 21 days starts a new period, so long seasonal
gaps are not silently bridged. This is intentionally approximate: shorter cancellations,
irregular gaps, weekday-mask evolution, and overlapping historical schedules may produce
extra candidates.

Every false-positive pattern expansion is retained in `pattern_exclusions` with its
date, route, and scheduled times. There are 1,588,133 exclusions and no required
additions. Raw pattern output is suitable for research/autocomplete; exact historical
lookup must use `exact_flights`, `exact_schedule_periods`, or apply the exclusions.

## Artifact and compression results

| Artifact / representation | Rows | Size | Build time | Source-row ratio | Size vs Phase 1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Phase 1 source | 20,928,579 | 1,926,508,544 B (1.794 GiB) | — | 1.00x | 100% |
| Exact lookup database (`exact_flights`) | 20,928,578 | 1,113,337,856 B (1.037 GiB) | 19.478 s | 1.00x | 57.790% |
| Exact schedule periods | 3,497,885 | included below | included below | 5.983x | — |
| Raw pattern periods | 1,737,791 | included below | included below | 12.043x | — |
| Pattern exclusions | 1,588,133 | included below | included below | — | — |
| Period/stability database (all three tables plus analyses) | 6,823,809 representation rows | 445,919,232 B (425.262 MiB) | 95.126 s | 3.067x combined | 23.146% |

The exact lookup artifact removes all Phase 1 operational fields except date, carrier,
flight number, route, and scheduled departure/arrival. The period database retains
those same lookup semantics plus validity ranges, weekday masks, observation counts,
stability summaries, and pattern exclusions. It does not contain actual times, delays,
cancellation/diversion fields, tail number, distance, or delay causes; those remain in
the Phase 1 research database.

## Reconstruction validation

Full-population expansion, not only sampling, established:

| Representation | Expanded rows | Exact matches | False positives | False negatives | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Exact periods | 20,928,578 | 20,928,578 | 0 | 0 | 100% | 100% |
| Raw patterns | 22,516,711 | 20,928,578 | 1,588,133 | 0 | 92.947% | 100% |

Raw-pattern false positives comprise 402,725 non-operating carrier/date/flight-number
keys, 332,107 route candidates on otherwise matching keys, and 853,301 scheduled-time
candidates on matching key/routes. These categories are mutually exclusive here.
Applying `pattern_exclusions` removes all of them.

A second fixed-seed (`20260812`) stratified check sampled 25,326 keys / 29,077 source
segments across carrier, year, month, weekday, unique/multi-segment operation, and
stable/changing schedules. Exact periods returned all 29,077 with zero extra or missing
rows. Raw patterns returned 29,997 rows: all source rows plus 889 distinct extra rows
(the raw join contained 920 extras before set de-duplication). This agrees with the
full-population result.

## Lookup performance

Benchmarks used 200 repetitions per case, warm reused read-only DuckDB connections,
and deterministic sorted results. Values are median / P95 milliseconds:

| Representation | Unique | Six-segment | No match |
| --- | ---: | ---: | ---: |
| Full Phase 1 | 3.765 / 4.358 | 3.686 / 4.163 | 0.694 / 1.044 |
| Exact lookup | 4.003 / 4.586 | 3.237 / 3.904 | 0.674 / 0.937 |
| Raw pattern | 3.562 / 4.948 | 4.042 / 5.345 | 0.827 / 1.219 |

All three are already single-digit-millisecond local queries. The compact artifacts
primarily reduce storage and serving scope; this benchmark does not demonstrate a
universal latency win and excludes process startup, network, serialization, and cold
filesystem effects.

## Limitations and architecture implication

This is historical schedule inference, not future scheduled-flight truth. No 2026+
itinerary may be asserted from these patterns, even when a flight number looks stable.
BTS records what was reported historically and does not provide a current commercial
schedule, terminal, or gate feed.

A future architecture should keep three concerns separate:

1. BTS historical data for lookup research, schedule patterns, and delay performance.
2. A future authoritative schedule provider for current/future carrier/date/flight
   number, route, times, and—only if available—terminal/gate.
3. The probability engine, consuming explicit itinerary inputs and historical delay
   evidence without treating inferred history as a promised future schedule.

## Recommended V2 Phase 3

Design and evaluate a provider-neutral future-schedule adapter contract and data-quality
policy without integrating a provider yet. Define provenance, freshness, ambiguity,
codeshare/operating-carrier handling, segment selection, timezone normalization, and
failure behavior. Keep exact BTS historical lookup as a separate research service and
use pattern periods only for clearly labeled discovery or fallback hints.
