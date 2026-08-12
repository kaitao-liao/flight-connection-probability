# V2 Phase 1 expanded research database

## Scope and artifact boundary

V2 Phase 1 adds a reproducible, research-only historical DuckDB built from the 36 BTS ZIP archives already present under `data/raw/`. It does not read, replace, or alter `data/production/flights_production.duckdb`, and it is not imported by the V1 API, estimator, frontend, or deployment image.

Generated files are Git-ignored:

- `data/processed/flights_v2_research.duckdb`
- `data/processed/v2_research_build_summary.json`

The checked-in implementation is:

- `backend/flight_connection/v2_research_schema.sql` — isolated research schema;
- `backend/flight_connection/v2_research_data.py` — build, lookup, and coverage logic;
- `scripts/v2_lookup_validation.py` — research lookup/validation CLI; and
- `tests/test_v2_research_data.py` — focused synthetic tests.

No terminal, gate, live-flight, or future-schedule data is added.

## Reproducible build

From the repository root, after installing the existing Python project dependencies:

```powershell
.venv\Scripts\python -m backend.flight_connection.v2_research_data `
  --raw-dir data\raw `
  --database data\processed\flights_v2_research.duckdb `
  --summary data\processed\v2_research_build_summary.json `
  --years 2023 2024 2025
```

The command fails if any expected existing monthly archive or required BTS column is absent. By default it recreates only the V2 artifact. `--resume` skips monthly sources already recorded in `v2_data_quality_monthly` and supports recovery after interruption.

Each monthly ZIP is extracted to a temporary directory, scanned by DuckDB with all source columns initially treated as text, normalized into typed fields, audited, and deleted from the temporary directory. A `CHECKPOINT` follows every month. After ingestion, DuckDB creates:

```sql
CREATE INDEX v2_lookup_idx
ON v2_flight_records(reporting_carrier, flight_date, flight_number);
```

The input archives are processed in year/month order. Table row order is not an API contract; lookup results are explicitly ordered by scheduled departure, origin, and destination.

## Core validation and normalization

A row is retained when:

- `FlightDate` parses as a date;
- reporting carrier is two or three uppercase alphanumeric characters after normalization;
- origin and destination are three uppercase letters;
- origin and destination airport IDs parse as integers;
- both scheduled times are valid BTS HHMM values; and
- cancelled and diverted flags are each 0 or 1.

Cancelled and diverted rows are deliberately preserved. Actual movement, delay, elapsed, tail, cancellation, and diversion values may be null when BTS leaves them blank or supplies an invalid optional value.

Normalization rules:

- codes and tail numbers are trimmed and uppercased;
- flight number is trimmed and preserved as text, including any leading zero representation in the source;
- empty text becomes null;
- scheduled and valid actual HHMM values become local minutes after midnight;
- BTS `2400` becomes minute `0` without inventing a date rollover;
- invalid or empty optional HHMM values become null;
- numeric values use `try_cast`, so empty or nonnumeric optional values become null;
- `Cancelled`, `Diverted`, and valid `DivReachedDest` values become booleans;
- all five diversion-leg groups are retained in an ordered JSON array; absent legs are omitted; and
- `source_file` records exact monthly archive lineage.

The database intentionally preserves raw historical local-clock semantics. It does not infer actual departure/arrival dates from HHMM values and does not resolve future schedules.

## Source-to-schema mapping

| BTS source column | V2 column | Type / normalization |
|---|---|---|
| `FlightDate` | `flight_date` | `DATE` |
| `Reporting_Airline` | `reporting_carrier` | uppercase trimmed text |
| `Flight_Number_Reporting_Airline` | `flight_number` | trimmed nullable text |
| `Tail_Number` | `tail_number` | uppercase trimmed nullable text |
| `Origin`, `Dest` | `origin`, `destination` | uppercase IATA text |
| `OriginAirportID`, `DestAirportID` | `origin_airport_id`, `destination_airport_id` | integer |
| `CRSDepTime`, `CRSArrTime` | `crs_departure_minutes`, `crs_arrival_minutes` | required local HHMM → minutes |
| `DepTime`, `ArrTime` | `departure_minutes`, `arrival_minutes` | nullable local HHMM → minutes |
| `DepDelay`, `ArrDelay` | `departure_delay_minutes`, `arrival_delay_minutes` | nullable double |
| `Cancelled`, `Diverted` | `cancelled`, `diverted` | required boolean |
| `CancellationCode` | `cancellation_code` | uppercase nullable text |
| `TaxiOut`, `TaxiIn` | `taxi_out_minutes`, `taxi_in_minutes` | nullable double |
| `WheelsOff`, `WheelsOn` | `wheels_off_minutes`, `wheels_on_minutes` | nullable local HHMM → minutes |
| `CRSElapsedTime` | `crs_elapsed_minutes` | nullable double |
| `ActualElapsedTime` | `actual_elapsed_minutes` | nullable double |
| `AirTime` | `air_time_minutes` | nullable double |
| `Distance` | `distance_miles` | nullable double |
| `CarrierDelay` | `carrier_delay_minutes` | nullable double |
| `WeatherDelay` | `weather_delay_minutes` | nullable double; attributed delay, not weather observation |
| `NASDelay` | `nas_delay_minutes` | nullable double |
| `SecurityDelay` | `security_delay_minutes` | nullable double |
| `LateAircraftDelay` | `late_aircraft_delay_minutes` | nullable double |
| `FirstDepTime` | `first_departure_minutes` | nullable local HHMM → minutes |
| `TotalAddGTime` | `total_additional_ground_minutes` | nullable double |
| `LongestAddGTime` | `longest_additional_ground_minutes` | nullable double |
| `DivAirportLandings` | `diversion_airport_landings` | nullable small integer |
| `DivReachedDest` | `diversion_reached_destination` | nullable boolean |
| `DivActualElapsedTime` | `diversion_actual_elapsed_minutes` | nullable double |
| `DivArrDelay` | `diversion_arrival_delay_minutes` | nullable double |
| `DivDistance` | `diversion_distance_miles` | nullable double |
| generated by builder | `source_file` | exact ZIP filename |

