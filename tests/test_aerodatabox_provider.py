import json
import errno
import socket
import ssl
from datetime import datetime, timezone

import httpx
import pytest
import scripts.v2_aerodatabox_live_audit as live_audit

from backend.flight_connection.aerodatabox_provider import (
    AUTH_HEADER, AeroDataBoxFutureFlightProvider, HttpResponse, HttpxTransport, redact_secrets,
    sanitized_response_diagnostic,
)
from backend.flight_connection.future_flight_provider import (
    FutureFlightLookupResult,
    MissingProviderCredential, MissingRequiredScheduleFields,
    ProviderAuthenticationError, ProviderQuotaError, ProviderRateLimitError,
    ProviderResponseError, ProviderTimeoutError,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


class MockTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        if self.error:
            raise self.error
        return self.response


def provider(response=None, error=None):
    transport = MockTransport(response=response, error=error)
    return (
        AeroDataBoxFutureFlightProvider(
            api_key="test-secret", transport=transport, now_utc=lambda: NOW
        ),
        transport,
    )


def response(payload, status=200):
    return HttpResponse(status, json.dumps(payload).encode())


def flight(number="DL1234", *, origin="ATL", destination="MIA", codeshare="IsOperator"):
    return {
        "number": number,
        "status": "Expected",
        "codeshareStatus": codeshare,
        "callSign": "DAL1234",
        "airline": {"name": "Delta Air Lines", "iata": "DL"},
        "departure": {
            "airport": {"iata": origin},
            "scheduledTime": {"local": "2026-08-20 20:52-04:00"},
            "terminal": "S", "gate": "B18", "quality": ["Basic"],
        },
        "arrival": {
            "airport": {"iata": destination},
            "scheduledTime": {"local": "2026-08-20 22:47-04:00"},
            "terminal": "N", "gate": "D5", "quality": ["Basic", "Live"],
        },
        "aircraft": {"model": "Boeing 737-900"},
    }


def test_successful_normalization_and_request_contract():
    adapter, transport = provider(response([flight()]))
    result = adapter.lookup_by_number("dl 1234", "2026-08-20")
    assert result.outcome == "unique_match"
    candidate = result.candidates[0]
    assert (candidate.origin_iata, candidate.destination_iata) == ("ATL", "MIA")
    assert candidate.marketing_flight_number == "DL1234"
    assert candidate.operating_flight_number == "DL1234"
    assert candidate.departure_terminal == "S"
    assert candidate.arrival_gate == "D5"
    assert candidate.quality == ("departure:Basic", "arrival:Basic", "arrival:Live")
    url, params, headers, timeout = transport.calls[0]
    assert url == (
        "https://prod.api.market/api/v1/aedbx/aerodatabox/"
        "flights/Number/DL1234/2026-08-20"
    )
    assert params == {
        "dateLocalRole": "Departure", "withAircraftImage": "false",
        "withLocation": "false", "withFlightPlan": "false",
    }
    assert headers[AUTH_HEADER] == "test-secret"
    assert headers == {"x-api-market-key": "test-secret", "Accept": "application/json"}
    assert "Content-Type" not in headers
    assert timeout == 15.0
    assert len(transport.calls) == 1


def test_http_200_with_quota_headers_is_still_successful():
    adapter, transport = provider(HttpResponse(
        200,
        json.dumps([flight()]).encode(),
        {
            "x-api-units-used": "2",
            "x-api-units-remaining": "0",
            "x-ratelimit-remaining": "0",
            "x-subscription-status": "unexpected-value",
        },
    ))
    result = adapter.lookup_by_number("DL1234", "2026-08-20")
    assert result.outcome == "unique_match"
    assert len(transport.calls) == 1


def test_missing_optional_terminal_and_gate_are_none():
    item = flight()
    for movement in (item["departure"], item["arrival"]):
        movement.pop("terminal")
        movement.pop("gate")
    adapter, _ = provider(response([item]))
    candidate = adapter.lookup_by_number("DL1234", "2026-08-20").candidates[0]
    assert candidate.departure_terminal is None
    assert candidate.arrival_terminal is None
    assert candidate.departure_gate is None
    assert candidate.arrival_gate is None


def test_codeshare_preserves_marketing_identity_without_inventing_operator():
    adapter, _ = provider(response([flight(number="AA7000", codeshare="IsCodeshared")]))
    candidate = adapter.lookup_by_number("AA7000", "2026-08-20").candidates[0]
    assert candidate.marketing_flight_number == "AA7000"
    assert candidate.codeshare_status == "IsCodeshared"
    assert candidate.operating_carrier is None
    assert candidate.operating_flight_number is None


def test_multiple_candidates_are_deterministically_ordered():
    later = flight(origin="BOS", destination="JFK")
    earlier = flight(origin="ATL", destination="MIA")
    adapter, _ = provider(response([later, earlier]))
    result = adapter.lookup_by_number("DL1234", "2026-08-20")
    assert result.outcome == "multiple_candidates"
    assert [candidate.origin_iata for candidate in result.candidates] == ["ATL", "BOS"]


def test_no_match_for_no_content_and_empty_array():
    adapter, _ = provider(HttpResponse(204))
    assert adapter.lookup_by_number("DL1234", "2026-08-20").outcome == "no_match"
    adapter, _ = provider(response([]))
    assert adapter.lookup_by_number("DL1234", "2026-08-20").outcome == "no_match"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, ProviderAuthenticationError), (402, ProviderQuotaError),
     (429, ProviderRateLimitError), (504, ProviderTimeoutError)],
)
def test_provider_status_errors(status, error):
    adapter, _ = provider(HttpResponse(status, b'{"message":"not included"}'))
    with pytest.raises(error) as captured:
        adapter.lookup_by_number("DL1234", "2026-08-20")
    assert "test-secret" not in str(captured.value)


