# V2 Phase 4: AeroDataBox Provider Acceptance Study

## Final decision

**B. Adopt with limitations.** Phase 4 is closed. AeroDataBox is approved as the
initial V2 provider of future schedule metadata, subject to explicit candidate
disambiguation, bounded/manual retry behavior, graceful provider-error handling, and no
assumption that terminal, gate, codeshare, or operating-carrier enrichment is complete.

This decision does not put AeroDataBox in the production path. Phase 5 requires separate
approval and implementation. AeroDataBox will supply schedule metadata only; BTS remains
the historical delay evidence and the existing estimator remains the probability engine.

## Evidence and accounting

The planned matrix contains 24 carrier/horizon cells. Fourteen cells received a study
outcome and ten were not tested. Completing all 24 cells is not required for this gate:
near-term lookup succeeded for all six target carriers, DL succeeded at all four
horizons, and representative non-DL evidence exists at 30, 90, and 180 days.

| Outcome | Observations |
| --- | ---: |
| Known `unique_match` | 5 |
| Known `multiple_candidates` | 2 |
| Successful match; outcome detail not retained | 5 |
| Valid `no_match` | 1 |
| `MissingRequiredScheduleFields` | 1 |
| Never tested | 10 |
| **Total planned** | **24** |

The five known unique matches are the three DL1234 horizon results, WN at about 30
days, and UA at about 90 days. The two multiple-candidate results are DL1226 and AA2117
at about 180 days; both returned two complete candidates and all candidates are
preserved. The five near-term non-DL successes were reported without retained outcome
category or candidate count, so they are not silently classified as unique.

`AS599:2027-02-15` returned `no_match`. This is a valid lookup outcome, not a provider
failure. `AA1133:2026-09-19` raised `MissingRequiredScheduleFields`; this remains a
normalization/data-quality failure and is not converted into no-match or success.

### Carrier coverage

| Carrier | Near | About 30 days | About 90 days | About 180 days | Untested |
| --- | --- | --- | --- | --- | ---: |
| DL | match | unique | unique | multiple (2) | 0 |
| AA | match | missing required fields | untested | multiple (2) | 1 |
| UA | match | untested | unique | untested | 2 |
| WN | match | unique | untested | untested | 2 |
| AS | match | untested | untested | no-match | 2 |
| B6 | match | untested | untested | untested | 3 |

### Horizon coverage

| Horizon | Match outcomes | No-match | Data-quality failure | Untested |
| --- | ---: | ---: | ---: | ---: |
| Near term | 6 | 0 | 0 | 0 |
| About 30 days | 2 | 0 | 1 | 3 |
| About 90 days | 2 | 0 | 0 | 4 |
| About 180 days | 2 | 1 | 0 | 3 |

The evidence demonstrates six-carrier near-term feasibility and non-DL longer-horizon
behavior through WN at 30 days, UA at 90 days, and AA/AS at 180 days. It is not a
population estimate of provider coverage or failure rates.

## Required and optional fields

Twelve observations returned usable matched schedules. Required identity, route,
scheduled departure, and scheduled arrival fields were complete for **12/12 matched
observations**. For results whose candidate-level counts were retained, all **9/9
returned candidates** were complete. The no-match observation has no candidate fields
and is excluded from completeness denominators. The missing-required-fields observation
is reported separately rather than hidden in the denominator.

Optional coverage uses only candidates for which the relevant evidence was retained:

| Optional field | Retained coverage |
| --- | ---: |
| Departure terminal | 3/5 (60%) |
| Arrival terminal | 2/5 (40%) |
| Aircraft type | 5/5 (100%) |
| Quality | 5/5 (100%) |
| Departure gate | 0/3 (0%) |
| Arrival gate | 0/3 (0%) |
| Explicit operating carrier | 0/2 (0%) |
| Status | Not measurable from retained evidence |
| Codeshare status | Not measurable from retained evidence |

Missing terminal or gate data is optional enrichment and does not make an otherwise
complete schedule unusable. Optional details that were not retained are excluded rather
than assumed absent.

## Operational limitations

Several initial controlled calls raised ordinary `ProviderResponseError`; separately
approved one-call retries of the same candidates later succeeded. The study does not
establish a gateway, transport, WAF, or product-layer root cause. This intermittent
behavior is an operational limitation and must not be interpreted as proof that a
carrier or horizon is unsupported.

The research adapter preserves sanitized non-2xx diagnostics: status/reason, content
type, server, selected request IDs, sorted safe response-header names, body category and
length, and bounded JSON error metadata or a generic text/HTML marker. It never retains
raw response bodies, request headers, credentials, cookies, or secret-like headers.

