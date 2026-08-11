# Flight Connection Probability

An interpretable MVP API that estimates whether a passenger will make a U.S. domestic-to-domestic flight connection. It combines observed BTS arrival delays with an explicitly assumed airport transfer-time model. It does not use real-time or paid data.

## Backend architecture

- `acquire.py` provides official monthly BTS download and HHMM utilities.
- `data_pipeline.py` builds resumable full and deterministically stratified development DuckDB datasets.
- `analyze_data.py` generates cohort-coverage, fallback-frequency, and year-over-year reports.
- `validation.py` runs strict temporal holdouts, rolling-window comparisons, quantile scoring, status-risk prototypes, and connection calibration replay.
- `stratified_validation.py` runs the larger design-weighted study, bootstrap intervals, subgroup diagnostics, and error-case extraction.
- `upper_tail_experiments.py` provides exact lazy temporal caching and isolated empirical upper-tail candidate experiments.
- `delay_model.py` returns an empirical arrival-delay distribution using documented cohort fallbacks.
- `simulator.py` bootstraps observed delays and samples an assumed transfer-time distribution.
- `schemas.py` defines versioned, frontend-friendly Pydantic request and response contracts.
- `timezone_validation.py` validates supported U.S. airport schedules in UTC using airport-local IANA timezones.
- `service.py` validates itinerary timing, calculates layovers, queries history, and runs the probability simulation.
- `api.py` is a thin FastAPI transport layer. Business logic does not live in route handlers.
- `scripts/example_request.py` submits a development request to a running server.
- `schema.sql` defines the cleaned historical-flight table.
- `docs/data.md` records exact BTS sources, fields, and cleaning lineage.

## Frontend architecture

- `frontend/app/connection-risk-calculator.tsx` owns the form state, client-side validation, loading/error states, and quantitative result presentation.
- `frontend/app/api-client.ts` is the typed HTTP boundary for `POST /api/v1/connection-risk`; it never substitutes mock results when the API fails.
- `frontend/app/globals.css` provides the responsive, accessible card layout without a component framework.
- `frontend/tests/connection-risk-calculator.test.tsx` covers normalization, validation, submission, loading, response formatting, scenarios, fallback warnings, disclaimers, and API errors.

Business and modeling logic remain in Python. The browser only validates basic input shape, submits the itinerary, and presents the backend response.

## Setup and development data

Requires Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m backend.flight_connection.data_pipeline --mode both --years 2023 2024 2025
```

The pipeline downloads 36 official monthly ZIPs to `data/raw/`, creates `data/processed/flights_full.duckdb`, and creates a deterministic representative `data/processed/flights_development.duckdb`. These generated artifacts are excluded from Git. Use `--resume` after interruption. See [the data documentation](docs/data.md) for source URLs, field lineage, actual row counts, quality results, sampling methodology, coverage, and year-over-year findings.

Run the reproducible sampled temporal validation:

```powershell
.venv\Scripts\python -m backend.flight_connection.validation --mode sampled --cases-per-split 50
```

`--mode full` raises the default to 1,000 cases per split and is substantially slower. The completed sampled run, leakage controls, metrics, calibration data, and recommendations are documented in [the temporal validation report](docs/model_validation.md).

Run the larger population-weighted stratified diagnostic study:

```powershell
.venv\Scripts\python -m backend.flight_connection.stratified_validation --delay-cases 500 --connection-cases 300 --bootstrap-replicates 500
```

The completed study, upper-tail localization, confidence intervals, calibration data, and failure cases are documented in [the larger stratified validation report](docs/stratified_validation.md).

Run the leakage-safe accelerated upper-tail candidate experiment:

```powershell
.venv\Scripts\python -m backend.flight_connection.upper_tail_experiments --arrival-cases 2000 --connection-cases 1000 --bootstrap-replicates 300
```

The exact acceleration benchmark, four-candidate comparison, cluster-aware intervals, connection impact, and V1 decision are documented in [the upper-tail experiment report](docs/upper_tail_experiments.md).

## Start the API

From the repository root:

```powershell
.venv\Scripts\python -m uvicorn backend.flight_connection.api:app --reload
```

The default API database is `data/processed/flights_development.duckdb`. Override it when needed:

```powershell
$env:FLIGHT_CONNECTION_DB = "C:\path\to\flights.duckdb"
```

Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Start the frontend

Requires Node.js 22.13 or newer. In a second terminal, from `frontend/`:

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`. The development environment defaults to `http://127.0.0.1:8000`; set the browser-visible API base URL explicitly when needed:

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

