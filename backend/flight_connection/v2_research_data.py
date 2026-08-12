"""Build and query the separate research-only V2 BTS DuckDB artifact."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import date
from pathlib import Path

import duckdb

from .acquire import FILE_TEMPLATE
from .data_pipeline import _create_macros, _csv_scan, _extract_csv

DEFAULT_DATABASE = Path("data/processed/flights_v2_research.duckdb")
DEFAULT_SUMMARY = Path("data/processed/v2_research_build_summary.json")

CORE_FIELDS = {
    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline", "Tail_Number",
    "Origin", "Dest", "OriginAirportID", "DestAirportID", "CRSDepTime", "DepTime",
    "CRSArrTime", "ArrTime", "DepDelay", "ArrDelay", "Cancelled", "Diverted",
    "CancellationCode", "TaxiOut", "WheelsOff", "WheelsOn", "TaxiIn",
    "CRSElapsedTime", "ActualElapsedTime", "AirTime", "Distance", "CarrierDelay",
    "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay", "FirstDepTime",
    "TotalAddGTime", "LongestAddGTime", "DivAirportLandings", "DivReachedDest",
    "DivActualElapsedTime", "DivArrDelay", "DivDistance",
}
DIVERSION_FIELDS = tuple(
    f"Div{leg}{suffix}"
    for leg in range(1, 6)
    for suffix in (
        "Airport", "AirportID", "AirportSeqID", "WheelsOn", "TotalGTime",
        "LongestGTime", "WheelsOff", "TailNum",
    )
)
REQUIRED_FIELDS = CORE_FIELDS | set(DIVERSION_FIELDS)


def _nullable_hhmm(column: str) -> str:
    return f"CASE WHEN hhmm_valid({column}) THEN hhmm_minutes({column}) END"


def _text(column: str, *, upper: bool = False) -> str:
    expression = f"nullif(trim({column}), '')"
    return f"upper({expression})" if upper else expression


def _diversion_json() -> str:
    legs: list[str] = []
    for leg in range(1, 6):
        prefix = f"Div{leg}"
        legs.append(f"""CASE WHEN nullif(trim({prefix}Airport), '') IS NOT NULL THEN json_object(
            'sequence', {leg}, 'airport', upper(trim({prefix}Airport)),
            'airport_id', try_cast({prefix}AirportID AS INTEGER),
            'airport_sequence_id', try_cast({prefix}AirportSeqID AS BIGINT),
            'wheels_on_minutes', {_nullable_hhmm(prefix + 'WheelsOn')},
            'total_ground_minutes', try_cast({prefix}TotalGTime AS DOUBLE),
            'longest_ground_minutes', try_cast({prefix}LongestGTime AS DOUBLE),
            'wheels_off_minutes', {_nullable_hhmm(prefix + 'WheelsOff')},
            'tail_number', {_text(prefix + 'TailNum', upper=True)}
        ) END""")
    return "to_json(list_filter([" + ",".join(legs) + "], x -> x IS NOT NULL))"


def _validate_header(connection: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    columns = {row[0] for row in connection.execute(
        f"DESCRIBE SELECT * FROM {_csv_scan(csv_path)}"
    ).fetchall()}
    missing = REQUIRED_FIELDS - columns
    if missing:
        raise ValueError(f"missing V2 BTS columns: {sorted(missing)}")


def ingest_v2_csv(
    connection: duckdb.DuckDBPyConnection, csv_path: Path, source_file: str,
) -> dict[str, int | str]:
    """Normalize one existing BTS monthly CSV while retaining all valid statuses."""
    _validate_header(connection, csv_path)
    scan = _csv_scan(csv_path)
    valid = """
        try_cast(FlightDate AS DATE) IS NOT NULL
        AND regexp_full_match(upper(trim(Reporting_Airline)), '[A-Z0-9]{2,3}')
        AND regexp_full_match(upper(trim(Origin)), '[A-Z]{3}')
        AND regexp_full_match(upper(trim(Dest)), '[A-Z]{3}')
        AND try_cast(OriginAirportID AS INTEGER) IS NOT NULL
        AND try_cast(DestAirportID AS INTEGER) IS NOT NULL
        AND hhmm_valid(CRSDepTime) AND hhmm_valid(CRSArrTime)
        AND try_cast(Cancelled AS DOUBLE) IN (0, 1)
        AND try_cast(Diverted AS DOUBLE) IN (0, 1)
    """
    source = source_file.replace("'", "''")
    connection.execute(f"""
        INSERT INTO v2_flight_records
        SELECT
            try_cast(FlightDate AS DATE), upper(trim(Reporting_Airline)),
            {_text('Flight_Number_Reporting_Airline')}, {_text('Tail_Number', upper=True)},
            upper(trim(Origin)), upper(trim(Dest)), try_cast(OriginAirportID AS INTEGER),
            try_cast(DestAirportID AS INTEGER), hhmm_minutes(CRSDepTime),
            {_nullable_hhmm('DepTime')}, hhmm_minutes(CRSArrTime), {_nullable_hhmm('ArrTime')},
            try_cast(DepDelay AS DOUBLE), try_cast(ArrDelay AS DOUBLE),
            try_cast(Cancelled AS DOUBLE) = 1, try_cast(Diverted AS DOUBLE) = 1,
            {_text('CancellationCode', upper=True)}, try_cast(TaxiOut AS DOUBLE),
            {_nullable_hhmm('WheelsOff')}, {_nullable_hhmm('WheelsOn')},
            try_cast(TaxiIn AS DOUBLE), try_cast(CRSElapsedTime AS DOUBLE),
            try_cast(ActualElapsedTime AS DOUBLE), try_cast(AirTime AS DOUBLE),
            try_cast(Distance AS DOUBLE), try_cast(CarrierDelay AS DOUBLE),
            try_cast(WeatherDelay AS DOUBLE), try_cast(NASDelay AS DOUBLE),
            try_cast(SecurityDelay AS DOUBLE), try_cast(LateAircraftDelay AS DOUBLE),
            {_nullable_hhmm('FirstDepTime')}, try_cast(TotalAddGTime AS DOUBLE),
            try_cast(LongestAddGTime AS DOUBLE), try_cast(DivAirportLandings AS SMALLINT),
            CASE WHEN try_cast(DivReachedDest AS DOUBLE) IN (0, 1)
                 THEN try_cast(DivReachedDest AS DOUBLE) = 1 END,
            try_cast(DivActualElapsedTime AS DOUBLE), try_cast(DivArrDelay AS DOUBLE),
            try_cast(DivDistance AS DOUBLE), {_diversion_json()}, '{source}'
        FROM {scan} WHERE {valid}
    """)
    row = connection.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE {valid}), count(*) FILTER (WHERE NOT ({valid})),
               count(*) FILTER (WHERE {valid} AND nullif(trim(Flight_Number_Reporting_Airline), '') IS NULL),
               count(*) FILTER (WHERE {valid} AND try_cast(Cancelled AS DOUBLE) = 1),
               count(*) FILTER (WHERE {valid} AND try_cast(Diverted AS DOUBLE) = 1)
        FROM {scan}
    """).fetchone()
    names = ("source_rows", "retained_rows", "invalid_core_rows", "missing_flight_number_rows",
             "cancelled_rows", "diverted_rows")
    metrics = dict(zip(names, map(int, row)))
    connection.execute(
        "INSERT OR REPLACE INTO v2_data_quality_monthly VALUES (?, ?, ?, ?, ?, ?, ?)",
        [source_file, *(metrics[name] for name in names)],
    )
    return {"source_file": source_file, **metrics}


