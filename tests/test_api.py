from fastapi.testclient import TestClient

from backend.flight_connection.api import create_app
from backend.flight_connection.service import ConnectionRiskService


VALID_REQUEST = {
    "carrier": "DL",
    "origin": "ATL",
    "connection": "JFK",
    "destination": "BOS",
    "travel_date": "2026-08-20",
    "first_departure_time": "15:30",
    "first_arrival_time": "17:45",
    "connecting_departure_time": "19:10",
}


def test_response_schema_and_probability_bounds(development_database):
    service = ConnectionRiskService(development_database, simulations=500, seed=9)
    client = TestClient(create_app(service=service))
    response = client.post("/api/v1/connection-risk", json=VALID_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "connection_probability", "scheduled_layover_minutes",
        "overnight_connection", "historical_sample_size", "delay_statistics", "scenarios", "model",
    }
    assert 0 <= body["connection_probability"] <= 1
    assert all(0 <= probability <= 1 for probability in body["scenarios"].values())
    assert body["model"]["arrival_delay_evidence"] == "observed_completed_non_diverted_BTS_flights"
    assert body["model"]["deplaning_time"] == {
        "fixed_minutes": 20.0, "evidence_type": "modeling_assumption",
    }
    assert body["model"]["transfer_time"]["evidence_type"] == "modeling_assumption"
    assert body["model"]["transfer_time"] == {
        "distribution": "triangular", "minimum_minutes": 15.0,
        "mode_minutes": 25.0, "maximum_minutes": 40.0,
        "evidence_type": "modeling_assumption",
    }


def test_invalid_airport_code_returns_422(development_database):
    client = TestClient(create_app(service=ConnectionRiskService(development_database)))
    payload = {**VALID_REQUEST, "origin": "ATL1"}
    response = client.post("/api/v1/connection-risk", json=payload)
    assert response.status_code == 422


def test_physically_impossible_cross_timezone_schedule_returns_422(development_database):
    client = TestClient(create_app(service=ConnectionRiskService(development_database)))
    payload = {
        **VALID_REQUEST,
        "origin": "LAX",
        "connection": "ATL",
        "first_departure_time": "15:30",
        "first_arrival_time": "17:45",
        "connecting_departure_time": "18:10",
    }
    response = client.post("/api/v1/connection-risk", json=payload)
    assert response.status_code == 422
    assert "local time zones" in response.json()["detail"]
    assert "connection_probability" not in response.json()


def test_unknown_airport_timezone_returns_frontend_friendly_422(development_database):
    client = TestClient(create_app(service=ConnectionRiskService(development_database)))
    response = client.post(
        "/api/v1/connection-risk", json={**VALID_REQUEST, "destination": "XYZ"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "Timezone data is unavailable for airport XYZ. Use a supported U.S. airport code."
    }


def test_seed_makes_api_response_deterministic(development_database):
    client = TestClient(create_app(service=ConnectionRiskService(development_database, simulations=300, seed=42)))
    first = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    second = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    assert first == second


def test_default_api_response_is_deterministic(development_database):
    service = ConnectionRiskService(development_database, simulations=500)
    client = TestClient(create_app(service=service))
    first = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    second = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    assert first == second
    assert first["model"]["random_seed"] is not None


def test_api_request_date_is_a_strict_temporal_cutoff(development_database):
    import duckdb

    service = ConnectionRiskService(development_database, simulations=300, seed=42)
    client = TestClient(create_app(service=service))
    before = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    with duckdb.connect(str(development_database)) as connection:
        connection.execute("""
            INSERT INTO historical_flights VALUES (
              '2026-08-20', 2026, 8, 4, 'DL', 'ATL', 'JFK', 930, 1065,
              'afternoon', 9999.0, 'future-leak.csv'
            )
        """)
    after = client.post("/api/v1/connection-risk", json=VALID_REQUEST).json()
    assert before["connection_probability"] == after["connection_probability"]
    assert before["historical_sample_size"] == after["historical_sample_size"]
    assert before["delay_statistics"] == after["delay_statistics"]
    assert before["scenarios"] == after["scenarios"]
    assert after["model"]["historical_coverage"]["strict_cutoff_exclusive"] == "2026-08-20"


def test_cors_allows_configured_frontend_origin(development_database):
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database),
        allowed_origins=["http://localhost:3000"],
    ))
    response = client.options(
        "/api/v1/connection-risk",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_does_not_allow_unconfigured_origin(development_database):
    client = TestClient(create_app(
        service=ConnectionRiskService(development_database),
        allowed_origins=["http://localhost:3000"],
    ))
    response = client.options(
        "/api/v1/connection-risk",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_wildcard_cors_is_rejected(development_database):
    try:
        create_app(
            service=ConnectionRiskService(development_database),
            allowed_origins=["*"],
        )
    except RuntimeError as error:
        assert "wildcard" in str(error)
    else:
        raise AssertionError("wildcard CORS origin should be rejected")


def test_production_requires_database_and_cors_environment(monkeypatch):
    monkeypatch.setenv("FLIGHT_CONNECTION_ENV", "production")
    monkeypatch.delenv("FLIGHT_CONNECTION_DB", raising=False)
    monkeypatch.delenv("FLIGHT_CONNECTION_CORS_ORIGINS", raising=False)
    try:
        create_app()
    except RuntimeError as error:
        assert "FLIGHT_CONNECTION_DB" in str(error)
    else:
        raise AssertionError("production should require an explicit database")


def test_startup_fails_when_database_is_missing(tmp_path):
    app = create_app(
        database=tmp_path / "missing.duckdb",
        allowed_origins=["https://flight.example"],
    )
    try:
        with TestClient(app):
            pass
    except RuntimeError as error:
        assert "does not exist" in str(error)
    else:
        raise AssertionError("startup should fail for a missing database")