def test_403_requires_machine_readable_quota_evidence():
    adapter, transport = provider(HttpResponse(403, b'{"message":"Forbidden"}'))
    with pytest.raises(ProviderResponseError, match="HTTP 403") as captured:
        adapter.lookup_by_number("DL1234", "2026-08-20")
    assert not isinstance(captured.value, ProviderQuotaError)
    assert len(transport.calls) == 1

    adapter, _ = provider(HttpResponse(403, b'{"code":"quota_exceeded"}'))
    with pytest.raises(ProviderQuotaError, match="exhausted"):
        adapter.lookup_by_number("DL1234", "2026-08-20")


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [(401, ProviderAuthenticationError), (429, ProviderRateLimitError),
     (500, ProviderResponseError)],
)
def test_status_mapping_is_preserved_with_sanitized_diagnostic(status, expected_error):
    body = b'{"code":"upstream_error","message":"Safe provider message"}'
    adapter, _ = provider(HttpResponse(
        status,
        body,
        {"Content-Type": "application/json", "X-Request-Id": "request-123"},
        "Failure",
    ))
    with pytest.raises(expected_error) as captured:
        adapter.lookup_by_number("DL1234", "2026-08-20")
    assert captured.value.diagnostic == {
        "status": status,
        "reason_phrase": "Failure",
        "content_type": "application/json",
        "server": None,
        "request_ids": {"x-request-id": "request-123"},
        "header_names": ["content-type", "x-request-id"],
        "body_kind": "json",
        "body_length": len(body),
        "json_top_level_keys": ["code", "message"],
        "error_code": "upstream_error",
        "message": "Safe provider message",
    }


def test_html_gateway_diagnostic_has_marker_but_never_raw_body_or_secrets():
    secret = "secret-looking-api-key-value"
    diagnostic = sanitized_response_diagnostic(HttpResponse(
        403,
        f"<html><title>Access Denied</title>{secret}</html>".encode(),
        {
            "Content-Type": "text/html; charset=utf-8",
            "Server": "cloudflare",
            "CF-Ray": "ray-123",
            "Set-Cookie": secret,
            "X-Api-Key-Debug": secret,
        },
        "Forbidden",
    ))
    serialized = json.dumps(diagnostic)
    assert diagnostic["body_kind"] == "html"
    assert diagnostic["body_marker"] == "access_denied"
    assert diagnostic["request_ids"] == {"cf-ray": "ray-123"}
    assert diagnostic["header_names"] == ["cf-ray", "content-type", "server"]
    assert "cookie" not in serialized.lower()
    assert "api-key" not in serialized.lower()
    assert secret not in serialized
    assert "body" not in diagnostic