The backend allows only `http://localhost:3000` and `http://127.0.0.1:3000` by default. To use another local frontend origin, provide an explicit comma-separated allowlist before starting FastAPI:

```powershell
$env:FLIGHT_CONNECTION_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
```

No wildcard CORS origin is enabled.

## API

`POST /api/v1/connection-risk`

Request:

```json
{
  "carrier": "DL",
  "origin": "ATL",
  "connection": "JFK",
  "destination": "BOS",
  "travel_date": "2026-08-20",
  "first_departure_time": "15:30",
  "first_arrival_time": "17:45",
  "connecting_departure_time": "19:10"
}
```

Representative response shape (values depend on the local BTS subset and random sampling):

```json
{
  "connection_probability": 0.78,
  "scheduled_layover_minutes": 85,
  "overnight_connection": false,
  "historical_sample_size": 1800,
  "delay_statistics": {
    "median_minutes": 7.0,
    "p75_minutes": 18.0,
    "p90_minutes": 38.0
  },
  "scenarios": {
    "on_time": 0.96,
    "delay_15": 0.87,
    "delay_30": 0.62,
    "delay_45": 0.29
  },
  "model": {
    "version": "v1",
    "cohort_level": "route_carrier_month_bucket",
    "arrival_delay_evidence": "observed_completed_non_diverted_BTS_flights",
    "transfer_time": {
      "distribution": "triangular",
      "minimum_minutes": 10.0,
      "mode_minutes": 20.0,
      "maximum_minutes": 35.0,
      "evidence_type": "modeling_assumption"
    },
    "boarding_cutoff_minutes": 15.0,
    "simulation_count": 20000,
    "random_seed": null,
    "exclusions": ["cancelled_flights", "diverted_flights"],
    "historical_coverage": {
      "lookback_months": 24,
      "available_start_date": "2023-01-01",
      "available_end_date": "2025-12-31",
      "requested_prediction_date": "2026-08-20",
      "effective_history_start_date": "2024-08-20",
      "effective_history_end_date": "2025-12-31",
      "strict_cutoff_exclusive": "2026-08-20",
      "freshness_warning": "Historical BTS data ends on 2025-12-31, 232 days before the requested prediction date."
    }
  }
}
```

Run the included example against a running server:

```powershell
.venv\Scripts\python scripts\example_request.py
```

