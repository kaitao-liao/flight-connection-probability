"""Reproducible multi-year BTS-to-DuckDB preprocessing pipeline."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import duckdb

from .acquire import FILE_TEMPLATE, download_month

DEFAULT_YEARS = (2023, 2024, 2025)
REQUIRED_FIELDS = {
    "FlightDate", "DayOfWeek", "Reporting_Airline", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "CRSArrTime", "DepDelay", "ArrDelay",
    "Cancelled", "Diverted", "CarrierDelay", "WeatherDelay", "NASDelay",
    "SecurityDelay", "LateAircraftDelay",
}


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''").replace("\\", "/")


def _csv_scan(path: Path) -> str:
    return f"read_csv('{_sql_path(path)}', header=true, all_varchar=true, auto_detect=true)"


def _create_macros(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("""
        CREATE OR REPLACE TEMP MACRO hhmm_valid(x) AS
            try_cast(x AS INTEGER) IS NOT NULL AND (
                try_cast(x AS INTEGER) = 2400 OR
                (try_cast(x AS INTEGER) BETWEEN 0 AND 2359 AND try_cast(x AS INTEGER) % 100 < 60)
            );
        CREATE OR REPLACE TEMP MACRO hhmm_minutes(x) AS
            CASE WHEN try_cast(x AS INTEGER) = 2400 THEN 0
                 ELSE (try_cast(x AS INTEGER) // 100) * 60 + try_cast(x AS INTEGER) % 100 END;
    """)


def _extract_csv(archive_path: Path, temp_dir: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected one CSV in {archive_path}, found {len(csv_names)}")
        output = temp_dir / f"{archive_path.stem}.csv"
        with archive.open(csv_names[0]) as source, output.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
    return output


def _validate_header(connection: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    columns = {row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {_csv_scan(csv_path)}").fetchall()}
    missing = REQUIRED_FIELDS - columns
    if missing:
        raise ValueError(f"missing BTS columns: {sorted(missing)}")


def ingest_csv(connection: duckdb.DuckDBPyConnection, csv_path: Path, source_file: str) -> dict[str, int | str]:
    """Clean one official monthly CSV with DuckDB-native scanning and append valid records."""
    _validate_header(connection, csv_path)
    scan = _csv_scan(csv_path)
    valid = """
        try_cast(FlightDate AS DATE) IS NOT NULL
        AND regexp_full_match(upper(trim(Reporting_Airline)), '[A-Z0-9]{2,3}')
        AND regexp_full_match(upper(trim(Origin)), '[A-Z]{3}')
        AND regexp_full_match(upper(trim(Dest)), '[A-Z]{3}')
        AND hhmm_valid(CRSDepTime) AND hhmm_valid(CRSArrTime)
        AND try_cast(DayOfWeek AS INTEGER) BETWEEN 1 AND 7
        AND try_cast(DayOfWeek AS INTEGER) = isodow(try_cast(FlightDate AS DATE))
        AND try_cast(Cancelled AS DOUBLE) IN (0, 1)
        AND try_cast(Diverted AS DOUBLE) IN (0, 1)
    """
    status = """CASE
        WHEN try_cast(Cancelled AS DOUBLE) = 1 THEN 'cancelled'
        WHEN try_cast(Diverted AS DOUBLE) = 1 THEN 'diverted'
        WHEN try_cast(ArrDelay AS DOUBLE) IS NULL THEN 'missing_arrival_delay'
        ELSE 'completed' END"""
    quoted_source = source_file.replace("'", "''")
    connection.execute(f"""
        INSERT INTO flight_records
        SELECT
            try_cast(FlightDate AS DATE), year(try_cast(FlightDate AS DATE)),
            month(try_cast(FlightDate AS DATE)), try_cast(isodow(try_cast(FlightDate AS DATE)) AS TINYINT),
            upper(trim(Reporting_Airline)), nullif(trim(Flight_Number_Reporting_Airline), ''),
            upper(trim(Origin)), upper(trim(Dest)),
            hhmm_minutes(CRSDepTime), hhmm_minutes(CRSArrTime),
            CASE WHEN hhmm_minutes(CRSDepTime) < 360 THEN 'overnight'
                 WHEN hhmm_minutes(CRSDepTime) < 720 THEN 'morning'
                 WHEN hhmm_minutes(CRSDepTime) < 1080 THEN 'afternoon' ELSE 'evening' END,
            try_cast(DepDelay AS DOUBLE), try_cast(ArrDelay AS DOUBLE),
            try_cast(Cancelled AS DOUBLE) = 1, try_cast(Diverted AS DOUBLE) = 1,
            {status},
            try_cast(CarrierDelay AS DOUBLE), try_cast(WeatherDelay AS DOUBLE),
            try_cast(NASDelay AS DOUBLE), try_cast(SecurityDelay AS DOUBLE),
            try_cast(LateAircraftDelay AS DOUBLE),
            coalesce(abs(try_cast(ArrDelay AS DOUBLE)) > 1440, false),
            '{quoted_source}'
        FROM {scan}
        WHERE {valid}
    """)
    metrics = connection.execute(f"""
        SELECT
            count(*) AS source_rows,
            count(*) FILTER (WHERE {valid}) AS cleaned_rows,
            count(*) FILTER (WHERE {valid} AND {status} = 'completed') AS completed_flights,
            count(*) FILTER (WHERE {valid} AND try_cast(Cancelled AS DOUBLE) = 1) AS cancelled_flights,
            count(*) FILTER (WHERE {valid} AND try_cast(Diverted AS DOUBLE) = 1) AS diverted_flights,
            count(*) FILTER (WHERE {valid} AND try_cast(ArrDelay AS DOUBLE) IS NULL) AS missing_arrival_delay_rows,
            count(*) FILTER (WHERE NOT ({valid})) AS invalid_field_rows,
            count(*) FILTER (WHERE {valid} AND try_cast(Cancelled AS DOUBLE) = 1 AND try_cast(Diverted AS DOUBLE) = 1) AS inconsistent_status_rows,
            count(*) FILTER (WHERE {valid} AND abs(try_cast(ArrDelay AS DOUBLE)) > 1440) AS arrival_delay_outlier_rows
        FROM {scan}
    """).fetchone()
    names = [column[0] for column in connection.description]
    summary = dict(zip(names, metrics))
    connection.execute(
        "INSERT OR REPLACE INTO data_quality_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [source_file, *(int(summary[name]) for name in names)],
    )
    return {"source_file": source_file, **{name: int(summary[name]) for name in names}}


def refresh_historical_flights(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("DELETE FROM historical_flights")
    connection.execute("""
        INSERT INTO historical_flights
        SELECT flight_date, year, month, day_of_week, reporting_carrier, origin, destination,
               crs_departure_minutes, crs_arrival_minutes, departure_time_bucket,
               arrival_delay_minutes, source_file
        FROM flight_records
        WHERE flight_status = 'completed' AND arrival_delay_minutes IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY flight_date, reporting_carrier, flight_number, origin, destination,
                         crs_departure_minutes
            ORDER BY source_file
        ) = 1
    """)


def create_development_database(
    full_database: Path, development_database: Path, *, per_stratum: int = 25,
) -> int:
    """Select deterministic carrier/month/time strata; hash ordering diversifies routes."""
    development_database.parent.mkdir(parents=True, exist_ok=True)
    if development_database.exists():
        development_database.unlink()
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with duckdb.connect(str(development_database)) as target:
        target.execute(schema)
        target.execute(f"ATTACH '{_sql_path(full_database)}' AS full_db (READ_ONLY)")
        target.execute("""
            INSERT INTO flight_records
            SELECT * EXCLUDE (sample_rank) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY year, month, reporting_carrier, departure_time_bucket
                    ORDER BY hash(flight_date, flight_number, origin, destination, crs_departure_minutes)
                ) AS sample_rank
                FROM full_db.flight_records
            ) WHERE sample_rank <= ?
        """, [per_stratum])
        target.execute("INSERT INTO data_quality_monthly SELECT * FROM full_db.data_quality_monthly")
        refresh_historical_flights(target)
        count = target.execute("SELECT count(*) FROM flight_records").fetchone()[0]
    return int(count)


def build_dataset(
    *, years: tuple[int, ...], raw_dir: Path, database: Path,
    development_database: Path | None = None, per_stratum: int = 25, resume: bool = False,
) -> dict:
    started = time.perf_counter()
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists() and not resume:
        database.unlink()
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    monthly: list[dict] = []
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        _create_macros(connection)
        processed = {
            row[0] for row in connection.execute("SELECT source_file FROM data_quality_monthly").fetchall()
        }
        with tempfile.TemporaryDirectory(prefix="bts-preprocess-") as temp:
            temp_dir = Path(temp)
            for year in years:
                for month in range(1, 13):
                    archive = download_month(year, month, raw_dir)
                    if archive.name in processed:
                        print(f"Skipped already processed {archive.name}")
                        continue
                    csv_path = _extract_csv(archive, temp_dir)
                    monthly.append(ingest_csv(connection, csv_path, archive.name))
                    csv_path.unlink()
                    print(f"Processed {archive.name}: {monthly[-1]['cleaned_rows']:,} cleaned rows")
        refresh_historical_flights(connection)
        duplicates = connection.execute("""
            SELECT coalesce(sum(count - 1), 0) FROM (
                SELECT count(*) AS count FROM flight_records
                GROUP BY flight_date, reporting_carrier, flight_number, origin, destination, crs_departure_minutes
                HAVING count(*) > 1
            )
        """).fetchone()[0]
        yearly = connection.execute("""
            SELECT year, count(*) source_cleaned, count(*) FILTER (WHERE flight_status='completed') completed,
                   count(*) FILTER (WHERE cancelled) cancelled, count(*) FILTER (WHERE diverted) diverted
            FROM flight_records GROUP BY year ORDER BY year
        """).fetchall()
    development_rows = None
    if development_database:
        development_rows = create_development_database(database, development_database, per_stratum=per_stratum)
    result = {
        "years": list(years), "runtime_seconds": round(time.perf_counter() - started, 2),
        "database_bytes": database.stat().st_size, "development_rows": development_rows,
        "duplicate_excess_rows": int(duplicates),
        "yearly": [dict(zip(("year", "cleaned_rows", "completed", "cancelled", "diverted"), map(int, row))) for row in yearly],
        "monthly": monthly,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("both", "full", "development"), default="both")
    parser.add_argument("--years", nargs="+", type=int, default=list(DEFAULT_YEARS))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--development-database", type=Path, default=Path("data/processed/flights_development.duckdb"))
    parser.add_argument("--development-per-stratum", type=int, default=25)
    parser.add_argument("--summary", type=Path, default=Path("data/processed/build_summary.json"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.mode == "development":
        if not args.database.exists():
            raise FileNotFoundError("full database is required before development sampling")
        started = time.perf_counter()
        development_rows = create_development_database(
            args.database, args.development_database, per_stratum=args.development_per_stratum
        )
        result = {
            "years": args.years, "runtime_seconds": round(time.perf_counter() - started, 2),
            "database_bytes": args.database.stat().st_size, "development_rows": development_rows,
            "development_database_bytes": args.development_database.stat().st_size,
        }
    else:
        result = build_dataset(
            years=tuple(args.years), raw_dir=args.raw_dir, database=args.database,
            development_database=args.development_database if args.mode == "both" else None,
            per_stratum=args.development_per_stratum, resume=args.resume,
        )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "monthly"}, indent=2))


if __name__ == "__main__":
    main()
