"""Offline-testable, quota-bounded V2 future-schedule provider acceptance study."""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

from .future_flight_provider import (
    FutureFlightLookupResult,
    FutureFlightProvider,
    MissingRequiredScheduleFields,
    ProviderAuthenticationError,
    ProviderNormalizationError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)

MAX_LIVE_LOOKUPS = 30
UNITS_PER_SUCCESSFUL_LOOKUP = 2
MINIMUM_REQUEST_INTERVAL_SECONDS = 1.0
_FLIGHT_NUMBER = re.compile(r"^([A-Z0-9]{2})\d{1,4}[A-Z]?$", re.ASCII)
OPTIONAL_FIELDS = (
    "departure_terminal", "arrival_terminal", "departure_gate", "arrival_gate",
    "aircraft_type", "quality", "status", "codeshare_status", "operating_carrier",
    "operating_flight_number",
)


@dataclass(frozen=True)
class AcceptanceCandidate:
    flight_number: str
    flight_date: date
    airline: str
    manually_confirmed: bool
    confirmation_source: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AcceptanceCandidate":
        flight_number = str(value.get("flight_number", "")).replace(" ", "").upper()
        match = _FLIGHT_NUMBER.fullmatch(flight_number)
        if not match:
            raise ValueError("candidate flight_number must contain carrier code and number")
        airline = str(value.get("airline", match.group(1))).strip().upper()
        if airline != match.group(1):
            raise ValueError("candidate airline must match the flight-number prefix")
        confirmed = value.get("manually_confirmed") is True
        source = str(value.get("confirmation_source", "")).strip()
        if not confirmed or not source:
            raise ValueError("every live candidate must be manually confirmed with a source note")
        return cls(
            flight_number=flight_number, flight_date=date.fromisoformat(str(value["flight_date"])),
            airline=airline, manually_confirmed=True, confirmation_source=source,
        )


def horizon_bucket(flight_date: date, reference_date: date) -> str:
    days = (flight_date - reference_date).days
    if days < 0:
        raise ValueError("acceptance-study candidate dates cannot be historical")
    if days <= 14:
        return "near_term"
    if days <= 60:
        return "approximately_30_days"
    if days <= 135:
        return "approximately_90_days"
    return "approximately_180_days"