def test_empty_and_plain_text_diagnostics_are_bounded():
    empty = sanitized_response_diagnostic(HttpResponse(500, b"", {}))
    assert empty["body_kind"] == "empty"
    assert empty["body_length"] == 0
    assert "body_marker" not in empty

    text = sanitized_response_diagnostic(HttpResponse(
        403, b"Forbidden: API key abc123", {"Content-Type": "text/plain"}
    ))
    assert text["body_kind"] == "text"
    assert text["body_marker"] == "forbidden"
    assert "abc123" not in json.dumps(text)


def test_json_secret_like_message_is_redacted_and_keys_are_sorted():
    secret = "never-persist-this-key"
    diagnostic = sanitized_response_diagnostic(HttpResponse(
        403,
        json.dumps({"z": 1, "message": f"API key {secret}", "code": "forbidden", "a": 2}).encode(),
        {"X-Correlation-ID": "correlation-1", "Content-Type": "application/json"},
    ))
    assert diagnostic["json_top_level_keys"] == ["a", "code", "message", "z"]
    assert diagnostic["header_names"] == ["content-type", "x-correlation-id"]
    assert diagnostic["error_code"] == "forbidden"
    assert diagnostic["message"] == "[REDACTED]"
    assert secret not in json.dumps(diagnostic)

    token = "AbCdEf0123456789AbCdEf0123456789"
    token_diagnostic = sanitized_response_diagnostic(HttpResponse(
        403, json.dumps({"message": f"Rejected credential {token}"}).encode(),
        {"Content-Type": "application/json"},
    ))
    assert token_diagnostic["message"] == "[REDACTED]"
    assert token not in json.dumps(token_diagnostic)


def test_transport_timeout_is_preserved_without_secret():
    adapter, _ = provider(error=ProviderTimeoutError("Future-flight provider request timed out"))
    with pytest.raises(ProviderTimeoutError, match="timed out") as captured:
        adapter.lookup_by_number("DL1234", "2026-08-20")
    assert "test-secret" not in str(captured.value)


def test_malformed_response_and_missing_required_schedule_fields():
    adapter, _ = provider(HttpResponse(200, b"not-json"))
    with pytest.raises(ProviderResponseError, match="malformed JSON"):
        adapter.lookup_by_number("DL1234", "2026-08-20")
    item = flight()
    item["arrival"].pop("scheduledTime")
    adapter, _ = provider(response([item]))
    with pytest.raises(MissingRequiredScheduleFields, match="required schedule"):
        adapter.lookup_by_number("DL1234", "2026-08-20")


def test_missing_key_fails_before_transport(monkeypatch):
    monkeypatch.delenv("AERODATABOX_API_KEY", raising=False)
    with pytest.raises(MissingProviderCredential, match="AERODATABOX_API_KEY"):
        AeroDataBoxFutureFlightProvider(transport=MockTransport())


def test_secret_redaction_covers_headers_nested_values_and_messages():
    secret = "never-print-this"
    value = {
        AUTH_HEADER: secret,
        "nested": {"Authorization": f"Bearer {secret}"},
        "message": f"request rejected for {secret}",
    }
    redacted = redact_secrets(value, secrets=(secret,))
    serialized = json.dumps(redacted)
    assert secret not in serialized
    assert serialized.count("[REDACTED]") == 3


def test_invalid_inputs_do_not_call_transport():
    adapter, transport = provider(response([]))
    with pytest.raises(ValueError, match="flight_number"):
        adapter.lookup_by_number("not a flight", "2026-08-20")
    with pytest.raises(ValueError):
        adapter.lookup_by_number("DL1234", "not-a-date")
    assert transport.calls == []


