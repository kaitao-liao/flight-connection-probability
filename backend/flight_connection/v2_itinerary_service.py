"""Resolve future schedules and adapt them to the frozen BTS-backed V1 estimator."""
from __future__ import annotations

from datetime import datetime, timezone

from .future_flight_provider import (
    FutureFlightCandidate, FutureFlightLookupResult, FutureFlightProvider,
    MissingProviderCredential, MissingRequiredScheduleFields, ProviderAuthenticationError,
    ProviderNormalizationError, ProviderQuotaError, ProviderRateLimitError,
    ProviderResponseError, ProviderTimeoutError,
)
from .schemas import ConnectionRiskRequest
from .service import ConnectionRiskService
from .timezone_validation import airport_timezone
from .v2_schemas import (
    AmbiguousLeg, ResolvedFlight, ResolvedItinerary, V2ConnectionRequest,
    V2ConnectionResponse,
)


class V2ItineraryService:
    def __init__(
        self, provider: FutureFlightProvider, estimator: ConnectionRiskService,
    ) -> None:
        self.provider = provider
        self.estimator = estimator

    def estimate(self, request: V2ConnectionRequest) -> V2ConnectionResponse:
        first = self._lookup(request.first_flight_number, request.travel_date, "first")
        if isinstance(first, V2ConnectionResponse):
            return first

        # A first-leg ambiguity is not terminal: resolving the second lookup allows the
        # response to preserve the "both legs ambiguous" case. A no-match/error above is
        # terminal and avoids an unnecessary second provider call.
        second = self._lookup(request.second_flight_number, request.travel_date, "second")
        if isinstance(second, V2ConnectionResponse):
            return second

        ambiguous = []
        if len(first.candidates) > 1:
            ambiguous.append(AmbiguousLeg(
                leg="first", candidates=[_resolved(candidate) for candidate in first.candidates]
            ))
        if len(second.candidates) > 1:
            ambiguous.append(AmbiguousLeg(
                leg="second", candidates=[_resolved(candidate) for candidate in second.candidates]
            ))
        if ambiguous:
            return V2ConnectionResponse(
                status="ambiguous", ambiguous_legs=ambiguous,
                message="Select one schedule candidate for each ambiguous leg.",
            )

        first_candidate = first.candidates[0]
        second_candidate = second.candidates[0]
        if first_candidate.destination_iata != second_candidate.origin_iata:
            return V2ConnectionResponse(
                status="invalid_connection_airport",
                message="First-flight destination must match second-flight origin.",
            )

        try:
            first_departure = _airport_instant(
                first_candidate.scheduled_departure_local, first_candidate.origin_iata
            )
            first_arrival = _airport_instant(
                first_candidate.scheduled_arrival_local, first_candidate.destination_iata
            )
            second_departure = _airport_instant(
                second_candidate.scheduled_departure_local, second_candidate.origin_iata
            )
            second_arrival = _airport_instant(
                second_candidate.scheduled_arrival_local, second_candidate.destination_iata
            )
        except (ValueError, RuntimeError):
            return V2ConnectionResponse(
                status="invalid_chronology",
                message="Scheduled times could not be normalized with airport time zones.",
            )

        if (
            first_arrival <= first_departure
            or second_departure <= first_arrival
            or second_arrival <= second_departure
        ):
            return V2ConnectionResponse(
                status="invalid_chronology",
                message="Second-flight departure must be after first-flight arrival.",
            )
        layover = int(
            (second_departure.astimezone(timezone.utc) - first_arrival.astimezone(timezone.utc))
            .total_seconds() // 60
        )
        if layover <= 0:
            return V2ConnectionResponse(
                status="invalid_chronology",
                message="Scheduled connection chronology is reversed or impossible.",
            )

        estimator_request = ConnectionRiskRequest(
            carrier=first_candidate.marketing_carrier,
            origin=first_candidate.origin_iata,
            connection=first_candidate.destination_iata,
            destination=second_candidate.destination_iata,
            travel_date=first_departure.date(),
            first_departure_time=first_departure.timetz().replace(tzinfo=None),
            first_arrival_time=first_arrival.timetz().replace(tzinfo=None),
            connecting_departure_time=second_departure.timetz().replace(tzinfo=None),
        )
        try:
            probability = self.estimator.estimate(estimator_request)
        except ValueError as error:
            return V2ConnectionResponse(status="invalid_chronology", message=str(error))
        except (OSError, RuntimeError):
            return V2ConnectionResponse(
                status="provider_temporarily_unavailable",
                message="Historical probability data is temporarily unavailable.",
            )

        warnings = [
            "Terminal and gate data are optional provider metadata."
        ] if any(value is None for value in (
            first_candidate.departure_terminal, first_candidate.arrival_terminal,
            second_candidate.departure_terminal, second_candidate.arrival_terminal,
        )) else []
        return V2ConnectionResponse(
            status="success",
            itinerary=ResolvedItinerary(
                first_flight=_resolved(first_candidate),
                second_flight=_resolved(second_candidate),
                connection_airport=first_candidate.destination_iata,
                scheduled_layover_minutes=layover,
            ),
            probability_result=probability,
            warnings=warnings,
        )

    def _lookup(self, number, travel_date, leg):
        try:
            result = self.provider.lookup_by_number(number, travel_date)
        except (MissingProviderCredential, ProviderAuthenticationError):
            return V2ConnectionResponse(
                status="provider_configuration_error", leg=leg,
                message="Future schedule provider is not configured correctly.",
            )
        except (MissingRequiredScheduleFields, ProviderNormalizationError):
            return V2ConnectionResponse(
                status="provider_data_quality_error", leg=leg,
                message="Future schedule provider returned incomplete or malformed schedule data.",
            )
        except (ProviderQuotaError, ProviderRateLimitError, ProviderTimeoutError,
                ProviderResponseError):
            return V2ConnectionResponse(
                status="provider_temporarily_unavailable", leg=leg,
                message="Future schedule provider is temporarily unavailable.",
            )
        if result.outcome == "no_match" or not result.candidates:
            return V2ConnectionResponse(
                status="schedule_not_found", leg=leg,
                message=f"No schedule was found for the {leg} flight.",
            )
        return result


def _airport_instant(value: datetime, airport: str) -> datetime:
    zone = airport_timezone(airport)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _resolved(candidate: FutureFlightCandidate) -> ResolvedFlight:
    return ResolvedFlight(
        marketing_carrier=candidate.marketing_carrier,
        marketing_flight_number=candidate.marketing_flight_number,
        origin=candidate.origin_iata,
        destination=candidate.destination_iata,
        scheduled_departure=candidate.scheduled_departure_local,
        scheduled_arrival=candidate.scheduled_arrival_local,
        departure_terminal=candidate.departure_terminal,
        arrival_terminal=candidate.arrival_terminal,
        departure_gate=candidate.departure_gate,
        arrival_gate=candidate.arrival_gate,
        aircraft_type=candidate.aircraft_type,
        provider_quality=list(candidate.quality),
        codeshare_status=candidate.codeshare_status,
        operating_carrier=candidate.operating_carrier,
        operating_flight_number=candidate.operating_flight_number,
    )
