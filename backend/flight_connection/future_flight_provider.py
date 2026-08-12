"""Provider-neutral contracts for research-only future flight schedule lookup."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Mapping, Protocol

LookupOutcome = Literal["no_match", "unique_match", "multiple_candidates"]


@dataclass(frozen=True)
class FutureFlightCandidate:
    source: str
    retrieved_at_utc: datetime
    flight_date: date
    marketing_carrier: str
    marketing_flight_number: str
    operating_carrier: str | None
    operating_flight_number: str | None
    origin_iata: str
    destination_iata: str
    scheduled_departure_local: datetime
    scheduled_arrival_local: datetime
    departure_terminal: str | None = None
    arrival_terminal: str | None = None
    departure_gate: str | None = None
    arrival_gate: str | None = None
    status: str | None = None
    quality: tuple[str, ...] = ()
    codeshare_status: str | None = None
    aircraft_type: str | None = None
    raw_provider_id: str | None = None


@dataclass(frozen=True)
class FutureFlightLookupResult:
    outcome: LookupOutcome
    candidates: tuple[FutureFlightCandidate, ...]


class FutureFlightProvider(Protocol):
    def lookup_by_number(
        self, flight_number: str, date_local: date | str
    ) -> FutureFlightLookupResult: ...


class FutureFlightProviderError(RuntimeError):
    """Base error that deliberately contains no credentials or raw request headers."""

    def __init__(
        self, message: str, *, diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = dict(diagnostic) if diagnostic is not None else None


class MissingProviderCredential(FutureFlightProviderError):
    pass


class ProviderAuthenticationError(FutureFlightProviderError):
    pass


class ProviderQuotaError(FutureFlightProviderError):
    pass


class ProviderRateLimitError(FutureFlightProviderError):
    pass


class ProviderTimeoutError(FutureFlightProviderError):
    pass


class ProviderResponseError(FutureFlightProviderError):
    pass


class ProviderNormalizationError(ProviderResponseError):
    pass


class MissingRequiredScheduleFields(ProviderResponseError):
    pass
