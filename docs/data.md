# BTS data source, schema, and preprocessing

## Official source

- BTS TranStats table: **Reporting Carrier On-Time Performance (1987-present)**  
  https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ
- Official field dictionary:  
  https://www.transtats.bts.gov/Fields.asp?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ
- Official monthly ZIP directory:  
  https://transtats.bts.gov/PREZIP/
- Filename pattern:  
  `On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{YEAR}_{MONTH}.zip`

The current historical foundation contains all 36 monthly reporting-carrier files for 2023, 2024, and 2025. No mirror or commercial flight-data source is used. Downloads are atomic: a file is written as `.part`, validated as a ZIP, and then renamed. Interrupted or corrupt downloads are replaced automatically.

Raw ZIPs, generated DuckDB databases, and JSON build reports are excluded from Git.

## Airport timezone reference data

Request validation uses the offline airport database distributed by
[`airportsdata==20260803`](https://pypi.org/project/airportsdata/20260803/). The package is
MIT licensed and provides IATA codes, ISO country codes, and IANA-compliant `tz` names. The
project pins the package version in `pyproject.toml` for reproducibility. At runtime, only IATA
airports in the United States and included U.S. domestic territories are loaded; an absent code
is rejected instead of assigned a guessed timezone. Python's standard-library `zoneinfo` applies
the installed IANA timezone rules, including daylight saving transitions.

Timezone metadata is used solely for input chronology validation. It is not observed BTS flight
performance, is not an airport transfer-time measurement, and is not an input to the empirical
delay distribution or Monte Carlo probability calculation.

The frontend autocomplete artifact is generated with:

```powershell
.venv\Scripts\python scripts\generate_supported_airports.py
```

`frontend/data/supported-airports.json` is the intersection of all distinct origin/destination
codes in the current production `historical_flights` table and supported `airportsdata` records.
The current artifact contains 362 airports and only the fields needed for display and search:
IATA `code`, `city`, and airport `name`. This keeps unsupported airports out of the selector and
avoids sending the full offline metadata package to browsers.

## Retained fields

| Official BTS field | Clean field | Meaning/use |
|---|---|---|
| `FlightDate` | `flight_date` | Local scheduled flight date |
| `DayOfWeek` | validation input for `day_of_week` | Source weekday is checked against the ISO weekday derived from `FlightDate` |
| `Reporting_Airline` | `reporting_carrier` | Reporting/operating carrier code |
| `Flight_Number_Reporting_Airline` | `flight_number` | Reporting carrier flight number |
| `Origin` | `origin` | Three-letter origin airport code |
| `Dest` | `destination` | Three-letter destination airport code |
| `CRSDepTime` | `crs_departure_minutes` | Scheduled local departure HHMM converted to minutes after midnight |
| `CRSArrTime` | `crs_arrival_minutes` | Scheduled local arrival HHMM converted to minutes after midnight |
| `DepDelay` | `departure_delay_minutes` | Actual minus scheduled departure, in minutes |
| `ArrDelay` | `arrival_delay_minutes` | Actual minus scheduled arrival, in minutes; negative values are retained |
| `Cancelled` | `cancelled` | BTS cancellation flag |
| `Diverted` | `diverted` | BTS diversion flag |
| `CarrierDelay` | `carrier_delay_minutes` | BTS-reported carrier delay component when available |
| `WeatherDelay` | `weather_delay_minutes` | BTS-reported weather delay component when available |
| `NASDelay` | `nas_delay_minutes` | BTS-reported National Airspace System delay component when available |
| `SecurityDelay` | `security_delay_minutes` | BTS-reported security delay component when available |
| `LateAircraftDelay` | `late_aircraft_delay_minutes` | BTS-reported late-arriving-aircraft component when available |

`year`, `month`, and ISO `day_of_week` are derived from `FlightDate`; `departure_time_bucket` is derived from scheduled departure. The source-provided `DayOfWeek` is range-validated and must match the derived value. Unneeded location IDs, tail numbers, actual movement times, taxi fields, and diversion-detail columns are not retained.

## Tables and modeling separation

- `flight_records` contains every record that passes core field validation, including completed, cancelled, diverted, and unexplained missing-arrival-delay records.
- `historical_flights` contains only completed, non-diverted records with an observed arrival delay. It is the table queried by the empirical delay model.
- `data_quality_monthly` contains source and cleaning counts for every monthly ZIP.

Duplicate checks use flight date, reporting carrier, flight number, origin, destination, and scheduled departure time. `historical_flights` deterministically removes duplicate excess records; `flight_records` preserves them for audit. No duplicate excess rows were found in the 2023–2025 build.

## Cleaning and validation

1. Validate the required official header fields.
2. Parse `FlightDate`; derive year and month.
3. Require carrier codes matching two or three ASCII letters/digits.
4. Require airport codes matching exactly three ASCII letters.
5. Parse BTS local HHMM values, accept `2400` as midnight, and reject invalid hours/minutes.
6. Require `DayOfWeek` in 1–7 and cancellation/diversion flags in `{0,1}`.
7. Normalize carrier and airport codes to uppercase.
8. Derive departure buckets: overnight `[00:00,06:00)`, morning `[06:00,12:00)`, afternoon `[12:00,18:00)`, evening `[18:00,24:00)`.
9. Assign explicit status with precedence: cancelled, diverted, missing arrival delay, completed.
10. Preserve negative delays. Flag `abs(ArrDelay) > 1,440` as an outlier but do not remove it.
11. Record source, cleaned, status, missing, invalid, inconsistent-flag, and outlier counts per file.

The pipeline uses DuckDB CSV scanning and SQL transformations on one extracted monthly CSV at a time. It never loads the complete multi-year dataset into pandas or Python memory.

## Build commands

Build the full database only:

```powershell
python -m backend.flight_connection.data_pipeline --mode full --years 2023 2024 2025
```

Create or replace the deterministic development database from an existing full database:

```powershell
python -m backend.flight_connection.data_pipeline --mode development --years 2023 2024 2025
```

Build both in one command:

```powershell
python -m backend.flight_connection.data_pipeline --mode both --years 2023 2024 2025
```

Add `--resume` to a full/both build after interruption. Completed source files are detected through `data_quality_monthly` and skipped.

## Development sampling

The development dataset is not a file prefix. It partitions records by year, month, reporting carrier, and departure-time bucket. Within each stratum it orders records by a deterministic DuckDB hash of date, flight number, route, and departure time, then retains 25 rows. This preserves carrier/month/time coverage, naturally samples flight statuses within strata, and diversifies routes without a random runtime seed.

Current development database:

- 51,979 total records
- 50,985 completed records
- 873 cancelled records
- 121 diverted records
- 15 reporting carriers
- 5,522 distinct directional routes
- 2,895,872 bytes

## 2023–2025 data quality results

| Year | Cleaned | Completed | Cancelled | Diverted | Unexplained missing arrival delay | Arrival-delay outliers |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 6,847,899 | 6,743,403 | 87,943 | 16,552 | 1 | 537 |
| 2024 | 7,079,061 | 6,965,247 | 96,315 | 17,499 | 0 | 567 |
| 2025 | 7,001,619 | 6,879,484 | 102,876 | 19,258 | 1 | 643 |
| **Total** | **20,928,579** | **20,588,134** | **287,134** | **53,309** | **2** | **1,747** |

Across all files, source rows equal cleaned rows, invalid core-field rows are zero, inconsistent simultaneous cancellation/diversion flags are zero, and duplicate excess rows are zero. There are 340,445 total rows without arrival delay: 287,134 cancellations, 53,309 diversions, and two otherwise unexplained records.

The full database is 712,519,680 bytes. The measured clean build, including download of missing official ZIPs and development sampling, took 405.46 seconds on the development machine. The 36 compressed raw ZIPs total 1,049,533,684 bytes. Runtime and storage will vary by system and DuckDB version.

## Coverage and distribution analysis

Run:

```powershell
python -m backend.flight_connection.analyze_data
```

The generated findings are in [generated_data_analysis.md](generated_data_analysis.md). The fixed 100-itinerary evaluation selected exact cohorts 25%, route/carrier/month/time-bucket cohorts 70%, and route/carrier/season cohorts 5%. It never fell back to route-only, carrier-only, or global cohorts in that sample.

The three representative routes all have at least 30 exact observations for the fixed August/weekday/afternoon query, and thousands of carrier-route observations. This demonstrates that the full database supports route-specific inference for common routes, but it does not prove coverage for every airport pair or rare carrier.

Year-over-year results show route-specific shifts. Most notably, DL ATL–JFK p90 increased from 40 minutes in 2024 to 61 minutes in 2025; UA ORD–DEN median/p75/p90 also moved later from 2023 to 2025; AA DFW–LAX p75 and p90 increased across the period. These descriptive differences warrant later temporal validation and potentially rolling lookback or recency features. The model is not automatically reweighted in this phase.

## Known limitations

- Reporting carrier and marketing carrier are not interchangeable for codeshares.
- Delay-cause fields are generally populated only for sufficiently delayed flights and must not be interpreted as complete causal measurements.
- Airport-code format checks do not prove that every code is an active U.S. airport on the flight date.
- Extreme delays remain in the modeling table. Their influence should be evaluated rather than silently trimmed.
- The fixed fallback evaluation contains 100 deterministic itineraries; broader validation is still required.
- The historical estimator does not yet restrict observations to dates before a prediction date or apply recency weighting.
- This data foundation does not make the connection probability production-ready.
