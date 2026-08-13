from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

import backend.flight_connection.api as api_module
from backend.flight_connection.api import create_app, create_default_app
from backend.flight_connection.service import ConnectionRiskService
from backend.flight_connection.v2_schemas import V2ConnectionResponse


class StubV2Service:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def estimate(self, request):
        self.requests.append(request)
        return self.response


def test_v2_endpoint_uses_injected_service_and_schema(development_database):
    service = StubV2Service(V2ConnectionResponse(
        status="ambiguous",
        ambiguous_legs=[{
            "leg": "first",
            "candidates": [{
                "marketing_carrier": "DL", "marketing_flight_number": "DL1234",
                "origin": "ATL", "destination": "JFK",
                "scheduled_departure": "2026-08-20T15:30:00-04:00",
                "scheduled_arrival": "2026-08-20T17:45:00-04:00",
            }],
        }],
    ))
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database), v2_service=service,
    ))
    response = client.post("/api/v2/connection-risk", json={
        "first_flight_number": "dl 1234", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "ambiguous"
    assert response.json()["ambiguous_legs"][0]["candidates"][0]["destination"] == "JFK"
    assert service.requests[0].first_flight_number == "DL1234"


def test_v2_endpoint_accepts_optional_candidate_indices(development_database):
    service = StubV2Service(V2ConnectionResponse(status="schedule_not_found", leg="first"))
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database), v2_service=service,
    ))
    response = client.post("/api/v2/connection-risk", json={
        "first_flight_number": "DL1234", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20", "first_candidate_index": 1,
    })
    assert response.status_code == 200
    assert service.requests[0].first_candidate_index == 1


def test_v2_endpoint_rejects_negative_candidate_index(development_database):
    service = StubV2Service(V2ConnectionResponse(status="schedule_not_found", leg="first"))
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database), v2_service=service,
    ))
    response = client.post("/api/v2/connection-risk", json={
        "first_flight_number": "DL1234", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20", "first_candidate_index": -1,
    })
    assert response.status_code == 422
    assert service.requests == []


def test_unconfigured_v2_provider_is_safe_and_does_not_construct_live_provider(development_database):
    client = TestClient(create_app(service=ConnectionRiskService(development_database)))
    response = client.post("/api/v2/connection-risk", json={
        "first_flight_number": "DL1234", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20",
    })
    assert response.status_code == 404


def test_v2_request_validation_rejects_invalid_flight_number(development_database):
    v2 = StubV2Service(V2ConnectionResponse(status="schedule_not_found", leg="first"))
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database), v2_service=v2,
    ))
    response = client.post("/api/v2/connection-risk", json={
        "first_flight_number": "not/a/flight", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20",
    })
    assert response.status_code == 422
    assert v2.requests == []


def test_v1_endpoint_contract_is_unchanged_when_v2_is_present(development_database):
    v2 = StubV2Service(V2ConnectionResponse(status="schedule_not_found", leg="first"))
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database, simulations=100, seed=4),
        v2_service=v2,
    ))
    response = client.post("/api/v1/connection-risk", json={
        "carrier": "DL", "origin": "ATL", "connection": "JFK", "destination": "BOS",
        "travel_date": "2026-08-20", "first_departure_time": "15:30",
        "first_arrival_time": "17:45", "connecting_departure_time": "19:10",
    })
    assert response.status_code == 200
    assert set(response.json()) == {
        "connection_probability", "scheduled_layover_minutes", "overnight_connection",
        "historical_sample_size", "delay_statistics", "scenarios", "model",
    }
    assert v2.requests == []


def test_provider_factory_is_lazy_and_called_once_on_first_lookup(development_database):
    calls = []

    class NoMatchProvider:
        def lookup_by_number(self, number, travel_date):
            from backend.flight_connection.future_flight_provider import FutureFlightLookupResult
            return FutureFlightLookupResult("no_match", ())

    def factory():
        calls.append(True)
        return NoMatchProvider()

    app = create_app(
        service=ConnectionRiskService(development_database), v2_provider_factory=factory,
    )
    assert calls == []
    response = TestClient(app).post("/api/v2/connection-risk", json={
        "first_flight_number": "DL1234", "second_flight_number": "DL5678",
        "travel_date": "2026-08-20",
    })
    assert response.json()["status"] == "schedule_not_found"
    assert calls == [True]


def test_production_default_app_registers_v2_without_provider_call_at_import_or_startup(
    monkeypatch, development_database,
):
    calls = []

    class NoMatchProvider:
        def lookup_by_number(self, number, travel_date):
            from backend.flight_connection.future_flight_provider import FutureFlightLookupResult
            return FutureFlightLookupResult("no_match", ())

    def factory():
        calls.append("constructed")
        return NoMatchProvider()

    monkeypatch.setenv("FLIGHT_CONNECTION_ENV", "production")
    monkeypatch.setenv("FLIGHT_CONNECTION_DB", str(development_database))
    monkeypatch.setenv("FLIGHT_CONNECTION_CORS_ORIGINS", "https://flight.example")
    monkeypatch.setattr(api_module, "_production_v2_provider_factory", factory)

    app = create_default_app()
    assert any(route.path == "/api/v2/connection-risk" for route in app.routes)
    assert calls == []
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert calls == []
        response = client.post("/api/v2/connection-risk", json={
            "first_flight_number": "DL1575", "second_flight_number": "DL5798",
            "travel_date": "2026-08-20",
        })
    assert response.json()["status"] == "schedule_not_found"
    assert calls == ["constructed"]


def test_production_missing_provider_credential_fails_safely(
    monkeypatch, development_database,
):
    monkeypatch.setenv("FLIGHT_CONNECTION_ENV", "production")
    monkeypatch.setenv("FLIGHT_CONNECTION_DB", str(development_database))
    monkeypatch.setenv("FLIGHT_CONNECTION_CORS_ORIGINS", "https://flight.example")
    monkeypatch.delenv("AERODATABOX_API_KEY", raising=False)

    with TestClient(create_default_app()) as client:
        response = client.post("/api/v2/connection-risk", json={
            "first_flight_number": "DL1575", "second_flight_number": "DL5798",
            "travel_date": "2026-08-20",
        })
    assert response.status_code == 200
    assert response.json() == {
        "status": "provider_configuration_error",
        "itinerary": None,
        "probability_result": None,
        "ambiguous_legs": [],
        "leg": "first",
        "message": "Future schedule provider is not configured correctly.",
        "warnings": [],
    }
    assert "AERODATABOX_API_KEY" not in response.text