def lookup_flight(
    database: Path | str, *, carrier: str, flight_date: date | str, flight_number: str,
    origin: str | None = None, destination: str | None = None,
    scheduled_departure_minutes: int | None = None,
) -> dict:
    """Return and classify every matching segment; optional fields disambiguate the key."""
    clauses = ["reporting_carrier = ?", "flight_date = ?", "flight_number = ?"]
    params: list[object] = [carrier.strip().upper(), str(flight_date), flight_number.strip()]
    for column, value in (("origin", origin), ("destination", destination)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value.strip().upper())
    if scheduled_departure_minutes is not None:
        if not 0 <= scheduled_departure_minutes <= 1439:
            raise ValueError("scheduled_departure_minutes must be between 0 and 1439")
        clauses.append("crs_departure_minutes = ?")
        params.append(scheduled_departure_minutes)
    with duckdb.connect(str(database), read_only=True) as connection:
        cursor = connection.execute(f"""
            SELECT flight_date, reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes, cancelled, diverted,
                   departure_delay_minutes, arrival_delay_minutes, tail_number
            FROM v2_flight_records WHERE {' AND '.join(clauses)}
            ORDER BY crs_departure_minutes, origin, destination
        """, params)
        names = [item[0] for item in cursor.description]
        segments = [dict(zip(names, row)) for row in cursor.fetchall()]
    classification = "no_match" if not segments else "unique_match" if len(segments) == 1 else "multiple_segments"
    return {"classification": classification, "match_count": len(segments), "segments": segments}


