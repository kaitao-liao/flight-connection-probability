# Flight Connection Probability

Flight Connection Probability is a web application that estimates the probability of making a U.S. domestic flight connection. It combines historical U.S. Bureau of Transportation Statistics (BTS) arrival performance with explicit, documented V1 passenger connection-time assumptions.

## What It Does

Users provide:

- origin, connection, and destination airports;
- reporting carrier;
- travel date;
- scheduled first-flight departure and arrival times; and
- scheduled connecting-flight departure time.

The application returns:

- the estimated probability of making the connection;
- scheduled layover duration;
- historical cohort size and fallback level;
- median, 75th-percentile, and 90th-percentile arrival delays; and
- sensitivity scenarios for an inbound flight arriving exactly on time or 15, 30, or 45 minutes late.

Airport and carrier fields use searchable, offline-supported lists. The backend continues to receive IATA airport codes and BTS reporting-carrier codes.

## How the Estimate Works

V1 follows an interpretable pipeline:

1. Select only BTS flights from the 24 calendar months before the requested travel date. The upper bound is exclusive, so future records cannot leak into an estimate.
2. Find a historical cohort using carrier, route, month or season, day of week, and scheduled departure-time bucket. The first cohort with at least 30 observations is used.
3. Resample arrival delays from that empirical cohort. Negative delays represent flights that arrived early.
4. Run 20,000 Monte Carlo simulations of the passenger connection process.

A simulated connection succeeds when:

```text
arrival delay
+ 20-minute deplaning time
+ Triangular(15, 25, 40)-minute gate-to-gate transfer time
+ 15-minute boarding cutoff
<= scheduled layover
```

The arrival-delay distribution is observed BTS evidence. The 20-minute deplaning time, triangular gate-transfer distribution, and 15-minute boarding cutoff are explicit **V1 modeling assumptions**, not BTS-observed passenger movement data.

The overall estimate samples the complete historical arrival-delay distribution, including early arrivals. Sensitivity scenarios instead fix arrival delay at exactly 0, 15, 30, or 45 minutes while continuing to simulate gate-transfer time.

## Historical Cohort Fallback

The cohort hierarchy is:

1. carrier, route, month, day of week, and time bucket;
2. carrier, route, month, and time bucket;
3. carrier, route, and adjacent-month season;
4. carrier and route;
5. route;
6. carrier; and
7. global history.

If a level has fewer than 30 strictly prior observations, V1 moves to the next broader comparison. The hard N=30 boundary was examined in a reproducible [fallback-boundary audit](docs/fallback_boundary_audit.md) and retained for V1. The response exposes the selected cohort and sample size.

## Deterministic Results

Monte Carlo sampling is deterministically seeded from canonical itinerary fields and the model version using SHA-256. Identical inputs under the same model version therefore return the same probability across repeated requests and process restarts. This improves reproducibility without changing the empirical distribution or simulation assumptions.

## Time Zones

All schedule fields are local airport clock times. The backend maps supported airports to IANA time zones, converts the first-flight schedule to UTC, and rejects physically impossible cross-timezone itineraries with HTTP 422.

The travel date is the departure date at the origin airport. Existing rules also support plausible next-day first-flight arrivals and overnight connections while rejecting ambiguous or excessive durations. See [time validation details](docs/serving_alignment.md) and the API tests for the enforced boundaries.

## Data

The empirical model uses the official [BTS On-Time Performance dataset](https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ).

Current repository data coverage is **2023-01-01 through 2025-12-31**. The full cleaned historical table contains **20,588,134 completed, non-diverted flights with observed arrival delay**. Exact sources, downloaded files, fields, cleaning rules, status counts, and reproducibility details are documented in [docs/data.md](docs/data.md).

This is historical data, not live flight data. Cancellations and diversions remain available for data-quality auditing but are excluded from the completed-flight arrival-delay distribution; V1 does not model their probability as connection outcomes.