def test_httpx_transport_sends_exactly_one_request_with_required_contract():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"[]")

    mock = httpx.MockTransport(handler)
    transport = HttpxTransport(
        client_factory=lambda **kwargs: httpx.Client(transport=mock, **kwargs)
    )
    result = transport.get(
        "https://prod.api.market/api/v1/aedbx/aerodatabox/flights/Number/DL1234/2026-08-20",
        params={
            "dateLocalRole": "Departure", "withAircraftImage": "false",
            "withLocation": "false", "withFlightPlan": "false",
        },
        headers={AUTH_HEADER: "test-secret", "Accept": "application/json"}, timeout=3,
    )
    assert result.status_code == 200
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "https://prod.api.market/api/v1/aedbx/aerodatabox/"
        "flights/Number/DL1234/2026-08-20?dateLocalRole=Departure&"
        "withAircraftImage=false&withLocation=false&withFlightPlan=false"
    )
    assert request.method == "GET"
    assert request.headers[AUTH_HEADER] == "test-secret"
    assert request.headers["Accept"] == "application/json"
    assert "content-type" not in request.headers


def test_httpx_transport_does_not_follow_redirects_or_retry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/"})

    mock = httpx.MockTransport(handler)
    transport = HttpxTransport(
        client_factory=lambda **kwargs: httpx.Client(transport=mock, **kwargs)
    )
    result = transport.get(
        "https://example.invalid/flights", params={},
        headers={AUTH_HEADER: "test-secret", "Accept": "application/json"}, timeout=3,
    )
    assert result.status_code == 302
    assert len(requests) == 1


def test_httpx_transport_redacts_request_errors():
    def handler(request):
        raise httpx.ConnectError(f"failed with {request.headers[AUTH_HEADER]}", request=request)

    mock = httpx.MockTransport(handler)
    transport = HttpxTransport(
        client_factory=lambda **kwargs: httpx.Client(transport=mock, **kwargs)
    )
    with pytest.raises(ProviderResponseError, match="network request failed") as captured:
        transport.get(
            "https://example.invalid/flights", params={},
            headers={AUTH_HEADER: "test-secret", "Accept": "application/json"}, timeout=3,
        )
    assert "test-secret" not in str(captured.value)


@pytest.mark.parametrize(
    ("httpx_error", "provider_error", "category", "phase"),
    [
        (httpx.ConnectTimeout, ProviderTimeoutError, "connect_timeout", "connect"),
        (httpx.ReadTimeout, ProviderTimeoutError, "read_timeout", "read"),
        (httpx.WriteTimeout, ProviderTimeoutError, "write_timeout", "write"),
        (httpx.PoolTimeout, ProviderTimeoutError, "pool_timeout", "pool"),
        (httpx.ConnectError, ProviderResponseError, "connect_error_unknown", "connect"),
        (httpx.ReadError, ProviderResponseError, "read_error", "read"),
        (httpx.WriteError, ProviderResponseError, "write_error", "write"),
        (httpx.CloseError, ProviderResponseError, "close_error", "network"),
        (httpx.ProxyError, ProviderResponseError, "proxy_error", "proxy"),
        (httpx.RemoteProtocolError, ProviderResponseError,
         "remote_protocol_error", "protocol"),
        (httpx.LocalProtocolError, ProviderResponseError,
         "local_protocol_error", "protocol"),
        (httpx.NetworkError, ProviderResponseError, "network_error", "network"),
        (httpx.TransportError, ProviderResponseError, "transport_error", "unknown"),
        (httpx.RequestError, ProviderResponseError, "request_error", "unknown"),
    ],
)
def test_transport_exception_categories_are_safe_and_deterministic(
    httpx_error, provider_error, category, phase,
):
    secret = "offline-secret-never-emit"

    def handler(request):
        raise httpx_error(
            f"unsafe {secret} https://proxy-user:proxy-pass@example.invalid",
            request=request,
        )

    mock = httpx.MockTransport(handler)
    transport = HttpxTransport(
        client_factory=lambda **kwargs: httpx.Client(transport=mock, **kwargs)
    )
    with pytest.raises(provider_error) as captured:
        transport.get(
            "https://prod.api.market/private/query?flight=secret", params={},
            headers={AUTH_HEADER: secret, "Accept": "application/json"}, timeout=3,
        )

    diagnostic = captured.value.diagnostic
    assert diagnostic == {
        "transport_error_category": category,
        "exception_class": httpx_error.__name__,
        "phase": phase,
        "response_received": False,
        "host": "prod.api.market",
        "request_method": "GET",
        "timeout_seconds": 3,
        "trust_env": True,
        "follow_redirects": False,
        "thread_name": "MainThread",
    }
    serialized = json.dumps(diagnostic)
    assert secret not in serialized
    assert "proxy-user" not in serialized
    assert "flight" not in serialized
    assert AUTH_HEADER not in serialized


