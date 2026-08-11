from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def development_database(tmp_path: Path) -> Path:
    database = tmp_path / "flights.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text(encoding="utf-8")
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        rows = [
            ("2024-08-20", 2024, 8, 2, "DL", "ATL", "JFK", 930, 1065,
             "afternoon", float(delay), "synthetic-unit-test.csv")
            for delay in range(-10, 30)
        ]
        connection.executemany("INSERT INTO historical_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return database