The production API uses an exact serving-only DuckDB projection containing the same rows and the eight fields queried by V1. The artifact is downloaded from a versioned GitHub Release and SHA-256 verified during the Docker build; it is not committed to Git.

## Validation

The repository includes reproducible validation and diagnostic work, without claiming measured real-world passenger connection accuracy:

- [Probability sanity checks](docs/probability_sanity_check.md) verified layover monotonicity, fixed-delay sensitivity ordering, cohort selection, probability bounds, and representative carrier/time-of-day behavior.
- Deterministic testing produced zero variation across 50 repeated direct estimates and 12 repeated API requests for each checked itinerary.
- The [fallback-boundary audit](docs/fallback_boundary_audit.md) evaluated 160 balanced near-threshold cases and temporal holdouts; N=30 remained competitive and was retained for V1.
- [Temporal validation](docs/model_validation.md), [stratified validation](docs/stratified_validation.md), and [serving-alignment validation](docs/serving_alignment.md) document leakage controls, cohort behavior, calibration diagnostics, and exact full/production database equivalence.
- Automated tests cover timezone chronology, overnight handling, API schema and errors, deterministic seeds, fallback behavior, frontend validation, autocomplete keyboard behavior, loading/error states, and stale-result prevention.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript |
| Backend | FastAPI, Python, Pydantic, NumPy |
| Data and serving | DuckDB, BTS historical flight-performance data |
| Airport metadata | pinned offline `airportsdata` dataset and IANA time zones |
| Deployment | Vercel frontend, Railway backend, Docker |

## Limitations

V1 does not use or model:

- live flight status;
- live weather or FAA operational conditions;
- actual gates, terminal changes, or airport-specific walking times;
- real-time airport congestion;
- aircraft type, seat position, or aircraft-specific deplaning times;
- security re-screening, mobility needs, checked-bag handling, or airline reaccommodation; or
- cancellation and diversion probabilities.

Passenger-time parameters are generic, transparent V1 assumptions rather than directly observed passenger-connection data. The boarding cutoff is not presented as a universal airline policy. Estimates are research-oriented probabilities, not guarantees or an airline operational decision system.

## Local Development

### Backend and data

Requires Python 3.11 or newer. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m backend.flight_connection.data_pipeline --mode both --years 2023 2024 2025
.venv\Scripts\python -m uvicorn backend.flight_connection.api:app --reload
```

The data pipeline downloads the 36 official monthly archives, builds the full research database, and creates a deterministic development subset. Generated ZIP and DuckDB files are Git-ignored. Use `--resume` after an interrupted download.

The API defaults to `data/processed/flights_development.duckdb`. Override it when necessary:

```powershell
$env:FLIGHT_CONNECTION_DB = "C:\path\to\flights.duckdb"
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

### Frontend

Requires Node.js 22.13 or newer. From `frontend/` in a second terminal:

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`. For local development, configure:

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

### Example API request

```powershell
.venv\Scripts\python scripts\example_request.py
```

Or submit the checked-in request directly:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/connection-risk `
  -ContentType application/json `
  -Body (Get-Content examples\connection_request.json -Raw)
```

### Tests

From the repository root:

```powershell
.venv\Scripts\python -m pytest -q
```

From `frontend/`:

```powershell
npm test
npm run lint
npx tsc --noEmit
npm run build
```

## Repository Structure

```text
backend/    FastAPI service, empirical estimator, simulator, data pipeline
frontend/   Next.js application and frontend tests
data/       Git-ignored raw, processed, and production data locations
docs/       Data lineage, methodology, validation, and deployment reports
scripts/    Reproducible analysis, artifact-generation, and smoke-test commands
tests/      Backend unit and integration tests
examples/   Example API request payloads
```

Deployment configuration, environment variables, the checksum-verified database build, and the public smoke-test procedure are documented in [docs/deployment.md](docs/deployment.md).

## Version

**Current stable version: V1**

The V1 probability model and assumptions are frozen. No `v1.0.0` Git tag is claimed by this README.
