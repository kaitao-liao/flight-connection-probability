# V2 Phase 3: AeroDataBox Feasibility and Provider-Neutral Adapter

## Outcome

**Recommendation: continue testing before adoption.** The adapter and offline contract
tests are ready, but this execution environment blocked outbound TCP before the first
provider request reached API.Market. Consequently, no honest live field-coverage,
future-horizon, codeshare, terminal/gate, or quota measurements can be reported yet.
Mock results validate software behavior, not provider data quality.

This work is research-only and isolated from V1. AeroDataBox is considered only as a
current/future schedule metadata provider, never as a historical delay source.

## Endpoint and authentication

The official API.Market OpenAPI specification inspected on 2026-08-12 was version
1.15.1.0. It specifies:

- Base URL: `https://prod.api.market/api/v1/aedbx/aerodatabox`
- Endpoint: `GET /flights/{searchBy}/{searchParam}/{dateLocal}`
- Lookup path: `Number/{carrier-and-flight-number}/{YYYY-MM-DD}`
- Authentication header: `x-api-market-key`
- Flight Status cost class: Tier 2

The prototype sends only:

```text
dateLocalRole=Departure
withAircraftImage=false
withLocation=false
withFlightPlan=false
```

`Both` is not used because a connection itinerary identifies a flight by its local
departure date. Position, image, and flight-plan data are unnecessary and may increase
response size or cost. Current provider pricing documentation says Tier 2 costs two API
units, and Basic/Pro API.Market rate limiting is one request per second. It also advertises
up to 210 future days for the lowest plan, subject to data coverage and published airline
schedules. Those are documented capabilities, not findings from this blocked live run.

## Security preflight

The preflight was completed before attempting a network connection:

- `.env` exists and is matched by `.gitignore`.
- `.env` is not tracked by Git.
- `AERODATABOX_API_KEY` is present and non-empty.
- The key had zero matches across tracked repository files.
- The key was never printed, persisted, included in fixtures, or copied into docs.
- The HTTP adapter never logs requests or headers.
- Exceptions use generic messages and do not include response bodies or credentials.
- Recursive redaction covers `x-api-market-key`, `x-magicapi-key`, `Authorization`, and
  secret values embedded in messages.

The live audit loads only `AERODATABOX_API_KEY` from the local `.env`; application code
itself reads only the environment variable. Missing configuration fails before the
transport is called.

## Smoke lookup and live-run limitation

The controlled smoke candidate was `DL1234` departing on 2026-08-20, eight days after
the audit date. The local process was denied socket access before an HTTP connection was
made (`WinError 10013`). This is an execution-sandbox failure, not an AeroDataBox HTTP
failure.

Measured live-run facts:

| Measure | Result |
| --- | ---: |
| Local smoke invocations | 1 |
| Requests that reached API.Market | 0 |
| HTTP responses | 0 |
| Provider lookups attempted | 0 |
| Successful provider lookups | Not measurable |
| API units consumed | 0 expected; no request reached the gateway |
| Quota before/after | Not measurable |

Per the requirement to diagnose the first failure before making many requests, the
20–30 lookup audit was not started.

### Offline correction after the first local run

A later local run reported `ProviderQuotaError`, while the API.Market request log showed
successful HTTP 200 AeroDataBox requests billed at two units. The original adapter could
raise `ProviderQuotaError` only after its HTTP transport returned 402 or 403; its 200 path
did not inspect quota headers and could not raise that exception during parsing. The
repository therefore cannot prove that a particular dashboard 200 row was the same final
HTTP response observed by the client.

The real classification defect was nonetheless concrete: every 403 was labeled quota or
subscription failure without machine-readable evidence. This has been corrected. A 200
response is always treated as successful at the status layer, regardless of usage,
remaining-unit, rate-limit, or subscription headers. HTTP 402 remains quota/subscription
evidence; HTTP 403 is quota only when its JSON contains a recognized machine-readable
quota code. An ordinary 403 is now `ProviderResponseError`. Response bodies and headers
are never copied into exceptions.

