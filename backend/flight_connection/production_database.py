"""Build and verify the exact, serving-only DuckDB artifact for frozen V1."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

SERVING_COLUMNS = (
    "flight_date",
    "month",
    "day_of_week",
    "reporting_carrier",
    "origin",
    "destination",
    "departure_time_bucket",
    "arrival_delay_minutes",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_production_database(source: Path, destination: Path, *, overwrite: bool = False) -> dict:
    """Project the full database to columns read by V1 without filtering or aggregation."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")
    if source == destination:
        raise ValueError("source and destination database paths must differ")
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"destination already exists: {destination}")
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(destination))
    try:
        connection.execute("SET threads = 1")
        source_sql = str(source).replace("'", "''")
        connection.execute(f"ATTACH '{source_sql}' AS source_db (READ_ONLY)")
        source_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info('source_db.historical_flights')"
            ).fetchall()
        }
        missing = set(SERVING_COLUMNS) - source_columns
        if missing:
            raise ValueError(f"source database is missing serving columns: {sorted(missing)}")
        columns = ", ".join(SERVING_COLUMNS)
        connection.execute(
            f"CREATE TABLE historical_flights AS "
            f"SELECT {columns} FROM source_db.historical_flights"
        )
        source_rows = connection.execute(
            "SELECT count(*) FROM source_db.historical_flights"
        ).fetchone()[0]
        destination_rows = connection.execute(
            "SELECT count(*) FROM historical_flights"
        ).fetchone()[0]
        if source_rows != destination_rows:
            raise RuntimeError(
                f"row-count mismatch: source={source_rows}, destination={destination_rows}"
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()

    return {
        "source": str(source),
        "destination": str(destination),
        "rows": destination_rows,
        "columns": list(SERVING_COLUMNS),
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "representation": "exact_temporal_row_projection_no_filter_no_aggregation",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("data/production/flights_production.duckdb"),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()
    metadata = build_production_database(args.source, args.destination, overwrite=args.overwrite)
    rendered = json.dumps(metadata, indent=2)
    print(rendered)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
