"""Generate the frontend carrier list from production BTS reporting carriers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

# Human-readable names for the current codes in the official BTS Unique Carrier lookup.
# Source: https://www.transtats.bts.gov/Download_Lookup.asp?Lookup=L_UNIQUE_CARRIERS
CARRIER_NAMES = {
    "9E": "Endeavor Air",
    "AA": "American Airlines",
    "AS": "Alaska Airlines",
    "B6": "JetBlue Airways",
    "DL": "Delta Air Lines",
    "F9": "Frontier Airlines",
    "G4": "Allegiant Air",
    "HA": "Hawaiian Airlines",
    "MQ": "Envoy Air",
    "NK": "Spirit Airlines",
    "OH": "PSA Airlines",
    "OO": "SkyWest Airlines",
    "UA": "United Airlines",
    "WN": "Southwest Airlines",
    "YX": "Republic Airways",
}


def generate(database: Path, output: Path) -> int:
    with duckdb.connect(str(database), read_only=True) as connection:
        historical_codes = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT reporting_carrier FROM historical_flights"
            ).fetchall()
        }

    missing_names = historical_codes - CARRIER_NAMES.keys()
    if missing_names:
        raise ValueError(
            "BTS carrier-name mapping must be updated for: "
            + ", ".join(sorted(missing_names))
        )

    supported = [
        {"code": code, "name": CARRIER_NAMES[code]}
        for code in sorted(historical_codes)
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(supported, indent=2) + "\n", encoding="utf-8")
    return len(supported)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/production/flights_production.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/data/supported-carriers.json"),
    )
    args = parser.parse_args()
    count = generate(args.database, args.output)
    print(f"Wrote {count} supported carriers to {args.output}")


if __name__ == "__main__":
    main()