@pytest.mark.parametrize(
    ("cause", "category"),
    [
        (socket.gaierror(-2, "unsafe DNS detail"), "dns_error"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "unsafe refusal"),
         "connection_refused"),
        (ConnectionResetError(errno.ECONNRESET, "unsafe reset"), "connection_reset"),
        (ssl.SSLError("unsafe TLS detail"), "tls_error"),
    ],
)
def test_connect_error_uses_only_structured_cause_for_safe_subclassification(
    cause, category,
):
    def handler(request):
        try:
            raise cause
        except BaseException as underlying:
            raise httpx.ConnectError("unsafe outer detail", request=request) from underlying

    mock = httpx.MockTransport(handler)
    transport = HttpxTransport(
        client_factory=lambda **kwargs: httpx.Client(transport=mock, **kwargs)
    )
    with pytest.raises(ProviderResponseError) as captured:
        transport.get(
            "https://prod.api.market/test", params={},
            headers={AUTH_HEADER: "offline-secret", "Accept": "application/json"},
            timeout=15,
        )
    assert captured.value.diagnostic["transport_error_category"] == category
    assert "unsafe" not in json.dumps(captured.value.diagnostic)


def test_one_live_audit_lookup_calls_provider_once(monkeypatch, capsys):
    class CountingProvider:
        calls = []

        def lookup_by_number(self, flight_number, flight_date):
            self.calls.append((flight_number, flight_date))
            return FutureFlightLookupResult("no_match", ())

    monkeypatch.setattr(live_audit, "AeroDataBoxFutureFlightProvider", CountingProvider)
    monkeypatch.setattr(live_audit, "load_local_env", lambda: None)
    monkeypatch.setattr(
        "sys.argv",
        ["v2_aerodatabox_live_audit", "--confirm-live", "--lookup", "DL1234:2026-08-20"],
    )
    live_audit.main()
    output = json.loads(capsys.readouterr().out)
    assert len(CountingProvider.calls) == 1
    assert output["summary"] == {"attempted": 1, "no_match": 1, "returned_candidates": 0}


def test_live_audit_prints_only_sanitized_error_diagnostic(monkeypatch, capsys):
    diagnostic = {
        "transport_error_category": "connect_timeout",
        "exception_class": "ConnectTimeout",
        "phase": "connect",
        "response_received": False,
        "host": "prod.api.market",
        "request_method": "GET",
        "timeout_seconds": 15.0,
        "trust_env": True,
        "follow_redirects": False,
        "thread_name": "MainThread",
    }

    class FailingProvider:
        calls = []

        def lookup_by_number(self, flight_number, flight_date):
            self.calls.append((flight_number, flight_date))
            raise ProviderResponseError(
                "Future-flight provider network request failed",
                diagnostic=diagnostic,
            )

    monkeypatch.setattr(live_audit, "AeroDataBoxFutureFlightProvider", FailingProvider)
    monkeypatch.setattr(live_audit, "load_local_env", lambda: None)
    monkeypatch.setattr(
        "sys.argv",
        ["v2_aerodatabox_live_audit", "--confirm-live", "--lookup", "DL1234:2026-09-14"],
    )
    live_audit.main()
    output = json.loads(capsys.readouterr().out)
    record = output["lookups"][0]
    assert len(FailingProvider.calls) == 1
    assert record["diagnostic"] == diagnostic
    assert AUTH_HEADER not in json.dumps(output)
