# V2 Phase 5: Backend Schedule Integration

## Status and separation of responsibilities

This backend-first slice resolves two future flight numbers into a connection itinerary
and then calls the existing BTS-backed probability service. It does not modify V1,
deploy V2, or add frontend behavior.

- **AeroDataBox / `FutureFlightProvider`** supplies future route and scheduled-time
  metadata plus optional terminal, gate, aircraft, quality, codeshare, and operator data.
- **BTS historical data** remains the evidence for arrival-delay distributions.
- **`ConnectionRiskService`** remains the connection-probability engine, including the
  frozen historical window, strict cutoff, cohort fallback, deterministic Monte Carlo,
  transfer-time assumptions, boarding cutoff, and cancellation/diversion exclusions.

## Architecture and lifecycle

```text
POST /api/v2/connection-risk
  -> V2ConnectionRequest
  -> V2ItineraryService
     -> FutureFlightProvider.lookup_by_number(first)
     -> stop on no-match or provider/data-quality failure
     -> FutureFlightProvider.lookup_by_number(second)
     -> preserve every ambiguous candidate
     -> validate connection airport
     -> normalize local schedules with airport IANA time zones
     -> validate both flights and connection chronology
     -> compute scheduled layover from timezone-aware instants
     -> thin ConnectionRiskRequest adapter
     -> existing ConnectionRiskService.estimate
  -> V2ConnectionResponse
```

The service accepts the provider abstraction through its constructor. The FastAPI app
accepts either an injected V2 service or an explicit provider factory. The module-level
app creates neither a live provider nor a credential-bearing client, so importing the
module cannot call AeroDataBox. Tests use fakes only.

## API contract

Request:

```json
{
  "first_flight_number": "DL1234",
  "second_flight_number": "DL5678",
  "travel_date": "2026-08-20"
}
```

Route: `POST /api/v2/connection-risk`. It is registered only when `create_app` receives
an explicit V2 service or provider factory. The current production V1 app supplies
neither, so Phase 5 does not silently change the deployed image or expose an unconfigured
live route.

The response always contains a `status`. A success contains the two resolved schedules,
connection airport, timezone-aware scheduled timestamps, scheduled layover, the complete
existing estimator response, and optional-data warnings. An ambiguous response contains
each ambiguous leg and every normalized candidate without selecting one. Other domain
statuses are `schedule_not_found`, `invalid_connection_airport`,
`invalid_chronology`, `provider_data_quality_error`,
`provider_temporarily_unavailable`, and `provider_configuration_error`. Provider raw
responses, diagnostics, exception text, headers, and credentials are not exposed.

## Outcome and failure handling

| Provider/service condition | V2 result |
| --- | --- |
| One candidate per leg | Validate and estimate. |
| Multiple candidates | `ambiguous`; preserve all candidates. |
| No candidate | `schedule_not_found`; not a provider failure. |
| Connection airports differ | `invalid_connection_airport`. |
| Reversed/impossible schedule | `invalid_chronology`. |
| Missing required schedule fields or malformed normalized data | `provider_data_quality_error`. |
| Authentication | `provider_configuration_error`. |
| Quota, rate limit, timeout, network, ordinary provider response failure | `provider_temporarily_unavailable`. |
| Missing terminal/gate | Continue and return a warning where applicable. |

There are no automatic retries, caches, request coalescing, discovery calls, or quota
probes. A terminal first-leg no-match or provider failure prevents the second lookup.
When the first leg is ambiguous, the second leg is still looked up so a both-legs
ambiguous response can be represented.

## Timezone and chronology rules

Airport IATA codes are resolved through the existing offline U.S. airport-to-IANA mapping.
Naive provider local timestamps are assigned the airport zone; timestamps with offsets
are converted to that airport zone. Chronology is compared as timezone-aware instants.
Both flights must have positive scheduled duration, the second departure must follow the
first arrival, and the first destination must exactly equal the second origin. Provider
dates naturally support overnight and date-boundary connections; the service does not
silently roll or guess a reversed provider timestamp.

## Phase 4 limitations retained in V2

- intermittent provider/transport failures must be presented as temporary unavailability;
- multiple candidates require user selection;
- no-match is recoverable and not a failure;
- required-field omissions block estimation;
- terminal, gate, codeshare, and operating-carrier enrichment can be incomplete;
- no retry is enabled, preventing hidden duplicate billing.

## Controlled live validation (Phase 5B)

The one approved end-to-end validation was run on 2026-08-12 through the actual
`POST /api/v2/connection-risk` FastAPI route, with the live provider injected through
the intended provider-factory seam. The independently verified same-day itinerary was:

