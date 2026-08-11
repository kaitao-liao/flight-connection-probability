"""Official BTS monthly download and legacy single-file smoke-test utilities."""
from __future__ import annotations

import argparse
import csv
import io
import urllib.request
import zipfile
from pathlib import Path

import duckdb

BASE_URL = "https://transtats.bts.gov/PREZIP"
FILE_TEMPLATE = "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
REQUIRED_FIELDS = {
    "FlightDate", "Year", "Month", "DayOfWeek", "Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "CRSArrTime", "ArrDelay", "Cancelled", "Diverted",
}


def hhmm_to_minutes(value: str) -> int:
    """Convert BTS local HHMM to minutes after midnight."""
    number = int(float(value))
    hours, minutes = divmod(number, 100)
    if hours == 24 and minutes == 0:
        return 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"invalid HHMM value: {value}")
    return hours * 60 + minutes


def time_bucket(minutes: int) -> str:
    hour = minutes // 60
    if hour < 6:
        return "overnight"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def download_month(year: int, month: int, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = FILE_TEMPLATE.format(year=year, month=month)
    destination = raw_dir / filename
    if destination.exists():
        try:
            if zipfile.is_zipfile(destination):
                return destination
        except OSError:
            pass
        destination.unlink()
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    urllib.request.urlretrieve(f"{BASE_URL}/{filename}", partial)
    if not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        raise ValueError(f"downloaded file is not a valid ZIP: {filename}")
    partial.replace(destination)
    return destination


def clean_rows(zip_path: Path, limit: int | None = None) -> list[tuple]:
    rows: list[tuple] = []
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected one CSV in {zip_path}, found {len(csv_names)}")
        with archive.open(csv_names[0]) as binary:
            reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
            missing = REQUIRED_FIELDS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"missing BTS columns: {sorted(missing)}")
            for raw in reader:
                if raw["Cancelled"] == "1.00" or raw["Diverted"] == "1.00" or not raw["ArrDelay"]:
                    continue
                try:
                    dep_minutes = hhmm_to_minutes(raw["CRSDepTime"])
                    arr_minutes = hhmm_to_minutes(raw["CRSArrTime"])
                    row = (
                        raw["FlightDate"], int(raw["Year"]), int(raw["Month"]), int(raw["DayOfWeek"]),
                        raw["Reporting_Airline"].strip().upper(), raw["Origin"].strip().upper(),
                        raw["Dest"].strip().upper(), dep_minutes, arr_minutes, time_bucket(dep_minutes),
                        float(raw["ArrDelay"]), zip_path.name,
                    )
                except (TypeError, ValueError):
                    continue
                rows.append(row)
                if limit is not None and len(rows) >= limit:
                    break
    return rows


def write_duckdb(rows: list[tuple], database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        connection.execute("DELETE FROM historical_flights")
        connection.executemany("INSERT INTO historical_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a bounded single-month smoke database; use data_pipeline for development/full datasets."
    )
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--month", type=int, default=1)
    parser.add_argument("--limit", type=int, default=50_000)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--database", type=Path, default=Path("data/processed/monthly_smoke.duckdb"))
    args = parser.parse_args()
    archive = download_month(args.year, args.month, args.raw_dir)
    rows = clean_rows(archive, args.limit)
    if not rows:
        raise RuntimeError("no usable completed, non-diverted flights found")
    write_duckdb(rows, args.database)
    print(f"Loaded {len(rows):,} rows from {archive} into {args.database}")


if __name__ == "__main__":
    main()