The original transport contained no explicit retry, and the audit loop called the provider
once per `--lookup`. Redirects were disabled for the fixed official endpoint. The
repository cannot determine which external or manual action produced the second historical
dashboard 200 entry.

### Strict offline request-parity audit

After API.Market logs showed Playground HTTP 200 responses while the local client saw an
unlogged HTTP 403, the request was reconstructed without opening a socket. The adapter's
sanitized canonical request is:

```http
GET https://prod.api.market/api/v1/aedbx/aerodatabox/flights/Number/DL1234/2026-08-20?dateLocalRole=Departure&withAircraftImage=false&withLocation=false&withFlightPlan=false
Accept: application/json
x-api-market-key: [REDACTED]
```

There is no request body and therefore no `Content-Type`. Python's `urllib` supplies its
own `Python-urllib/<version>` User-Agent at send time unless one is explicitly set.

The official API.Market OpenAPI v1.15.1.0 specifies the same HTTPS host, base path,
endpoint, exact enum value `Number`, `YYYY-MM-DD` date, exact query names,
`Departure` enum value, boolean values, and `x-api-market-key`. The Playground uses the
same documented request shape. Header names are case-insensitive in HTTP; `urllib`'s
internal display spelling `X-api-market-key` does not change the header's meaning.

No documented request mismatch was found. The Playground/client User-Agent differs, but
the OpenAPI contract does not require or define a User-Agent, so changing it would be an
unsupported guess rather than a parity fix. Likewise, the official base URL is direct;
no required redirect or alternate host is documented. With redirects disabled, a real
redirect would surface as 3xx/`ProviderResponseError`, not the observed 403. Generic
redirect following remains disabled so credentials cannot be forwarded to an unexpected
target.

Because the local 403 did not appear in API.Market Request Logs, it was generated before
the product-level request logger, but repository and OpenAPI evidence cannot identify
which upstream gateway or security rule produced it. No request-construction code was
changed during this parity audit. Regression tests now lock the exact URL, parameter case
and values, header set, lack of `Content-Type`, timeout, and one-call behavior.

### Python transport replacement after direct PowerShell validation

A direct PowerShell `Invoke-WebRequest` from the same machine subsequently returned HTTP
200 and valid AeroDataBox flight JSON using the same key, URL, date, and parameters. This
validated the subscription, credential, trial quota, endpoint, query, local network, and
TLS path. The remaining difference was the Python HTTP stack: the original adapter used
standard-library `urllib`, while PowerShell used its .NET HTTP stack.

No packet capture or upstream gateway diagnostic identifies which `urllib` characteristic
caused the unlogged 403, so the exact trigger is not proven. Candidate differences include
User-Agent, HTTP implementation/version negotiation, connection and compression defaults,
TLS fingerprint, proxy handling, and header serialization. None should be presented as
the confirmed cause.

The research adapter now uses the project's existing `httpx` development dependency with
a synchronous one-shot client. It preserves environment proxy settings (`trust_env=True`),
uses a bounded timeout, sends exactly the required auth and Accept headers, performs one
`GET`, has no retry configuration, and keeps `follow_redirects=False`. Transport
exceptions are replaced with credential-free provider exceptions using suppressed causes,
so even a lower-level exception that includes request headers cannot leak the key through
a traceback. Mock-transport tests assert the exact request, one-call behavior, no redirect,
successful 200 normalization, and secret-safe failures.

## Planned feasibility sample

The opt-in audit should use 24 independently verified flights, four for each of DL, AA,
UA, WN, AS, and B6. Candidate schedules must be checked immediately before the run; no
flight numbers are embedded in the repository because doing so without verification
would fabricate future schedule data.

The 24 cells should collectively cover:

- horizons near future, approximately 30, 90, and 180 days;
- ATL, JFK/LGA, ORD, DFW, DEN, CLT, PHX, SEA, and BOS where published;
- short-haul, medium-haul, transcontinental, same-day, and overnight operation;
- known marketing-code and operating-code queries for codeshare comparison.