| Leg | Flight | Route | Scheduled local times | Public schedule source |
| --- | --- | --- | --- | --- |
| First | DL1575 | ATL-JFK | 2026-08-20 13:56-16:30 | [Flight.info DL1575](https://www.flight.info/DL1575) |
| Second | DL5798 | JFK-BOS | 2026-08-20 17:30-19:15 | [Flight.info DL5798](https://www.flight.info/DL5798) |

The public schedule implied a 60-minute JFK connection. The request budget was two
provider calls (approximately four API units maximum), with no discovery, retry,
redirect following, or quota probe.

The first-leg lookup made one provider request and ended in `ProviderResponseError`.
The V2 service safely returned HTTP 200 with the structured domain outcome
`provider_temporarily_unavailable`, identified `leg: first`, and did not expose provider
details or credentials. Per the approved stop rule, the second leg was not queried and
the itinerary, timezone chronology, layover, BTS cohort/sample, probability,
sensitivity values, and optional terminal/gate/aircraft/quality metadata could not be
resolved in this run. One request was made, corresponding to an estimated two API units;
actual billed units were not observable from the local response.

This result validates the API-to-service failure path and first-leg short-circuit, but it
does not yet validate the complete successful provider-to-estimator path. A future live
retry requires separate approval and should not be automatic.

### Final manual Phase 5 result

The complete controlled validation subsequently passed in the normal Windows Python
3.12 virtual environment. `DL1575` and `DL5798` on 2026-08-20 each produced one unique,
required-field-complete candidate. The real V2 route resolved ATL-JFK-BOS, validated JFK
as the connection airport, passed timezone-aware chronology, calculated a 60-minute
layover, and invoked the production-DuckDB estimator. The result used 115 BTS
observations and returned connection probability `0.5909` with existing coverage,
assumptions, sensitivity scenarios, and freshness warning intact. Two provider requests
were made. This passed the Phase 5 backend integration gate; it did not deploy or add
frontend behavior.

## Offline differential diagnosis after Phase 5B

An offline comparison was performed after the standalone audit resolved DL1575 but two
Phase 5B attempts returned a first-leg `ProviderResponseError`. No provider request was
made during this diagnosis.

### Call-path comparison

| Detail | Standalone audit | Phase 5B harness |
| --- | --- | --- |
| Entry point | `v2_aerodatabox_live_audit.main` | `v2_live_end_to_end.main` |
| Credential loader | Imported local `.env` parser | The exact same imported `load_local_env` function |
| `.env` path | `Path(".env")`, relative to current working directory | Identical |
| Environment precedence | `os.environ.setdefault`; an existing process value wins | Identical |
| Provider construction | `AeroDataBoxFutureFlightProvider()` | Identical, then wrapped only to count/sanitize outcomes |
| Provider lifetime | One instance for the audit invocation | One instance created before app/TestClient construction |
| V2 service | Not involved | Factory creates one `V2ItineraryService` around that provider |
| Lookup call | Directly from the script main thread | Sync FastAPI endpoint via TestClient/AnyIO worker thread |
| HTTP client | A fresh synchronous `httpx.Client` per lookup | Identical transport code |
| Client lifetime | Context-managed and closed after the lookup | Identical |
| Timeout | 15 seconds | Identical |
| Environment proxy behavior | `trust_env=True` | Identical |
| Redirect behavior | `follow_redirects=False` | Identical |
| Retries | None | None |
| Provider exception | Printed as provider error with sanitized diagnostic | Mapped by V2 to `provider_temporarily_unavailable` |

The repository-root diagnostic environment had no pre-existing process variable, had a
project-root `.env` entry, and therefore both scripts would load the same stripped value
from the same file. Only presence and length were inspected; the credential value was
not printed or persisted. Both paths use the same whitespace stripping and quote
removal. The wrapper does not mutate the provider, credential, transport, request, or
result.

### Sanitized request parity

Mock-transport instrumentation sent the same DL1575-equivalent 200 response through a
direct provider call and through the full FastAPI/V2 route. Both normalized it
successfully. Excluding the deliberately measured thread identifier, their first-call
fingerprints were identical:

```text
method: GET
host: prod.api.market
path: /api/v1/aedbx/aerodatabox/flights/Number/DL1575/2026-08-20
query: dateLocalRole=Departure, withAircraftImage=false,
       withLocation=false, withFlightPlan=false
header names supplied by adapter: Accept, x-api-market-key
Content-Type supplied by adapter: none
timeout: 15 seconds
redirects: disabled
environment settings: trusted
client: synchronous httpx.Client (0.28.1 in the diagnostic environment)
```

Neither script supplies a User-Agent, Connection, or Accept-Encoding value explicitly;
the same `httpx.Client` defaults serialize those headers in both paths. The base URL,
path casing, date format, query parameter casing/values, and authentication header name
are produced inside the same provider method.

### Findings and next diagnostic step

No request-construction, credential-loading, provider-construction, or client-lifecycle
mismatch was found. The concrete execution-context difference is that TestClient runs
the synchronous route/provider call in an AnyIO worker thread, whereas the standalone
audit calls it from the script main thread. Offline mocks prove that this boundary does
not alter request fields or normalization behavior; they do not prove that it caused
the observed live provider response. The Starlette/httpx TestClient deprecation warning
is also real, but is not evidence of the external 403 cause.

The standalone audit preserved sanitized provider response diagnostics while the initial
Phase 5B harness retained only the exception type. The harness now retains the same
already-sanitized diagnostic metadata (never raw bodies, request headers, or secrets),
while the public V2 response continues to expose only its safe domain status.

The next controlled live diagnostic should avoid TestClient while preserving the same
provider and V2 service. The narrowest option is one direct `V2ItineraryService.estimate`
invocation with the existing live provider and existing two-request guard. A stronger
HTTP-level alternative is to run the real V2-enabled FastAPI app on localhost and send
one localhost request. Either option requires separate approval and must retain the
same no-retry, two-request maximum and first-leg short-circuit rules.

## Before production deployment

- decide HTTP status-code policy for domain responses versus the current structured
  response-body status;
- add explicit user candidate-selection input/API flow after ambiguity;
- decide bounded cache/retry/coalescing policy and API-unit observability;
- wire an explicit AeroDataBox provider factory in the intended serving entry point;
- update the Docker serving allowlist and dependency installation if required;
- build frontend flight-number entry and disambiguation only after backend approval;
- add operational monitoring without recording credentials or raw provider bodies.