def validate_candidates(
    candidates: Iterable[AcceptanceCandidate], *, reference_date: date,
) -> tuple[AcceptanceCandidate, ...]:
    values = tuple(candidates)
    if not values:
        raise ValueError("at least one manually confirmed candidate is required")
    if len(values) > MAX_LIVE_LOOKUPS:
        raise ValueError(f"acceptance study cannot exceed {MAX_LIVE_LOOKUPS} live lookups")
    keys = [(item.flight_number, item.flight_date) for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate flight-number/date candidates are not allowed")
    for item in values:
        match = _FLIGHT_NUMBER.fullmatch(item.flight_number)
        if not match or match.group(1) != item.airline:
            raise ValueError("candidate airline must match the flight-number prefix")
        if not item.manually_confirmed or not item.confirmation_source.strip():
            raise ValueError(
                "every live candidate must be manually confirmed with a source note"
            )
        horizon_bucket(item.flight_date, reference_date)
    return values


def study_plan(candidates: Iterable[AcceptanceCandidate], *, reference_date: date) -> dict[str, Any]:
    values = validate_candidates(candidates, reference_date=reference_date)
    return {
        "live_requests_planned": len(values),
        "maximum_estimated_units": len(values) * UNITS_PER_SUCCESSFUL_LOOKUP,
        "hard_request_cap": MAX_LIVE_LOOKUPS,
        "units_per_successful_request": UNITS_PER_SUCCESSFUL_LOOKUP,
        "airlines": dict(sorted(Counter(item.airline for item in values).items())),
        "horizons": dict(sorted(Counter(
            horizon_bucket(item.flight_date, reference_date) for item in values
        ).items())),
    }


def run_acceptance_study(
    provider: FutureFlightProvider, candidates: Iterable[AcceptanceCandidate], *,
    reference_date: date, confirmed_live: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    values = validate_candidates(candidates, reference_date=reference_date)
    plan = study_plan(values, reference_date=reference_date)
    if not confirmed_live:
        raise PermissionError("explicit live confirmation is required")
    totals: Counter[str] = Counter()
    groups: dict[str, dict[str, Counter[str]]] = {
        "airline": defaultdict(Counter), "horizon": defaultdict(Counter),
    }
    lookups: list[dict[str, Any]] = []
    stopped = False
    stop_reason: str | None = None
    for index, candidate in enumerate(values):
        if index:
            sleep(MINIMUM_REQUEST_INTERVAL_SECONDS)
        horizon = horizon_bucket(candidate.flight_date, reference_date)
        try:
            result = provider.lookup_by_number(candidate.flight_number, candidate.flight_date)
        except MissingRequiredScheduleFields:
            _count_lookup(totals, groups, candidate.airline, horizon, "missing_required_fields")
            lookups.append(_lookup_row(candidate, horizon, "missing_required_fields"))
            continue
        except ProviderNormalizationError:
            _count_lookup(totals, groups, candidate.airline, horizon, "malformed_response")
            lookups.append(_lookup_row(candidate, horizon, "malformed_response"))
            continue
        except (
            ProviderAuthenticationError, ProviderQuotaError, ProviderRateLimitError,
            ProviderTimeoutError, ProviderResponseError,
        ) as error:
            _count_lookup(totals, groups, candidate.airline, horizon, "provider_errors")
            row = _lookup_row(candidate, horizon, "provider_error", type(error).__name__)
            if error.diagnostic is not None:
                row["diagnostic"] = error.diagnostic
            lookups.append(row)
            stopped = True
            stop_reason = type(error).__name__
            break
        _record_result(totals, groups, candidate, horizon, result)
        lookups.append(_lookup_row(candidate, horizon, result.outcome, candidate_count=len(result.candidates)))
    return {
        "plan": plan, "attempted": totals["attempted"], "stopped_early": stopped,
        "stop_reason": stop_reason, "totals": dict(totals),
        "coverage": _coverage(totals),
        "by_airline": _group_reports(groups["airline"]),
        "by_horizon": _group_reports(groups["horizon"]),
        "lookups": lookups,
    }


def _record_result(totals, groups, candidate, horizon, result: FutureFlightLookupResult) -> None:
    _count_lookup(totals, groups, candidate.airline, horizon, result.outcome)
    for normalized in result.candidates:
        for counter in (totals, groups["airline"][candidate.airline], groups["horizon"][horizon]):
            counter["returned_candidates"] += 1
            counter["required_complete"] += int(all((
                normalized.marketing_flight_number, normalized.origin_iata,
                normalized.destination_iata, normalized.scheduled_departure_local,
                normalized.scheduled_arrival_local,
            )))
            for field in OPTIONAL_FIELDS:
                value = getattr(normalized, field)
                counter[f"has_{field}"] += int(bool(value))


def _count_lookup(totals, groups, airline, horizon, outcome) -> None:
    for counter in (totals, groups["airline"][airline], groups["horizon"][horizon]):
        counter["attempted"] += 1
        counter[outcome] += 1


def _coverage(counter: Counter[str]) -> dict[str, float | int | None]:
    denominator = counter["returned_candidates"]
    output: dict[str, float | int | None] = {"candidate_denominator": denominator}
    for field in ("required_complete", *(f"has_{name}" for name in OPTIONAL_FIELDS)):
        output[f"{field}_percent"] = (
            round(100 * counter[field] / denominator, 3) if denominator else None
        )
    return output


def _group_reports(groups: dict[str, Counter[str]]) -> dict[str, Any]:
    return {
        key: {"totals": dict(value), "coverage": _coverage(value)}
        for key, value in sorted(groups.items())
    }


def _lookup_row(candidate, horizon, outcome, error_type=None, candidate_count=None):
    row = {
        "flight_number": candidate.flight_number, "flight_date": candidate.flight_date.isoformat(),
        "airline": candidate.airline, "horizon": horizon, "outcome": outcome,
    }
    if error_type:
        row["error_type"] = error_type
    if candidate_count is not None:
        row["candidate_count"] = candidate_count
    return row
