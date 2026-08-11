from datetime import date
from pathlib import Path
import duckdb
from backend.flight_connection.delay_model import (
    TemporalHistory, historical_delay_cohort_counts, historical_delay_distribution,
    subtract_months, temporal_delay_cohorts,
)


def test_falls_back_from_exact_to_route_carrier(tmp_path: Path):
    database = tmp_path / "test.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as con:
        con.execute(schema)
        for day in range(1, 6):
            con.execute("INSERT INTO historical_flights VALUES (?, 2024, 1, ?, 'AA', 'BOS', 'ORD', 480, 600, 'morning', ?, 'synthetic-test.csv')", [f"2024-01-0{day}", day, day * 2])
    result = historical_delay_distribution(database, carrier="AA", origin="BOS", destination="ORD", travel_date=date(2025, 7, 1), scheduled_departure_minutes=480, min_observations=4)
    assert result.fallback_level == "route_carrier"
    assert result.observation_count == 5


def test_representative_cohort_counts(tmp_path: Path):
    database = tmp_path / "test.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as con:
        con.execute(schema)
        for day in range(1, 6):
            con.execute("INSERT INTO historical_flights VALUES (?, 2024, 1, 1, 'DL', 'ATL', 'JFK', 930, 1065, 'afternoon', ?, 'test.csv')", [f"2024-01-0{day}", day])
    counts = historical_delay_cohort_counts(
        database, carrier="DL", origin="ATL", destination="JFK",
        travel_date=date(2025, 1, 6), scheduled_departure_minutes=930,
    )
    assert counts["exact"] == 5
    assert counts["route_carrier"] == 5
    assert counts["global"] == 5


def test_exact_24_month_lower_bound_and_strict_upper_bound(tmp_path: Path):
    database = tmp_path / "boundaries.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        for flight_date, delay in (
            ("2023-03-30", 999.0),
            ("2023-03-31", 10.0),
            ("2025-03-30", 20.0),
            ("2025-03-31", 888.0),
        ):
            connection.execute("""
                INSERT INTO historical_flights VALUES (
                  ?::DATE, year(?::DATE), month(?::DATE), isodow(?::DATE),
                  'DL', 'ATL', 'JFK', 930, 1065, 'afternoon', ?, 'test.csv'
                )
            """, [flight_date, flight_date, flight_date, flight_date, delay])
    result = historical_delay_distribution(
        database, carrier="DL", origin="ATL", destination="JFK",
        travel_date=date(2025, 3, 31), scheduled_departure_minutes=930,
        min_observations=2,
    )
    assert sorted(result.samples_minutes.tolist()) == [10.0, 20.0]
    assert result.coverage.effective_start == date(2023, 3, 31)
    assert result.coverage.effective_end == date(2025, 3, 30)


def test_24_month_bounds_handle_leap_dates():
    assert subtract_months(date(2024, 2, 29), 24) == date(2022, 2, 28)
    assert subtract_months(date(2025, 3, 31), 24) == date(2023, 3, 31)


def test_future_prediction_uses_only_available_rows_and_reports_coverage(tmp_path: Path):
    database = tmp_path / "future.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        for flight_date, delay in (("2023-12-31", 999.0), ("2024-01-15", 5.0), ("2025-12-31", 15.0)):
            connection.execute("""
                INSERT INTO historical_flights VALUES (
                  ?::DATE, year(?::DATE), month(?::DATE), isodow(?::DATE),
                  'DL', 'ATL', 'JFK', 930, 1065, 'afternoon', ?, 'test.csv'
                )
            """, [flight_date, flight_date, flight_date, flight_date, delay])
    result = historical_delay_distribution(
        database, carrier="DL", origin="ATL", destination="JFK",
        travel_date=date(2026, 1, 15), scheduled_departure_minutes=930,
        min_observations=2,
    )
    assert sorted(result.samples_minutes.tolist()) == [5.0, 15.0]
    assert result.coverage.available_end == date(2025, 12, 31)
    assert result.coverage.cutoff_exclusive == date(2026, 1, 15)
    assert result.coverage.freshness_warning is None
