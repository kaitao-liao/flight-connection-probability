"""Validate research-only historical flight-number lookup and print JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.flight_connection.v2_research_data import lookup_flight, lookup_statistics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_v2_research.duckdb"))
    parser.add_argument("--carrier", default="DL")
    parser.add_argument("--date", default="2025-06-15")
    parser.add_argument("--flight-number", default="1234")
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--scheduled-departure-minutes", type=int)
    args = parser.parse_args()
    output = {
        "query": lookup_flight(
            args.database, carrier=args.carrier, flight_date=args.date,
            flight_number=args.flight_number, origin=args.origin, destination=args.destination,
            scheduled_departure_minutes=args.scheduled_departure_minutes,
        ),
        "coverage": lookup_statistics(args.database),
    }
    print(json.dumps(output, default=str, indent=2))


if __name__ == "__main__":
    main()