An equivalent PowerShell request is:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/connection-risk -ContentType application/json -Body (Get-Content examples\connection_request.json -Raw)
```

## Methodology

Frozen V1 first restricts history to `flight_date >= travel_date - 24 calendar months` and `flight_date < travel_date`. It then searches these cohorts until it finds the configured minimum number of observations (default 30): exact carrier/route/month/day-of-week/time bucket; carrier/route/month/time bucket; carrier/route/adjacent-month season; carrier/route; route; carrier; global. Serving and validation use the same temporal cohort-query implementation. The response exposes the selected level, observation count, effective history, available BTS coverage, and any freshness warning.

The simulator resamples the selected observed arrival delays. Separately, it samples transfer time from a triangular **modeling assumption** of 10/20/35 minutes (minimum/mode/maximum). Success means:

`arrival delay + transfer time <= scheduled layover - boarding cutoff`

The boarding cutoff defaults to 15 minutes. Scenario probabilities hold arrival delay fixed at 0, 15, 30, or 45 minutes while sampling transfer time.

The product deliberately returns a quantitative probability rather than mapping it to a qualitative risk category.

## Time validation

All schedule inputs are airport-local clock times with minute precision. `travel_date` is the departure date in the origin airport's local timezone. Before querying history or running a simulation, the backend resolves the origin and connection airports to IANA timezone names, converts scheduled departure and arrival to UTC, and requires a plausible positive elapsed duration. IANA timezone rules handle daylight saving time; fixed UTC offsets are not used.

Because the request does not contain a first-flight arrival date, validation first interprets arrival on `travel_date` in the connection airport's timezone. If that instant is not after departure, it tests arrival on the following calendar day. The next-day interpretation is accepted only when the resulting scheduled duration is between 30 minutes and the deliberately generous 15-hour domestic-flight sanity limit. Otherwise the request receives HTTP 422 and no probability is calculated. The 15-hour limit accommodates long Alaska/Hawaii itineraries and is not a route-duration model.

Connection layover calculation remains local to the connection airport and retains the existing deterministic rollover rule: a reversed connection time is next-day only when arrival is at or after 18:00 and connecting departure is at or before 12:00. Layovers must be at least the boarding cutoff and no more than 12 hours.

Airport metadata comes from the pinned `airportsdata==20260803` package, whose MIT-licensed offline database maps IATA codes to IANA timezone names. Supported domestic jurisdictions are the United States, Puerto Rico, U.S. Virgin Islands, Guam, American Samoa, and Northern Mariana Islands. Unknown or unsupported airports are rejected rather than guessed. This validation does not change the frozen V1 delay estimator, cohort hierarchy, 24-month lookback, Monte Carlo simulation, or transfer-time assumptions.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

Tests use clearly labeled synthetic unit fixtures. They cover same-day and overnight connections, cross-timezone chronology, Phoenix's non-DST timezone, a DST transition, unknown airports, excessive durations, invalid airport codes, ambiguous reversed connection times, fallback and empty-history behavior, deterministic seeded simulations, API response shape, and probability bounds.

Run frontend interaction tests, lint, and the production build from `frontend/`:

```powershell
npm test
npm run lint
npm run build
```

## Production preparation

The recommended public architecture is a Vercel-hosted Next.js frontend and a Railway-hosted backend built from the repository Dockerfile. During image build, the Dockerfile downloads the exact versioned serving DuckDB from the public `v1-data` GitHub Release and fails unless its SHA-256 matches. It never downloads or rebuilds raw BTS data at application startup, and the DuckDB remains absent from Git.

Build the exact production database:

```powershell
.venv\Scripts\python -m backend.flight_connection.production_database `
  --source data\processed\flights_full.duckdb `
  --destination data\production\flights_production.duckdb
```

Verify frozen-V1 equivalence:

```powershell
.venv\Scripts\python -m scripts.compare_production_database --cases 50 --seed 20260810
```

The current serving artifact is 113,782,784 bytes rather than 712,519,680 bytes for the full research database. It retains all rows, preserves `flight_date` for strict temporal filtering, and removes only columns V1 never queries. The completed 50-case fixed-seed comparison had zero probability, scenario, quantile, cohort, or sample-size differences.

See [the deployment guide](docs/deployment.md) for the artifact boundaries, platform tradeoffs, checksum-verified container build, environment variables, costs and limitations, GitHub, Vercel and Railway steps, and production smoke test.

See [the frozen V1 serving alignment report](docs/serving_alignment.md) for strict 24-month semantics, historical coverage metadata, production/full equivalence, and the repeated 2024/2025 holdout results.

## Current assumptions and limitations

- Only completed, non-diverted BTS flights inform arrival delay. Cancellation and diversion probability is not modeled.
- Transfer time is a generic assumption, not measured airport-specific data. It does not model terminals, gates, mobility, checked bags, or security re-screening.
- The boarding cutoff is configurable but is not a universal airline policy.
- The API uses reporting carrier, not marketing carrier.
- Airport-local timezone conversion validates only the scheduled chronology of the first flight; it does not add schedule data or alter probability estimation. Ambiguous local times during the autumn DST fold use Python `zoneinfo`'s default first occurrence because the request has no UTC offset or fold indicator.
- Frozen V1 uses only strictly prior observations from the previous 24 calendar months. Available BTS history currently spans 2023-01-01 through 2025-12-31; later predictions do not fabricate newer observations and return a freshness warning when the coverage gap exceeds 90 days.
- The development database is deterministically stratified; model validation should still use the full database or an explicitly designed evaluation sample.
- Estimates do not yet include confidence intervals or model calibration results.
- Production requires an explicit HTTPS `NEXT_PUBLIC_API_BASE_URL`, database path, and exact CORS allowlist.

## Intentionally not implemented

The MVP has no authentication, accounts, payments, notifications, live public deployment, weather, FAA status, real-time tracking, flight-number lookup, schedule API, or airport-specific transfer-time database.

The next milestone is building and publishing the prepared backend image from a Docker-capable machine, then following the deployment guide to obtain the two public HTTPS URLs. The empirical estimator and public API response remain unchanged.
