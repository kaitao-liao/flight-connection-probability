# V2 data inventory

## Scope

This is a read-only inventory of the V1 repository as it existed after the `v1.0.0` freeze. No schema, data, estimator, API, frontend, test, or deployment change was made. Database queries used read-only DuckDB connections, and no external API or additional dataset was called or downloaded.

The inventory distinguishes the deployed serving database from the larger local research database. A field present in `flights_full.duckdb` is not automatically available to the production API.

## 1. Production DuckDB

Inspected artifact: `data/production/flights_production.duckdb`.

It contains one table:

| Table | Rows | Date range | Purpose |
|---|---:|---|---|
| `historical_flights` | 20,588,134 | 2023-01-01 through 2025-12-31 | Exact V1 serving projection of completed, non-diverted flights with observed arrival delay |

### Production columns

| Column | DuckDB type | Nulls | Null rate |
|---|---|---:|---:|
| `flight_date` | `DATE` | 0 | 0% |
| `month` | `TINYINT` | 0 | 0% |
| `day_of_week` | `TINYINT` | 0 | 0% |
| `reporting_carrier` | `VARCHAR` | 0 | 0% |
| `origin` | `VARCHAR` | 0 | 0% |
| `destination` | `VARCHAR` | 0 | 0% |
| `departure_time_bucket` | `VARCHAR` | 0 | 0% |
| `arrival_delay_minutes` | `DOUBLE` | 0 | 0% |

The production projection therefore has flight date, reporting carrier, origin IATA, destination IATA, a derived scheduled departure **bucket**, and arrival delay. It does **not** have:

- flight number or tail number;
- origin or destination airport ID;
- exact scheduled departure or arrival time;
- actual departure or arrival time;
- departure delay;
- cancellation or diversion indicators;
- scheduled or actual elapsed time; or
- distance.

This narrow projection is intentional for exact V1 serving and cannot answer flight-number lookup by itself.

## 2. Other existing local DuckDB data

The Git-ignored full research database was also inspected because it establishes what can be reused locally without downloading BTS again.

### `data/processed/flights_full.duckdb`

| Table | Rows | Date range |
|---|---:|---|
| `flight_records` | 20,928,579 | 2023-01-01 through 2025-12-31 |
| `historical_flights` | 20,588,134 | 2023-01-01 through 2025-12-31 |
| `data_quality_monthly` | 36 | One audit row per monthly source archive |

`flight_records` preserves completed, cancelled, diverted, and two otherwise missing-arrival-delay records. Its columns are:

| Column | Type | Null rate | V2 relevance |
|---|---|---:|---|
| `flight_date` | `DATE` | 0% | Historical lookup date |
| `year` | `SMALLINT` | 0% | Derived calendar field |
| `month` | `TINYINT` | 0% | Cohorting |
| `day_of_week` | `TINYINT` | 0% | Cohorting |
| `reporting_carrier` | `VARCHAR` | 0% | Historical lookup carrier |
| `flight_number` | `VARCHAR` | 0.000005% (1 row) | Historical lookup candidate |
| `origin` | `VARCHAR` | 0% | Route disambiguation |
| `destination` | `VARCHAR` | 0% | Route disambiguation |
| `crs_departure_minutes` | `SMALLINT` | 0% | Exact scheduled local departure |
| `crs_arrival_minutes` | `SMALLINT` | 0% | Exact scheduled local arrival |
| `departure_time_bucket` | `VARCHAR` | 0% | Existing V1 cohort field |
| `departure_delay_minutes` | `DOUBLE` | 1.319201% | Historical departure performance |
| `arrival_delay_minutes` | `DOUBLE` | 1.626699% | Historical arrival performance |
| `cancelled` | `BOOLEAN` | 0% | Historical status modeling opportunity |
| `diverted` | `BOOLEAN` | 0% | Historical status modeling opportunity |
| `flight_status` | `VARCHAR` | 0% | Completed/cancelled/diverted/missing status |
| `carrier_delay_minutes` | `DOUBLE` | 79.113250% | BTS delay-cause field; populated only where BTS reports cause minutes |
| `weather_delay_minutes` | `DOUBLE` | 79.113250% | BTS delay-cause field, not a weather observation |
| `nas_delay_minutes` | `DOUBLE` | 79.113250% | BTS delay-cause field |
| `security_delay_minutes` | `DOUBLE` | 79.113250% | BTS delay-cause field |
| `late_aircraft_delay_minutes` | `DOUBLE` | 79.113250% | BTS delay-cause field |
| `arrival_delay_outlier` | `BOOLEAN` | 0% | Audit flag; observations are not silently removed |
| `source_file` | `VARCHAR` | 0% | Data lineage |

