import csv
from pathlib import Path

import duckdb
import pytest

from backend.flight_connection.data_pipeline import _create_macros
from backend.flight_connection.v2_research_data import (
    REQUIRED_FIELDS, ingest_v2_csv, lookup_flight, lookup_statistics,
)


def record(**overrides) -> dict[str, str]:
    row = {field: "" for field in REQUIRED_FIELDS}
    row.update({
        "FlightDate": "2025-06-15", "Reporting_Airline": "DL",
        "Flight_Number_Reporting_Airline": "1234", "Tail_Number": "N123DL",
        "Origin": "ATL", "Dest": "MIA", "OriginAirportID": "10397",
        "DestAirportID": "13303", "CRSDepTime": "2052", "DepTime": "2100",
        "CRSArrTime": "2245", "ArrTime": "2238", "DepDelay": "8", "ArrDelay": "-7",
        "Cancelled": "0", "Diverted": "0", "TaxiOut": "15", "WheelsOff": "2115",
        "WheelsOn": "2228", "TaxiIn": "10", "CRSElapsedTime": "113",
        "ActualElapsedTime": "98", "AirTime": "73", "Distance": "594",
    })
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted(REQUIRED_FIELDS)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "v2.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(Path("backend/flight_connection/v2_research_schema.sql").read_text())
    return path


def ingest(database: Path, tmp_path: Path, rows: list[dict[str, str]]) -> dict:
    csv_path = tmp_path / "month.csv"
    write_csv(csv_path, rows)
    with duckdb.connect(str(database)) as connection:
        _create_macros(connection)
        return ingest_v2_csv(connection, csv_path, "official.zip")


def test_time_parsing_normalizes_2400_and_nullable_actual_times(database: Path, tmp_path: Path):
    ingest(database, tmp_path, [record(CRSDepTime="2400", DepTime="2400", ArrTime="bad")])
    with duckdb.connect(str(database), read_only=True) as connection:
        times = connection.execute("""
            SELECT crs_departure_minutes, departure_minutes, arrival_minutes,
                   wheels_off_minutes, wheels_on_minutes
            FROM v2_flight_records
        """).fetchone()
    assert times == (0, 0, None, 1275, 1348)


def test_cancelled_and_diverted_records_are_preserved_with_details(database: Path, tmp_path: Path):
    summary = ingest(database, tmp_path, [
        record(Flight_Number_Reporting_Airline="10", Cancelled="1", CancellationCode="B",
               DepTime="", ArrTime="", DepDelay="", ArrDelay=""),
        record(Flight_Number_Reporting_Airline="11", Diverted="1", DivAirportLandings="1",
               DivReachedDest="1", Div1Airport="FLL", Div1AirportID="11697",
               Div1AirportSeqID="1169706", Div1WheelsOn="2310", Div1WheelsOff="2355",
               Div1TailNum="N456DL", ArrDelay=""),
    ])
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute("""
            SELECT flight_number, cancelled, diverted, cancellation_code,
                   diversion_airport_landings, json_extract_string(diversion_details, '$[0].airport')
            FROM v2_flight_records ORDER BY flight_number
        """).fetchall()
    assert summary["retained_rows"] == 2
    assert summary["cancelled_rows"] == 1
    assert summary["diverted_rows"] == 1
    assert rows == [("10", True, False, "B", None, None), ("11", False, True, None, 1, "FLL")]


def test_lookup_classifies_and_disambiguates_multiple_segments(database: Path, tmp_path: Path):
    ingest(database, tmp_path, [
        record(Reporting_Airline="WN", Flight_Number_Reporting_Airline="39", Origin="GSP",
               Dest="BWI", CRSDepTime="600", CRSArrTime="730"),
        record(Reporting_Airline="WN", Flight_Number_Reporting_Airline="39", Origin="BWI",
               Dest="ABQ", CRSDepTime="815", CRSArrTime="1030"),
    ])
    multiple = lookup_flight(database, carrier="wn", flight_date="2025-06-15", flight_number="39")
    route = lookup_flight(database, carrier="WN", flight_date="2025-06-15", flight_number="39",
                          origin="BWI", destination="ABQ")
    time = lookup_flight(database, carrier="WN", flight_date="2025-06-15", flight_number="39",
                         scheduled_departure_minutes=360)
    missing = lookup_flight(database, carrier="WN", flight_date="2025-06-15", flight_number="999")
    assert multiple["classification"] == "multiple_segments"
    assert multiple["match_count"] == 2
    assert route["classification"] == "unique_match"
    assert route["segments"][0]["origin"] == "BWI"
    assert time["classification"] == "unique_match"
    assert time["segments"][0]["origin"] == "GSP"
    assert missing["classification"] == "no_match"
    stats = lookup_statistics(database)
    assert stats["observed_keys"] == 1
    assert stats["multiple_segment_keys"] == 1
    assert stats["multiple_keys_all_distinct_routes"] == 1


def test_lookup_rejects_invalid_departure_minute(database: Path):
    with pytest.raises(ValueError, match="between 0 and 1439"):
        lookup_flight(database, carrier="DL", flight_date="2025-06-15", flight_number="1234",
                      scheduled_departure_minutes=1440)
