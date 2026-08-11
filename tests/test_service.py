from datetime import date, time

import pytest

from backend.flight_connection.schemas import ConnectionRiskRequest
from backend.flight_connection.service import ConnectionRiskService, elapsed_minutes


def itinerary(**overrides) -> ConnectionRiskRequest:
    values = {
        "carrier": "DL",
        "origin": "ATL",
        "connection": "JFK",
        "destination": "BOS",
        "travel_date": date(2026, 8, 20),
        "first_departure_time": time(15, 30),
        "first_arrival_time": time(17, 45),
        "connecting_departure_time": time(19, 10),
    }
    values.update(overrides)
    return ConnectionRiskRequest(**values)


def test_same_day_connection(development_database):
    result = ConnectionRiskService(development_database, simulations=500, seed=4).estimate(itinerary())
    assert result.scheduled_layover_minutes == 85
    assert result.overnight_connection is False
    assert result.model.cohort_level == "route_carrier_month_bucket"
    assert result.model.historical_coverage.lookback_months == 24
    assert result.model.historical_coverage.available_end_date == date(2024, 8, 20)
    assert result.model.historical_coverage.effective_history_start_date == date(2024, 8, 20)
    assert result.model.historical_coverage.strict_cutoff_exclusive == date(2026, 8, 20)
    assert "Historical BTS data ends" in result.model.historical_coverage.freshness_warning
    assert 0 <= result.connection_probability <= 1


def test_overnight_connection(development_database):
    request = itinerary(
        first_departure_time=time(20), first_arrival_time=time(22),
        connecting_departure_time=time(6),
    )
    result = ConnectionRiskService(development_database, simulations=100, seed=1).estimate(request)
    assert result.scheduled_layover_minutes == 480
    assert result.overnight_connection is True


def test_connecting_departure_before_arrival_is_invalid_when_not_overnight(development_database):
    request = itinerary(first_arrival_time=time(15), connecting_departure_time=time(14))
    with pytest.raises(ValueError, match="overnight rule"):
        ConnectionRiskService(development_database).estimate(request)


def test_empty_historical_database_reports_insufficient_data(tmp_path):
    import duckdb
    from pathlib import Path

    database = tmp_path / "empty.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(Path("backend/flight_connection/schema.sql").read_text())
    with pytest.raises(ValueError, match="no eligible historical flights"):
        ConnectionRiskService(database).estimate(itinerary())


def test_elapsed_minutes_rejects_seconds():
    with pytest.raises(ValueError, match="minute precision"):
        elapsed_minutes(time(10, 0, 1), time(11), label="test")