Status totals are 20,588,134 completed, 287,134 cancelled, 53,309 diverted, and 2 with otherwise missing arrival delay. The full database contains 15 reporting carriers and 362 distinct airports across origin and destination.

The full database still does not retain flight tail number, airport IDs, actual movement times, elapsed time, or distance, even though several exist in the downloaded BTS source files.

## 3. Flight-number feasibility

### Production feasibility

No: the production DuckDB does not contain `flight_number`. Supporting lookup from the deployed serving artifact would require a new V2 artifact/schema; V1 should remain unchanged.

### Existing full-data feasibility

The full research database contains `flight_number` for all but 1 of 20,928,579 rows. It can identify historical candidates by `reporting_carrier + flight_number + flight_date`, but that key is not always unique.

The audit grouped every actual key in 2023–2025:

| Match count for an observed key | Keys | Share of observed keys |
|---|---:|---:|
| Exactly one | 14,048,799 | 81.18% |
| Multiple | 3,257,059 | 18.82% |
| No match | 0 | 0% by construction |
| **Total observed non-null keys** | **17,305,858** | **100%** |

The zero no-match count applies only to an evaluation universe made from keys known to occur in the database. The no-match rate for arbitrary user queries cannot be estimated without a representative query or schedule distribution; dates outside 2023–2025, invalid combinations, and flights not reported under the supplied carrier would naturally return no rows.

Record-weighted, 14,048,799 rows (67.13%) belong to unique keys and 6,879,779 rows (32.87%) belong to multi-match keys. One row has a null flight number and is excluded from lookup statistics. Observed multiplicity ranged from 1 to 8. The earlier inventory query counted that null as a grouped key; the completed V2 build correctly excludes it, reducing the key and unique counts by one without changing the rounded percentages or ambiguity conclusion.

All 3,257,059 multi-match keys consisted of distinct origin–destination legs; none represented repeated rows for one route. The cause is through-flight/flight-number reuse: a reporting carrier can operate several sequential segments under the same flight number on the same date. For example, WN 39 appears as six sequential legs on representative September 2025 dates. Therefore a practical historical lookup must return candidates or use origin, destination, and preferably scheduled departure time to disambiguate.

The requested example, `DL + 1234 + 2025-06-15`, has exactly one local match: ATL–MIA, scheduled departure minute 1252 (20:52 local), status completed.

### Future-date lookup requirement

Historical BTS records cannot resolve a future flight number to a future itinerary. Future lookup requires a dated schedule source containing, at minimum, operating/reporting carrier, flight number, origin, destination, and scheduled local or UTC times. It also needs a defined treatment of marketing versus operating/reporting carrier and codeshares. A static or periodically refreshed public schedule dataset may be sufficient for planned flights; live status requires a live provider.

## 4. Airport data inventory

`frontend/data/supported-airports.json` contains 362 supported airports. Each browser record has exactly:

- IATA `code`;
- `city`; and
- airport `name`.

The artifact is generated from airports present in BTS history intersected with the pinned offline `airportsdata` source and supported U.S./territory rules. The underlying installed `airportsdata` records provide the following coverage for those same 362 airports:

| Field | Browser JSON | Offline source coverage |
|---|---|---:|
| IATA code | Yes | 362/362 |
| Airport name | Yes | 362/362 |
| City | Yes | 362/362 |
| State/subdivision | No | 359/362 |
| Country/territory code | No | 362/362 |
| Latitude | No | 362/362 |
| Longitude | No | 362/362 |
| IANA timezone | No | 362/362 |
| Terminal | No | 0/362 |
| Gate | No | 0/362 |

Timezone metadata is used server-side through the pinned offline dependency but is deliberately not shipped in the lightweight frontend artifact. Latitude/longitude and country are locally available from the same dependency and could be materialized in a separate V2 artifact without a paid API. State/subdivision is missing for 3 supported airports and would need explicit handling rather than silent assumptions.

## 5. Carrier data inventory

`frontend/data/supported-carriers.json` contains 15 records with `code` and human-readable `name`. Its codes exactly cover the 15 distinct `reporting_carrier` values in the full production history. The generator joins production codes to maintained names from the official BTS Unique Carrier lookup and fails if a code lacks a name, so the present mapping is complete and explicit.

