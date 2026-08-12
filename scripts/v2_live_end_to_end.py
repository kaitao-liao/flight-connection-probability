"""Run the single approved Phase 5B V2 live validation through FastAPI.

This is intentionally not a generic runner. It requires explicit confirmation, uses
one fixed independently verified itinerary, and permits at most two provider calls.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.flight_connection.aerodatabox_provider import AeroDataBoxFutureFlightProvider
from backend.flight_connection.api import create_app
from backend.flight_connection.future_flight_provider import FutureFlightProviderError
from backend.flight_connection.service import ConnectionRiskService
from scripts.v2_aerodatabox_live_audit import load_local_env

ITINERARY = {
    "first_flight_number": "DL1575",
    "second_flight_number": "DL5798",
    "travel_date": "2026-08-20",
}
PRODUCTION_DATABASE = Path("data/production/flights_production.duckdb")


class ObservedProvider:
    """Record bounded normalized lookup metadata and enforce the two-call budget."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.observations: list[dict[str, Any]] = []

    def lookup_by_number(self, flight_number: str, date_local: date | str):
        if len(self.observations) >= 2:
            raise RuntimeError("Phase 5B provider request budget exceeded")
        record: dict[str, Any] = {
            "flight_number": flight_number.replace(" ", "").upper(),
            "flight_date": str(date_local),
        }
        self.observations.append(record)
        try:
            result = self.provider.lookup_by_number(flight_number, date_local)
        except FutureFlightProviderError as error:
            record.update(outcome="provider_error", error_type=type(error).__name__)
            if error.diagnostic is not None:
                # Provider diagnostics are already bounded and secret-redacted. Keeping
                # them here makes the opt-in harness as observable as the standalone
                # audit without exposing request headers or provider response bodies.
                record["diagnostic"] = error.diagnostic
            raise
        record.update(
            outcome=result.outcome,
            candidate_count=len(result.candidates),
            required_complete=sum(
                all((
                    candidate.marketing_carrier,
                    candidate.marketing_flight_number,
                    candidate.origin_iata,
                    candidate.destination_iata,
                    candidate.scheduled_departure_local,
                    candidate.scheduled_arrival_local,
                ))
                for candidate in result.candidates
            ),
        )
        return result


def execute(provider: Any, database: str | Path = PRODUCTION_DATABASE) -> dict[str, Any]:
    """Exercise the real V2 FastAPI route and service stack with an injected provider."""
    observed = ObservedProvider(provider)
    estimator = ConnectionRiskService(database)
    app = create_app(
        service=estimator,
        v2_provider_factory=lambda: observed,
        allowed_origins=["http://localhost:3000"],
    )
    with TestClient(app) as client:
        response = client.post("/api/v2/connection-risk", json=ITINERARY)
    return {
        "itinerary_request": ITINERARY,
        "provider_request_count": len(observed.observations),
        "estimated_api_units": 2 * len(observed.observations),
        "provider_observations": observed.observations,
        "http_status": response.status_code,
        "v2_response": response.json(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required because this makes up to two paid requests")
    load_local_env()
    output = execute(AeroDataBoxFutureFlightProvider())
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