For each `N` from 1 through 5, the following BTS fields are stored as one object in `diversion_details`: `DivNAirport`, `DivNAirportID`, `DivNAirportSeqID`, `DivNWheelsOn`, `DivNTotalGTime`, `DivNLongestGTime`, `DivNWheelsOff`, and `DivNTailNum`. Object keys are `sequence`, `airport`, `airport_id`, `airport_sequence_id`, `wheels_on_minutes`, `total_ground_minutes`, `longest_ground_minutes`, `wheels_off_minutes`, and `tail_number`.

## Completed build result

The full local build completed from the existing archives with:

| Measure | Result |
|---|---:|
| Rows | 20,928,579 |
| Date range | 2023-01-01 through 2025-12-31 |
| Source rows retained | 20,928,579 of 20,928,579 |
| Invalid core rows | 0 |
| Missing flight number | 1 |
| Cancelled rows | 287,134 |
| Diverted rows | 53,309 |
| Rows with diversion-leg JSON | 55,277 |
| Artifact size | 1,926,508,544 bytes (1,837.26 MiB / 1.79 GiB) |
| Build runtime in this environment | 225.4 seconds |

Optional-field null counts from the completed artifact include 48,139 tail numbers, 275,298 actual departure times, 292,084 actual arrival times, 340,445 actual elapsed times, and 20,641,445 cancellation codes. These patterns are expected to depend on flight status and field applicability; null does not imply a failed build.

The database contains `v2_flight_records`, `v2_data_quality_monthly`, and the `v2_lookup_idx` index. The status cross-check found 20,588,136 rows that are neither cancelled nor diverted; this comprises 20,588,134 completed flights with observed arrival delay plus the two previously audited rows with missing arrival delay.

## Historical lookup validation

Run the default audited example and full key statistics:

```powershell
.venv\Scripts\python -m scripts.v2_lookup_validation
```

Optional disambiguators are available:

```powershell
.venv\Scripts\python -m scripts.v2_lookup_validation `
  --carrier WN --date 2025-09-15 --flight-number 39 `
  --origin GSP --destination BWI --scheduled-departure-minutes 360
```

The lookup returns all matching segments and classifies the filtered result as `no_match`, `unique_match`, or `multiple_segments`. Route and exact scheduled departure minute filters can be used independently or together.

Completed full-artifact statistics, excluding the one null flight number:

| Classification | Observed keys | Share |
|---|---:|---:|
| Unique match | 14,048,799 | 81.179442% |
| Multiple segments | 3,257,059 | 18.820558% |
| Total | 17,305,858 | 100% |

There are 20,928,578 records with a flight number: 14,048,799 records belong to unique keys and 6,879,779 belong to multi-segment keys. Maximum observed multiplicity is eight. Every multi-match key contains distinct origin–destination routes; no repeated same-route rows were found inside these keys.

The completed build corrects the inventory's original key total by one: its first grouping included the one null flight-number row as a key. Excluding null—the behavior required for lookup—reduces both total and unique keys by one and does not change the reported percentages or feasibility conclusion.

### Audited example

`DL + 1234 + 2025-06-15` returns `unique_match` with one segment:

| Route | Scheduled departure | Scheduled arrival | Status | Tail | Departure delay | Arrival delay |
|---|---:|---:|---|---|---:|---:|
| ATL–MIA | 20:52 local | 22:47 local | completed | N687DL | +73 min | +68 min |

This agrees with the inventory's route and scheduled-departure result while adding fields that were not retained in the previous full database.

## Tests

Focused tests cover:

- scheduled and optional HHMM parsing, including `2400 → 0`;
- cancelled and diverted record preservation;
- cancellation and diversion-detail normalization;
- no-match, unique-match, and multi-segment classification;
- route and scheduled-time disambiguation; and
- invalid disambiguation time rejection.

Run them with:

```powershell
.venv\Scripts\python -m pytest -q tests\test_v2_research_data.py
```

## Known limitations

- This is historical BTS data, not a future schedule or live status source.
- Reporting carrier is not a complete marketing/operating carrier or codeshare identity model.
- Carrier/date/flight-number is ambiguous for 18.82% of observed keys because one flight number can cover sequential legs.
- Local HHMM values do not encode timezone offsets, DST folds, or their actual calendar rollover.
- Tail number is historical and does not imply future aircraft assignment or aircraft type.
- BTS delay-cause fields are conditional attributed minutes; `WeatherDelay` is not measured weather.
- Diversion fields are sparse and apply only to affected records.
- Airport IDs, distance, and operational times do not provide passenger transfer times.
- There is no terminal or gate data and no airport-specific transfer-time evidence.
- The 1.79 GiB research artifact is intentionally excluded from Git and deployment.