This is a reporting-carrier mapping. It is not a complete marketing-carrier, operating-carrier, codeshare, or historical-brand identity model, which matters for consumer flight-number search.

## 6. Terminal and gate availability

Repository source, schemas, generated artifacts, DuckDB tables, documentation, and one original 110-field BTS archive header were searched for terminal and gate data. No scheduled or actual terminal/gate field exists locally. The only gate references are descriptions of the V1 assumed gate-to-gate transfer time and explicit statements that gate assignments are not modeled.

The BTS On-Time Performance extract does not supply scheduled or real-time passenger terminal/gate assignments. Consequently:

| Need | Existing repository/BTS data sufficient? | Additional source needed? |
|---|---|---|
| Scheduled terminal | No | Yes; schedule/airport/airline data source |
| Scheduled gate | No | Yes; schedule/airport/airline data source |
| Real-time terminal | No | Yes; live operational API/feed |
| Real-time gate | No | Yes; live operational API/feed |

Airport-level transfer modeling does not inherently require flight gates. A V2 model could use airport identity, geometry, public terminal maps, connection type, and transparently assumed airport-level distributions. Terminal-pair or gate-pair modeling does require terminal/gate data plus defensible transfer-time evidence; coordinates alone do not measure passenger movement time.

## 7. BTS source-data opportunity

The existing 36 downloaded BTS archives contain 110 columns. The current pipeline intentionally selects a smaller subset, and the production artifact projects only the eight V1 serving fields.

Useful fields already present in the downloaded source but not retained in the full DuckDB include:

- `Tail_Number`;
- `OriginAirportID`, `OriginAirportSeqID`, and origin city/state identifiers and names;
- `DestAirportID`, `DestAirportSeqID`, and destination city/state identifiers and names;
- actual `DepTime` and `ArrTime`;
- `TaxiOut`, `WheelsOff`, `WheelsOn`, and `TaxiIn`;
- `CRSElapsedTime`, `ActualElapsedTime`, and `AirTime`;
- `Distance` and `DistanceGroup`;
- `CancellationCode`;
- departure/arrival delay groups and 15-minute indicators;
- diversion landing, alternate-airport, elapsed-time, ground-time, distance, and diverted-tail details; and
- carrier DOT/IATA identifiers.

The full DuckDB already retains flight number, exact scheduled departure/arrival minutes, departure/arrival delay, status flags, and five BTS-reported delay-cause minute fields. The source also includes these and therefore does not require a new download to prototype an expanded local V2 build, provided the existing raw archives remain available and their exact lineage is preserved.

Useful qualifications:

- Actual departure/arrival times are historical actuals, not a future schedule.
- `WeatherDelay` is an attributed delay component, not measured weather conditions.
- Tail number can support aircraft-history research but is not guaranteed to identify aircraft type or a future assignment.
- Airport IDs and coordinates help identity and distance features; they do not provide terminals, gates, or transfer time.

## 8. V2 data gap analysis

