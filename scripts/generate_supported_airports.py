"""Generate the frontend airport list from BTS history and offline airport metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import airportsdata
import duckdb

US_DOMESTIC_COUNTRY_CODES = frozenset({"AS", "GU", "MP", "PR", "US", "VI"})


def generate(database: Path, output: Path) -> int:
    with duckdb.connect(str(database), read_only=True) as connection:
        historical_codes = {
            row[0]
            for row in connection.execute(
                """
                SELECT origin AS code FROM historical_flights
                UNION
                SELECT destination AS code FROM historical_flights
                """
            ).fetchall()
        }

    metadata = airportsdata.load("IATA")
    supported = []
    for code in sorted(historical_codes):
        airport = metadata.get(code)
        if not airport or airport["country"] not in US_DOMESTIC_COUNTRY_CODES or not airport["tz"]:
            continue
        try:
            ZoneInfo(str(airport["tz"]))
        except ZoneInfoNotFoundError:
            continue
        supported.append({"code": code, "city": airport["city"], "name": airport["name"]})

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(supported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(supported)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/production/flights_production.duckdb"))
    parser.add_argument("--output", type=Path, default=Path("frontend/data/supported-airports.json"))
    args = parser.parse_args()
    count = generate(args.database, args.output)
    print(f"Wrote {count} supported airports to {args.output}")


if __name__ == "__main__":
    main()
