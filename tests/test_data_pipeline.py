import csv
from pathlib import Path

import duckdb

from backend.flight_connection.data_pipeline import (
    _create_macros, create_development_database, ingest_csv, refresh_historical_flights,
)


FIELDS = [
    "FlightDate", "DayOfWeek", "Reporting_Airline", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "CRSArrTime", "DepDelay", "ArrDelay",
    "Cancelled", "Diverted", "CarrierDelay", "WeatherDelay", "NASDelay",
    "SecurityDelay", "LateAircraftDelay",
]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(**overrides) -> dict:
    result = {
        "FlightDate": "2024-01-08", "DayOfWeek": "1", "Reporting_Airline": "DL",
        "Flight_Number_Reporting_Airline": "100", "Origin": "ATL", "Dest": "JFK",
        "CRSDepTime": "1530", "CRSArrTime": "1745", "DepDelay": "2", "ArrDelay": "-5",
        "Cancelled": "0", "Diverted": "0", "CarrierDelay": "", "WeatherDelay": "",
        "NASDelay": "", "SecurityDelay": "", "LateAircraftDelay": "",
    }
    result.update(overrides)
    return result


def test_ingestion_derives_dates_and_preserves_statuses(tmp_path: Path):
    csv_path = tmp_path / "month.csv"
    write_csv(csv_path, [row(), row(Flight_Number_Reporting_Airline="101", Cancelled="1", ArrDelay=""),
                         row(Flight_Number_Reporting_Airline="102", Diverted="1", ArrDelay="")])
    database = tmp_path / "test.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(Path("backend/flight_connection/schema.sql").read_text())
        _create_macros(connection)
        summary = ingest_csv(connection, csv_path, "official.zip")
        refresh_historical_flights(connection)
        derived = connection.execute(
            "SELECT year, month, day_of_week, departure_time_bucket, arrival_delay_minutes FROM historical_flights"
        ).fetchone()
        statuses = dict(connection.execute(
            "SELECT flight_status, count(*) FROM flight_records GROUP BY flight_status"
        ).fetchall())
    assert derived == (2024, 1, 1, "afternoon", -5.0)
    assert statuses == {"completed": 1, "cancelled": 1, "diverted": 1}
    assert summary["completed_flights"] == 1
    assert summary["cancelled_flights"] == 1
    assert summary["diverted_flights"] == 1


def test_invalid_fields_and_outliers_are_summarized_not_silently_trimmed(tmp_path: Path):
    csv_path = tmp_path / "month.csv"
    write_csv(csv_path, [row(Origin="A1X"), row(Flight_Number_Reporting_Airline="200", ArrDelay="1500")])
    database = tmp_path / "test.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(Path("backend/flight_connection/schema.sql").read_text())
        _create_macros(connection)
        summary = ingest_csv(connection, csv_path, "official.zip")
        outlier = connection.execute("SELECT arrival_delay_outlier FROM flight_records").fetchone()[0]
    assert summary["invalid_field_rows"] == 1
    assert summary["arrival_delay_outlier_rows"] == 1
    assert outlier is True


def test_development_sample_is_deterministic(tmp_path: Path):
    full = tmp_path / "full.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(full)) as connection:
        connection.execute(schema)
        for number in range(10):
            connection.execute("""
                INSERT INTO flight_records VALUES (
                    '2024-01-08', 2024, 1, 1, 'DL', ?, 'ATL', ?, 930, 1065, 'afternoon',
                    0, ?, false, false, 'completed', NULL, NULL, NULL, NULL, NULL, false, 'source.zip'
                )
            """, [str(number), "JFK" if number % 2 else "BOS", float(number)])
    first = tmp_path / "dev1.duckdb"
    second = tmp_path / "dev2.duckdb"
    assert create_development_database(full, first, per_stratum=3) == 3
    assert create_development_database(full, second, per_stratum=3) == 3
    with duckdb.connect(str(first), read_only=True) as a, duckdb.connect(str(second), read_only=True) as b:
        assert a.execute("SELECT flight_number FROM flight_records ORDER BY flight_number").fetchall() == \
               b.execute("SELECT flight_number FROM flight_records ORDER BY flight_number").fetchall()

