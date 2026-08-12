"""Quota-bounded AeroDataBox Phase 4 acceptance study using confirmed candidates."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from backend.flight_connection.aerodatabox_provider import AeroDataBoxFutureFlightProvider
from backend.flight_connection.v2_provider_acceptance import (
    AcceptanceCandidate, run_acceptance_study, study_plan, validate_candidates,
)
from scripts.v2_aerodatabox_live_audit import load_local_env


def load_candidates(path: Path) -> tuple[AcceptanceCandidate, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("candidate file must contain a JSON array")
    return tuple(AcceptanceCandidate.from_mapping(item) for item in payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    candidates = validate_candidates(
        load_candidates(args.candidates), reference_date=args.reference_date
    )
    plan = study_plan(candidates, reference_date=args.reference_date)
    print(json.dumps({"study_plan": plan}, indent=2), flush=True)
    if not args.confirm_live:
        print("Dry run only. Add --confirm-live to spend API units.")
        return
    load_local_env()
    report = run_acceptance_study(
        AeroDataBoxFutureFlightProvider(), candidates, reference_date=args.reference_date,
        confirmed_live=True,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