Other limitations:

- candidate sources were primarily public third-party schedules rather than airline
  contractual schedule feeds;
- the sample is deliberately small and quota-conscious;
- five successful near-term observations lack retained outcome/optional-field detail;
- no-match may mean no operation, unpublished schedule, invalid planning evidence, or
  provider coverage limits;
- multiple candidates require user disambiguation;
- terminal/gate/codeshare/operating identity is incomplete;
- API.Market billing telemetry is not available to the local study scripts.

## API usage

No additional live call was made during final closeout. Exact accumulated usage cannot be
reconstructed from local artifacts. The original DL lookup is documented as consuming
2 units; other actual charges must be read from API.Market logs. The study used one
logical request per controlled call, no automatic retries, no redirect following, and no
discovery or quota-probe calls. Do not present an estimated upper bound as observed
billing.

## Phase 5 plan (not implemented)

### Target flow

```text
first flight number + second flight number + travel date
  -> FutureFlightProvider / AeroDataBox lookup for each flight
  -> preserve every normalized candidate
  -> user disambiguation when either lookup is ambiguous
  -> validate first destination == second origin
  -> resolve airport-local times and time zones
  -> validate chronology and compute scheduled layover
  -> existing BTS historical arrival-delay estimator
  -> existing connection simulator
  -> connection probability plus transparent schedule metadata
```

### Production behavior

| Condition | Required V2 behavior |
| --- | --- |
| `unique_match` | Continue with the normalized schedule candidate. |
| `multiple_candidates` | Return all candidates with route/time labels and require explicit selection; never guess. |
| `no_match` | Return a recoverable schedule-not-found result; allow manual itinerary entry where product policy permits. |
| Missing required schedule fields | Return a provider-data-quality error; do not calculate from an incomplete schedule. |
| Authentication error | Fail closed, alert operators, and do not retry. |
| Quota/rate-limit error | Fail gracefully, expose no credentials, and do not retry during the user request. |
| Timeout/network error | Return a temporary-unavailable response; permit one separately bounded retry under policy. |
| Ordinary intermittent provider error | Preserve sanitized diagnostics and return temporary-unavailable; do not imply unsupported flight. |
| Missing terminal/gate | Continue; label enrichment unavailable and use the existing assumed transfer-time model. |
| Invalid connection airport | Reject when first destination does not equal second origin. |
| Impossible/reversed chronology | Reject after timezone-aware normalization; handle valid overnight connections explicitly. |
| Time zones | Interpret provider local times with airport IANA zones, retain offsets, and compare timezone-aware instants. |

### Conservative retry policy

Do not implement retries inside the provider adapter. Phase 5 should use a request-level
policy with at most one retry for timeout, network failure, or ordinary 5xx/provider
failure; no retry for authentication, quota, rate limit, missing fields, no-match, or
multiple candidates. Retry only after a short bounded delay, use an idempotency/cache key
based on provider + flight number + date, and coalesce concurrent identical lookups to
avoid duplicate billing. Record attempt counts and sanitized diagnostics. The retry must
remain disabled until its unit-budget and UX behavior are tested explicitly.

### Expected files

Existing research contracts should be promoted carefully rather than coupled to V1.
Phase 5 is expected to change or add:

- `backend/flight_connection/future_flight_provider.py`
- `backend/flight_connection/aerodatabox_provider.py`
- new `backend/flight_connection/v2_itinerary_service.py`
- new `backend/flight_connection/v2_schemas.py`
- `backend/flight_connection/api.py` (new V2 routes only; V1 contract unchanged)
- new `tests/test_v2_itinerary_service.py`
- new `tests/test_v2_api.py`
- updates to `tests/test_aerodatabox_provider.py`
- dependency metadata only if the serving environment does not already install `httpx`
- a deployment Docker allowlist update only when V2 serving is intentionally deployed
- Phase 5 documentation under `docs/`
- later, after backend acceptance and separate approval, dedicated frontend V2 form,
  API-client, types, and tests; no frontend file is changed in Phase 4 or the initial
  backend-only Phase 5 slice

Exact route placement should be confirmed from the repository's current FastAPI
composition before implementation. V1 estimator modules, the production DuckDB schema,
and V1 routes must remain unchanged.

## Closeout

Phase 4 is complete with decision **B. Adopt with limitations**. The project is ready to
begin a separately approved, backend-first Phase 5 implementation. No Phase 5 code,
frontend change, deployment, commit, push, tag, or release is part of this closeout.