| Category | Data | Already available locally? | Source | Quality/coverage | Needed for V2? |
|---|---|---|---|---|---|
| A | Historical arrival delay | Yes; production and full DB | BTS | 20,588,134 completed, non-diverted rows, 2023–2025 | Core existing evidence |
| A | Historical flight number | Yes in full DB; no in production | BTS | One null in 20,928,579 records; key alone is multi-leg for 18.82% of observed keys | Yes for historical lookup |
| A | Historical exact schedule | Yes in full DB; no in production | BTS `CRSDepTime`/`CRSArrTime` | 0% null after pipeline validation | Yes for disambiguation and schedule features |
| A | Airport name/city/IATA | Yes | Generated JSON + `airportsdata` | 362 supported airports; complete for these fields | Yes |
| A | IANA timezone | Yes offline, not browser JSON/DB | `airportsdata` | 362/362 supported airports | Yes for chronology |
| A | Cancellation/diversion | Yes in full `flight_records`; no in production | BTS | 287,134 cancellations and 53,309 diversions | Candidate V2 outcome/risk extension |
| A | Historical delay causes | Yes in full DB | BTS | 79.11% null by BTS applicability/reporting; weather delay is not weather data | Optional diagnostic features |
| B | Tail number | In raw archives, not DuckDB | Existing BTS files | Historical field; future aircraft assignment not guaranteed | Optional research feature |
| B | Airport IDs and city/state fields | In raw archives, not DuckDB | Existing BTS files | Broad historical coverage; stable identity aid | Useful for richer schema |
| B | Actual movement/taxi/wheels times | In raw archives, not DuckDB | Existing BTS files | Historical operational data; null/status behavior needs a dedicated audit | Useful for delay decomposition, not future lookup |
| B | Elapsed time and distance | In raw archives, not DuckDB | Existing BTS files | Historical schedule/actual fields; needs cleaning audit | Useful route features |
| B | Cancellation code/diversion details | In raw archives, not DuckDB | Existing BTS files | Applicable only to affected flights | Useful for explicit disruption research |
| C | Airport coordinates/country | Yes in dependency, not current JSON | Existing free `airportsdata` | 362/362 for lat/lon/country | Useful for airport/route features |
| C | Airport-specific transfer time | No | Requires a defensible public dataset, airport study, or transparent research assumptions | No measured local source identified | Needed for airport-specific passenger modeling |
| C | Terminal layout/connectivity | No | Public airport maps/open geospatial data where licensing and coverage permit | Likely heterogeneous and incomplete | Needed for terminal-aware modeling |
| D | Future flight-number schedule | No | Static/periodic public schedule source or schedule API | Coverage, codeshares, and update cadence must be evaluated | Required for future lookup |
| D | Scheduled terminal/gate | No | Schedule/airline/airport provider | Not in BTS On-Time data | Required only for terminal/gate-aware itinerary resolution |
| D | Real-time terminal/gate | No | Live operational API/feed | Time-sensitive and provider-dependent | Required for live gate-aware estimates |
| D | Live flight status | No | Live flight API/feed | Not available from historical BTS | Required only for real-time V2 behavior |
| C/D | Weather | No weather observations; only BTS-attributed delay minutes | Free historical sources such as NOAA are possible; live weather requires a current feed/API | Requires spatial/temporal joining and separate validation | Optional future feature, not Phase 1 |

Category definitions:

- **A — Already available locally:** present in a current database or checked-in artifact/dependency.
- **B — Available in the existing free BTS downloads but not retained:** can be rebuilt from current raw archives without a new provider.
- **C — Requires another public dataset:** potentially offline/free, subject to coverage, licensing, and validation.
- **D — Likely requires an external/live API:** future schedules or operational state that historical BTS cannot supply.

## 9. Recommendation

### Direct answers

1. **Can existing data support historical flight-number lookup?** Yes, using the full research database, but not the production serving database. `carrier + flight number + date` uniquely identifies 81.18% of observed keys and returns multiple route legs for 18.82%. A lookup must include origin/destination or present candidates. One row lacks a flight number and must be excluded or handled explicitly.
2. **What is required for future flight-number lookup?** A dated future schedule source with operating/reporting carrier, flight number, route, and scheduled times, plus explicit marketing/operating carrier and codeshare rules. Historical BTS alone cannot identify future schedules.
3. **Can airport-specific transfer modeling be built without a paid flight API?** A research-grade airport-level model can be prototyped without one by combining the existing airport identities/timezones/coordinates with responsibly licensed public airport-layout or transfer-study data and explicit assumptions. The current repository has no measured airport-specific transfer times, so it cannot be built from existing evidence alone. Gate-aware or real-time estimates are a separate requirement.
4. **What requires terminal/gate data?** Terminal-pair and gate-pair walking/connection models, terminal-change penalties, and operational gate-aware estimates. A coarse airport-level distribution does not require a flight API, but must not be represented as measured gate-level behavior.

### Recommended V2 Phase 1

Build a **separate research-only expanded historical data artifact and lookup analysis**, without changing V1 production serving:

1. define a V2 schema sourced from the already-downloaded BTS archives;
2. retain flight number, exact scheduled times, airport IDs, distance, elapsed-time fields, actual movement times, tail number, and disruption details with measured null/quality rules;
3. implement and validate an offline historical lookup key using carrier + flight number + date, returning/disambiguating route legs with origin, destination, and scheduled departure;
4. quantify coverage, ambiguity, carrier-code behavior, and temporal consistency before designing any public API; and
5. separately evaluate free future-schedule and airport-layout sources, licensing, refresh cadence, and coverage before committing to future lookup or terminal-aware modeling.

This phase uses data already owned by the project, resolves the clearest feasibility question, and preserves the frozen V1 system. It should not begin with live APIs, terminal/gate integration, or a new probability model.
