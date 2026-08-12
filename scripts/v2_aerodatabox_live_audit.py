"""Explicitly opt-in, quota-consuming AeroDataBox feasibility audit.

This script prints normalized coverage only. It never persists raw provider responses or
credentials. Candidate flight numbers must be independently verified and supplied by the
researcher; the repository intentionally contains no fabricated future schedule list.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path

from backend.flight_connection.aerodatabox_provider import AeroDataBoxFutureFlightProvider
from backend.flight_connection.future_flight_provider import FutureFlightProviderError


def load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "AERODATABOX_API_KEY":
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def parse_lookup(value: str) -> tuple[str, date]:
    try:
        flight_number, date_text = value.rsplit(":", 1)
        return flight_number, date.fromisoformat(date_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("lookup must be FLIGHT_NUMBER:YYYY-MM-DD") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--lookup", action="append", type=parse_lookup, default=[])
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required because requests consume API units")
    if not 1 <= len(args.lookup) <= 30:
        parser.error("provide between 1 and 30 independently verified --lookup values")
    load_local_env()
    provider = AeroDataBoxFutureFlightProvider()
    totals: Counter[str] = Counter()
    records = []
    for index, (flight_number, flight_date) in enumerate(args.lookup):
        if index:
            time.sleep(1.05)  # Trial/Pro API.Market plans are documented at 1 request/second.
        try:
            result = provider.lookup_by_number(flight_number, flight_date)
            totals["attempted"] += 1
            totals[result.outcome] += 1
            candidates = result.candidates
            totals["returned_candidates"] += len(candidates)
            for candidate in candidates:
                totals["required_complete"] += int(all((
                    candidate.marketing_carrier, candidate.marketing_flight_number,
                    candidate.origin_iata, candidate.destination_iata,
                    candidate.scheduled_departure_local, candidate.scheduled_arrival_local,
                )))
                for field in (
                    "departure_terminal", "arrival_terminal", "departure_gate", "arrival_gate",
                    "operating_carrier", "aircraft_type",
                ):
                    totals[f"has_{field}"] += int(getattr(candidate, field) is not None)
                totals["has_quality"] += int(bool(candidate.quality))
            records.append({
                "flight_number": flight_number.replace(" ", "").upper(),
                "flight_date": flight_date.isoformat(), "outcome": result.outcome,
                "candidate_count": len(candidates),
            })
        except FutureFlightProviderError as error:
            totals["attempted"] += 1
            totals["provider_errors"] += 1
            record = {
                "flight_number": flight_number.replace(" ", "").upper(),
                "flight_date": flight_date.isoformat(), "outcome": "provider_error",
                "error_type": type(error).__name__,
                "error_message": str(error),
            }
            if error.diagnostic is not None:
                record["diagnostic"] = error.diagnostic
            records.append(record)
    # Flight Status is Tier 2: two API units for a successful request under current pricing.
    output = {
        "summary": dict(totals), "estimated_units_if_all_2xx": 2 * totals["attempted"],
        "lookups": records,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
