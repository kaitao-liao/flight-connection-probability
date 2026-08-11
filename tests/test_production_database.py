import duckdb

from backend.flight_connection.production_database import build_production_database
from backend.flight_connection.schemas import ConnectionRiskRequest
from backend.flight_connection.service import ConnectionRiskService
from datetime import date, time, timedelta
from pathlib import Path


def test_production_database_keeps_only_exact_serving_projection(tmp_path):
    source = tmp_path / "source.duckdb"
    destination = tmp_path / "production.duckdb"
    with duckdb.connect(str(source)) as connection:
        connection.execute("""
            CREATE TABLE historical_flights AS SELECT
              DATE '2025-01-01' AS flight_date, 2025::SMALLINT AS year,
              1::TINYINT AS month, 3::TINYINT AS day_of_week,
              'DL'::VARCHAR AS reporting_carrier, 'ATL'::VARCHAR AS origin,
              'JFK'::VARCHAR AS destination, 900::SMALLINT AS crs_departure_minutes,
              1100::SMALLINT AS crs_arrival_minutes, 'morning'::VARCHAR AS departure_time_bucket,
              7.0::DOUBLE AS arrival_delay_minutes, 'source.zip'::VARCHAR AS source_file
        """)

    metadata = build_production_database(source, destination)

    assert metadata["rows"] == 1
    assert metadata["representation"] == "exact_temporal_row_projection_no_filter_no_aggregation"
    with duckdb.connect(str(destination), read_only=True) as connection:
        columns = [row[1] for row in connection.execute(
            "PRAGMA table_info('historical_flights')"
        ).fetchall()]
        assert columns == metadata["columns"]
        assert connection.execute(
            "SELECT arrival_delay_minutes FROM historical_flights"
        ).fetchone()[0] == 7.0


def test_projected_production_database_matches_full_temporal_service(tmp_path):
    source = tmp_path / "full.duckdb"
    destination = tmp_path / "production.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text(encoding="utf-8")
    with duckdb.connect(str(source)) as connection:
        connection.execute(schema)
        rows = []
        for offset in range(40):
            flight_date = date(2024, 1, 1) + timedelta(days=offset)
            rows.append((flight_date, flight_date.year, flight_date.month,
                         flight_date.isoweekday(), "DL", "ATL", "JFK", 930, 1065,
                         "afternoon", float(offset - 10), "test.csv"))
        connection.executemany(
            "INSERT INTO historical_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    build_production_database(source, destination)
    request = ConnectionRiskRequest(
        carrier="DL", origin="ATL", connection="JFK", destination="BOS",
        travel_date=date(2025, 1, 15), first_departure_time=time(15, 30),
        first_arrival_time=time(17, 45), connecting_departure_time=time(19, 10),
    )
    expected = ConnectionRiskService(source, simulations=500, seed=7).estimate(request)
    actual = ConnectionRiskService(destination, simulations=500, seed=7).estimate(request)
    assert actual == expected
