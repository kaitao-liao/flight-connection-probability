import json
import os
import sys
from datetime import date, datetime, timezone

import httpx

from backend.flight_connection.future_flight_provider import (
    FutureFlightCandidate,
    FutureFlightLookupResult,
    ProviderResponseError,
)
import scripts.v2_aerodatabox_endpoint_diagnostic as diagnostic_script
from scripts.v2_aerodatabox_endpoint_diagnostic import run_diagnostic, run_root_only


class RootClient:
    def __init__(self, result, calls, **settings):
        self.result = result
        self.calls = calls
        self.calls.append(("construct", settings))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get(self, url):
        self.calls.append(("get", url))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Provider:
    def __init__(self, result, calls):
        self.result = result
        self.calls = calls

    def lookup_by_number(self, number, flight_date):
        self.calls.append((number, flight_date))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def candidate():
    return FutureFlightCandidate(
        source="test", retrieved_at_utc=datetime(2026, 8, 12, tzinfo=timezone.utc),
        flight_date=date(2026, 8, 20), marketing_carrier="DL",
        marketing_flight_number="DL1575", operating_carrier="DL",
        operating_flight_number="DL1575", origin_iata="ATL", destination_iata="JFK",
        scheduled_departure_local=datetime(2026, 8, 20, 13, 56),
        scheduled_arrival_local=datetime(2026, 8, 20, 16, 30),
    )


def execute(root_result, flight_result):
    root_calls, provider_calls, factory_calls = [], [], []
    output = run_diagnostic(
        root_client_factory=lambda **settings: RootClient(root_result, root_calls, **settings),
        provider_factory=lambda: factory_calls.append(True) or Provider(flight_result, provider_calls),
    )
    return output, root_calls, provider_calls, factory_calls


def test_root_301_then_exactly_one_unique_flight_lookup():
    output, root_calls, provider_calls, factory_calls = execute(
        httpx.Response(301, request=httpx.Request("GET", "https://prod.api.market/"),
                       extensions={"http_version": b"HTTP/1.1"}),
        FutureFlightLookupResult("unique_match", (candidate(),)),
    )
    assert output["root_check"]["http_status"] == 301
    assert output["root_check"]["http_version"] == "HTTP/1.1"
    assert output["flight_lookup"]["outcome"] == "unique_match"
    assert output["flight_lookup"]["required_complete"] == 1
    assert provider_calls == [("DL1575", "2026-08-20")]
    assert factory_calls == [True]
    settings = root_calls[0][1]
    assert settings["follow_redirects"] is False
    assert settings["trust_env"] is True
    assert float(settings["timeout"].connect) == 15.0
    assert root_calls[1] == ("get", "https://prod.api.market/")


def test_root_301_then_flight_connect_error_preserves_safe_diagnostic():
    diagnostic = {
        "transport_error_category": "connect_error_unknown",
        "exception_class": "ConnectError", "phase": "connect",
        "response_received": False, "host": "prod.api.market",
        "request_method": "GET", "timeout_seconds": 15.0,
        "trust_env": True, "follow_redirects": False, "thread_name": "MainThread",
    }
    output, _, provider_calls, _ = execute(
        httpx.Response(301, request=httpx.Request("GET", "https://prod.api.market/")),
        ProviderResponseError("safe", diagnostic=diagnostic),
    )
    assert provider_calls == [("DL1575", "2026-08-20")]
    assert output["flight_lookup"]["diagnostic"] == diagnostic


def test_root_failure_prevents_provider_construction_and_lookup():
    request = httpx.Request("GET", "https://prod.api.market/")
    output, _, provider_calls, factory_calls = execute(
        httpx.ConnectError("unsafe", request=request),
        FutureFlightLookupResult("unique_match", (candidate(),)),
    )
    assert output["root_check"]["status"] == "failure"
    assert output["flight_lookup"] == {"status": "not_executed"}
    assert provider_calls == []
    assert factory_calls == []


def test_output_never_contains_secret_or_raw_exception_text():
    secret = "never-emit-this-secret"
    request = httpx.Request("GET", "https://prod.api.market/")
    output, *_ = execute(
        httpx.ConnectError(f"unsafe {secret}", request=request),
        FutureFlightLookupResult("no_match", ()),
    )
    serialized = json.dumps(output)
    assert secret not in serialized
    assert "unsafe" not in serialized
    assert "headers" not in serialized.lower()


def test_root_only_is_credential_free_one_request_and_does_not_follow_redirects(
    monkeypatch,
):
    root_calls = []
    monkeypatch.delenv("AERODATABOX_API_KEY", raising=False)
    output = run_root_only(root_client_factory=lambda **settings: RootClient(
        httpx.Response(
            301, request=httpx.Request("GET", "https://prod.api.market/"),
            extensions={"http_version": b"HTTP/1.1"},
        ), root_calls, **settings,
    ))
    assert output["root_check"]["status"] == "success"
    assert output["root_check"]["http_status"] == 301
    assert root_calls[1:] == [("get", "https://prod.api.market/")]
    settings = root_calls[0][1]
    assert settings["follow_redirects"] is False
    assert settings["trust_env"] is True
    assert settings["timeout"].connect == 15.0
    assert output["runtime"]["python_executable"] == sys.executable
    assert set(output["environment_present"]) == set(diagnostic_script.ENVIRONMENT_FLAGS)
    assert all(isinstance(value, bool) for value in output["environment_present"].values())


def test_root_only_cli_never_loads_env_or_constructs_provider(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(diagnostic_script, "load_local_env", lambda: calls.append("env"))
    monkeypatch.setattr(
        diagnostic_script, "AeroDataBoxFutureFlightProvider",
        lambda: calls.append("provider"),
    )
    monkeypatch.setattr(
        diagnostic_script, "run_root_only",
        lambda: {"root_check": {"status": "success"}},
    )
    monkeypatch.setattr(sys, "argv", ["diagnostic", "--root-only"])
    diagnostic_script.main()
    assert json.loads(capsys.readouterr().out)["root_check"]["status"] == "success"
    assert calls == []


def test_root_only_failure_diagnostic_is_sanitized(monkeypatch):
    secret = "never-print-root-secret"
    request = httpx.Request("GET", "https://prod.api.market/")
    output = run_root_only(root_client_factory=lambda **settings: RootClient(
        httpx.ConnectError(f"unsafe {secret}", request=request), [], **settings,
    ))
    serialized = json.dumps(output)
    assert output["root_check"]["status"] == "failure"
    assert output["root_check"]["diagnostic"]["response_received"] is False
    assert secret not in serialized
    assert "unsafe" not in serialized