Use the live script only in a network-enabled environment:

```powershell
python -m scripts.v2_aerodatabox_live_audit --confirm-live `
  --lookup DL1234:2026-08-20 `
  --lookup AA1234:2026-09-10
```

The values above illustrate syntax only; every candidate must be independently verified
before use. The script enforces a maximum of 30 lookups, waits slightly over one second
between requests, prints only normalized counts/outcomes, and does not persist raw data.

## Coverage measurement status

No live coverage percentage is available. The following table deliberately separates
required adoption evidence from offline adapter capability:

| Field | V2 role | Adapter support | Live coverage |
| --- | --- | --- | --- |
| Carrier / flight number | Required | Yes | Not measured |
| Flight date | Required | Yes, from departure local time | Not measured |
| Origin / destination IATA | Required | Yes | Not measured |
| Scheduled departure / arrival local | Required | Yes | Not measured |
| Marketing identity | Required | Yes | Not measured |
| Operating identity | Required for codeshares when available | Preserved only when explicit/inferable | Not measured |
| Quality markers | Useful | Departure/arrival markers preserved | Not measured |
| Terminal | Optional | Nullable | Not measured |
| Gate | Live-oriented optional | Nullable | Not measured |
| Aircraft type | Optional | Nullable | Not measured |

Provider adoption requires high live coverage of every required field across the planned
sample. Terminal and especially gate coverage must be reported separately and must never
be used as a prerequisite for connection-probability calculation without evidence.

## Provider-neutral adapter design

`FutureFlightProvider.lookup_by_number(flight_number, date_local)` returns a
`FutureFlightLookupResult` with one of:

- `no_match`
- `unique_match`
- `multiple_candidates`

Candidates are sorted by scheduled departure, origin, destination, and marketing flight
number. Multiple candidates are never silently collapsed. A future caller can ask the
user for route/time disambiguation.

Normalized fields include source and retrieval timestamp, requested flight date,
marketing and operating identities, route, local scheduled times, nullable terminal/gate,
status, quality markers, codeshare status, aircraft type, and a provider reference when
available. Raw provider response objects do not escape the adapter.

The AeroDataBox contract exposes `number`, `airline`, and `codeshareStatus`. For
`IsOperator`, the adapter records the same marketing and operating identity. For
`IsCodeshared`, it preserves the marketing identity but leaves operating identity null
unless the provider supplies it explicitly; it does not invent an operator from a call
sign or historical BTS data. Live testing must determine whether querying the marketing
and operating numbers returns duplicate objects or a canonical operating segment.

Missing required schedule fields fail the entire lookup visibly. They are never replaced
with historical schedule inference.

## Error behavior

The research adapter distinguishes:

- missing credential;
- authentication failure;
- unavailable quota/subscription;
- rate limit;
- timeout;
- no match (`204` or an empty array);
- malformed JSON or non-array response;
- candidates missing required schedule fields;
- other provider/network failures.

HTTP response bodies are intentionally omitted from raised errors because gateways can
occasionally echo request details. Normal backend tests inject a mock transport and never
make network calls.

### Pre-response transport diagnostics

Previously, `HttpxTransport` caught every `httpx.TimeoutException` as a generic
`ProviderTimeoutError` and every other `httpx.RequestError` as a generic
`ProviderResponseError`, without diagnostic metadata. A failure before an HTTP response
therefore exposed no safe evidence about which transport phase failed.

The provider now attaches a bounded internal diagnostic to those same provider-level
exceptions. Public V2 status and messages are unchanged. The schema is:

```text
transport_error_category
exception_class
phase
response_received: false
host: prod.api.market
request_method: GET
timeout_seconds
trust_env: true
follow_redirects: false
thread_name
```