def lookup_statistics(database: Path | str) -> dict[str, int | float]:
    with duckdb.connect(str(database), read_only=True) as connection:
        row = connection.execute("""
            WITH keys AS (
                SELECT flight_date, reporting_carrier, flight_number, count(*) AS n,
                       count(DISTINCT origin || '>' || destination) AS routes
                FROM v2_flight_records WHERE flight_number IS NOT NULL GROUP BY 1, 2, 3
            )
            SELECT count(*), count(*) FILTER (WHERE n = 1), count(*) FILTER (WHERE n > 1),
                   coalesce(sum(n), 0), coalesce(sum(n) FILTER (WHERE n = 1), 0),
                   coalesce(sum(n) FILTER (WHERE n > 1), 0),
                   max(n), count(*) FILTER (WHERE n > 1 AND routes = n),
                   count(*) FILTER (WHERE n > 1 AND routes < n)
            FROM keys
        """).fetchone()
    names = ("observed_keys", "unique_keys", "multiple_segment_keys", "records_with_flight_number",
             "records_in_unique_keys", "records_in_multiple_keys", "maximum_segments",
             "multiple_keys_all_distinct_routes", "multiple_keys_with_repeated_route")
    result = dict(zip(names, map(int, row)))
    result["unique_key_percent"] = round(100 * result["unique_keys"] / result["observed_keys"], 6)
    result["multiple_key_percent"] = round(100 * result["multiple_segment_keys"] / result["observed_keys"], 6)
    return result


def build_v2_research_database(
    *, raw_dir: Path, database: Path, years: tuple[int, ...], resume: bool = False,
) -> dict:
    started = time.perf_counter()
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists() and not resume:
        database.unlink()
    schema = Path(__file__).with_name("v2_research_schema.sql").read_text(encoding="utf-8")
    monthly: list[dict] = []
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        _create_macros(connection)
        processed = {row[0] for row in connection.execute(
            "SELECT source_file FROM v2_data_quality_monthly"
        ).fetchall()}
        with tempfile.TemporaryDirectory(prefix="bts-v2-research-") as temp:
            for year in years:
                for month in range(1, 13):
                    archive = raw_dir / FILE_TEMPLATE.format(year=year, month=month)
                    if not archive.exists():
                        raise FileNotFoundError(f"existing BTS archive is required: {archive}")
                    if archive.name in processed:
                        continue
                    csv_path = _extract_csv(archive, Path(temp))
                    monthly.append(ingest_v2_csv(connection, csv_path, archive.name))
                    csv_path.unlink()
                    connection.execute("CHECKPOINT")
                    print(f"Processed {archive.name}: {monthly[-1]['retained_rows']:,} rows")
        connection.execute("CREATE INDEX IF NOT EXISTS v2_lookup_idx ON v2_flight_records "
                           "(reporting_carrier, flight_date, flight_number)")
        row_count, start_date, end_date = connection.execute(
            "SELECT count(*), min(flight_date), max(flight_date) FROM v2_flight_records"
        ).fetchone()
    stats = lookup_statistics(database)
    return {
        "years": list(years), "row_count": int(row_count), "date_start": str(start_date),
        "date_end": str(end_date), "database_bytes": database.stat().st_size,
        "runtime_seconds": round(time.perf_counter() - started, 2), "lookup": stats,
        "monthly": monthly,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = build_v2_research_database(
        raw_dir=args.raw_dir, database=args.database, years=tuple(args.years), resume=args.resume,
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "monthly"}, indent=2))


if __name__ == "__main__":
    main()
