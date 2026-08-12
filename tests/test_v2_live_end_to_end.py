import json
import threading
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import pytest

from backend.flight_connection.aerodatabox_provider import (
    AeroDataBoxFutureFlightProvider,
    HttpResponse,
)
from backend.flight_connection.future_flight_provider import (
    FutureFlightCandidate,
    FutureFlightLookupResult,
    ProviderResponseError,
)
from scripts.v2_live_end_to_end import execute


def candidate(number, origin, destination, departure, arrival):
    return FutureFlightCandidate(
        source="offline-test",
        retrieved_at_utc=datetime(2026, 8, 12, tzinfo=timezone.utc),
        flight_date=date(2026, 8, 20),
        marketing_carrier="DL",
        marketing_flight_number=number,
        operating_carrier="DL",
        operating_flight_number=number,
        origin_iata=origin,
        destination_iata=destination,
        scheduled_departure_local=departure,
        scheduled_arrival_local=arrival,
        departure_terminal="S" if origin == "ATL" else "4",
        arrival_terminal="4" if destination == "JFK" else "A",
        aircraft_type="Test Aircraft",
        quality=("departure:Basic", "arrival:Basic"),
        codeshare_status="IsOperator",
    )


class FakeProvider:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def lookup_by_number(self, flight_number, date_local):
        self.calls.append((flight_number, str(date_local)))
        return next(self.results)


class FingerprintTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.fingerprints = []

    def get(self, url, *, params, headers, timeout):
        parsed = urlsplit(url)
        self.fingerprints.append({
            "method": "GET",
            "host": parsed.netloc,
            "path": parsed.path,
            "query": dict(params),
            "header_names": sorted(name.lower() for name in headers),
            "timeout": timeout,
            "transport_interface": "HttpTransport.get",
            "thread_id": threading.get_ident(),
        })
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def provider_payload(number, origin, destination, departure, arrival):
    return {
        "number": number,
        "status": "Expected",
        "codeshareStatus": "IsOperator",
        "airline": {"name": "Delta Air Lines", "iata": "DL"},
        "departure": {
            "airport": {"iata": origin},
            "scheduledTime": {"local": departure},
            "terminal": "S" if origin == "ATL" else "4",
            "quality": ["Basic"],
        },
        "arrival": {
            "airport": {"iata": destination},
            "scheduledTime": {"local": arrival},
            "terminal": "4" if destination == "JFK" else "A",
            "quality": ["Basic"],
        },
        "aircraft": {"model": "Test Aircraft"},
    }


def http_response(payload):
    return HttpResponse(200, json.dumps(payload).encode("utf-8"))


def test_controlled_harness_uses_actual_route_and_exactly_two_provider_calls(
    development_database,
):
    first = candidate(
        "DL1575", "ATL", "JFK",
        datetime(2026, 8, 20, 13, 56), datetime(2026, 8, 20, 16, 30),
    )
    second = candidate(
        "DL5798", "JFK", "BOS",
        datetime(2026, 8, 20, 17, 30), datetime(2026, 8, 20, 19, 15),
    )
    provider = FakeProvider([
        FutureFlightLookupResult("unique_match", (first,)),
        FutureFlightLookupResult("unique_match", (second,)),
    ])

    output = execute(provider, development_database)

    assert output["http_status"] == 200
    assert output["v2_response"]["status"] == "success"
    assert output["v2_response"]["itinerary"]["scheduled_layover_minutes"] == 60
    assert output["provider_request_count"] == 2
    assert output["estimated_api_units"] == 4
    assert provider.calls == [
        ("DL1575", "2026-08-20"),
        ("DL5798", "2026-08-20"),
    ]


def test_controlled_harness_stops_after_terminal_first_leg_no_match(development_database):
    provider = FakeProvider([FutureFlightLookupResult("no_match", ())])

    output = execute(provider, development_database)

    assert output["v2_response"]["status"] == "schedule_not_found"
    assert output["v2_response"]["leg"] == "first"
    assert output["provider_request_count"] == 1
    assert provider.calls == [("DL1575", "2026-08-20")]


def test_standalone_and_fastapi_paths_have_identical_first_request_fingerprints(
    development_database,
):
    first_payload = provider_payload(
        "DL1575", "ATL", "JFK",
        "2026-08-20 13:56-04:00", "2026-08-20 16:30-04:00",
    )
    second_payload = provider_payload(
        "DL5798", "JFK", "BOS",
        "2026-08-20 17:30-04:00", "2026-08-20 19:15-04:00",
    )
    direct_transport = FingerprintTransport([http_response([first_payload])])
    direct_provider = AeroDataBoxFutureFlightProvider(
        api_key="offline-test-secret", transport=direct_transport,
    )
    direct_result = direct_provider.lookup_by_number("DL1575", "2026-08-20")

    route_transport = FingerprintTransport([
        http_response([first_payload]), http_response([second_payload]),
    ])
    route_provider = AeroDataBoxFutureFlightProvider(
        api_key="offline-test-secret", transport=route_transport,
    )
    route_output = execute(route_provider, development_database)

    assert direct_result.outcome == "unique_match"
    assert route_output["v2_response"]["status"] == "success"
    assert route_output["v2_response"]["itinerary"]["scheduled_layover_minutes"] == 60
    direct_fingerprint = {k: v for k, v in direct_transport.fingerprints[0].items()
                          if k != "thread_id"}
    route_fingerprint = {k: v for k, v in route_transport.fingerprints[0].items()
                         if k != "thread_id"}
    assert direct_fingerprint == route_fingerprint
    # A sync FastAPI endpoint under TestClient runs in an AnyIO worker thread. This is
    # the only observed execution-context difference; it does not change request fields.
    assert direct_transport.fingerprints[0]["thread_id"] != route_transport.fingerprints[0]["thread_id"]


def test_provider_error_is_raised_directly_but_safely_mapped_by_v2_route(
    development_database,
):
    diagnostic = {
        "transport_error_category": "proxy_error",
        "exception_class": "ProxyError",
        "phase": "proxy",
        "response_received": False,
        "host": "prod.api.market",
        "request_method": "GET",
        "timeout_seconds": 15.0,
        "trust_env": True,
        "follow_redirects": False,
        "thread_name": "MainThread",
    }
    direct_transport = FingerprintTransport([
        ProviderResponseError("provider failure", diagnostic=diagnostic),
    ])
    direct_provider = AeroDataBoxFutureFlightProvider(
        api_key="offline-test-secret", transport=direct_transport,
    )
    with pytest.raises(ProviderResponseError) as captured:
        direct_provider.lookup_by_number("DL1575", "2026-08-20")
    assert captured.value.diagnostic == diagnostic

    route_transport = FingerprintTransport([
        ProviderResponseError("provider failure", diagnostic=diagnostic),
    ])
    route_provider = AeroDataBoxFutureFlightProvider(
        api_key="offline-test-secret", transport=route_transport,
    )
    output = execute(route_provider, development_database)

    assert output["v2_response"] == {
        "status": "provider_temporarily_unavailable",
        "itinerary": None,
        "probability_result": None,
        "ambiguous_legs": [],
        "leg": "first",
        "message": "Future schedule provider is temporarily unavailable.",
        "warnings": [],
    }
    assert output["provider_observations"][0]["diagnostic"] == diagnostic
    assert "offline-test-secret" not in json.dumps(output)