It never includes exception text, traceback, URL path/query, request or response headers,
environment values, proxy URLs, cookies, bodies, or credentials. Standalone and opt-in
Phase 5 harnesses retain this already-sanitized object; `V2ItineraryService` continues to
map the failure to its existing safe public outcome without exposing the diagnostic.

| httpx exception | Category / phase | Provider exception |
| --- | --- | --- |
| `ConnectTimeout` | `connect_timeout` / connect | `ProviderTimeoutError` |
| `ReadTimeout` | `read_timeout` / read | `ProviderTimeoutError` |
| `WriteTimeout` | `write_timeout` / write | `ProviderTimeoutError` |
| `PoolTimeout` | `pool_timeout` / pool | `ProviderTimeoutError` |
| `ConnectError` | structured DNS, refused, reset, TLS, or unknown / connect | `ProviderResponseError` |
| `ReadError`, `WriteError`, `CloseError` | corresponding safe category | `ProviderResponseError` |
| `ProxyError` | `proxy_error` / proxy | `ProviderResponseError` |
| `RemoteProtocolError`, `LocalProtocolError` | corresponding category / protocol | `ProviderResponseError` |
| generic `NetworkError`, `TransportError`, `RequestError` | network or unknown fallback | `ProviderResponseError` |

Connect-error subclassification inspects only exception-chain types and structured
`errno`: `socket.gaierror` identifies DNS, `ssl.SSLError` identifies TLS, and recognized
connection-refused/reset errno values identify those socket outcomes. Arbitrary exception
messages are never inspected, so an unrecognized connect failure remains
`connect_error_unknown` rather than being guessed.

`trust_env=True` permits httpx to consult process proxy environment variables. During
this offline diagnostic, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` were all
absent. Only presence booleans were checked; no environment value was printed. This does
not establish what was present during earlier live attempts, and PowerShell behavior
cannot be inferred beyond the local evidence recorded at that time.

## Units and projected capacity

The official pricing page states that API units are not requests and Flight Status is
Tier 2, or two units for a successful call. API.Market also states that only successful
2xx calls are charged. This run could not query account usage, so actual balance and
metering were not independently verified.

If the documented two-unit cost applies, without cache, retries, refreshes, or other API
operations:

- one flight lookup: approximately 2 units;
- a two-leg itinerary: approximately 4 units;
- a 6,000-unit plan: theoretical maximum 3,000 flight lookups or 1,500 two-leg
  itineraries per month.

These are ceilings, not capacity promises. Multi-step discovery, retries, refreshes,
provider changes, and successful no-match responses may alter consumption.

## Cache design, not implementation

Suggested key:

```text
(provider, normalized_marketing_flight_number, local_departure_date)
```

Cache entries must include retrieval time, provenance, outcome, and candidate list.
Distant-future schedules can probably be retained longer than near-departure schedules;
near-departure terminal/gate/status data needs shorter refresh intervals. Negative and
ambiguous results need distinct policies. No TTL is selected without live freshness
evidence. At the theoretical limit, a cache hit avoids one two-unit lookup, but savings
depend entirely on repeated itinerary/date searches.

## Limitations and adoption gate

- No provider HTTP response was obtained in this environment.
- Required-field, terminal, gate, codeshare, and horizon coverage remain unknown.
- The public API.Market product page observed during research displayed no currently
  selectable plan while the official AeroDataBox pricing page still described API.Market
  plans; subscription availability should be confirmed manually.
- No future schedule source is authoritative forever; provenance and freshness must be
  visible in any later product.
- Historical BTS patterns remain separate and cannot silently substitute for provider
  failure or missing future schedules.

## Recommendation and Phase 4

Choose **2. Continue testing before adoption**. In a network-enabled environment, run the
24-cell sample, record usage before/after, verify required-field coverage and horizons,
and manually cross-check a small stratified subset against authoritative published
schedules. Adopt only if required schedule fields are consistently complete and
codeshare/multi-result behavior is explainable.

Recommended V2 Phase 4 is a live, quota-bounded provider acceptance study plus an adapter
contract review. Do not build frontend flight-number search until that gate passes.
