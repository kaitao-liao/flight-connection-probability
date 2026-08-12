from datetime import date, datetime, timezone

import pytest

from backend.flight_connection.future_flight_provider import (
    FutureFlightCandidate, FutureFlightLookupResult, MissingRequiredScheduleFields,
    MissingProviderCredential, ProviderAuthenticationError, ProviderNormalizationError, ProviderQuotaError,
    ProviderRateLimitError, ProviderResponseError, ProviderTimeoutError,
)
from backend.flight_connection.service import ConnectionRiskService
from backend.flight_connection.v2_itinerary_service import V2ItineraryService
from backend.flight_connection.v2_schemas import V2ConnectionRequest

DAY = date(2026, 8, 20)


def flight(number, origin, destination, departure, arrival, **overrides):
    carrier = "".join(character for character in number if not character.isdigit())
    values = dict(
        source="test", retrieved_at_utc=datetime(2026, 8, 12, tzinfo=timezone.utc),
        flight_date=departure.date(), marketing_carrier=carrier,
        marketing_flight_number=number, operating_carrier=carrier,
        operating_flight_number=number, origin_iata=origin, destination_iata=destination,
        scheduled_departure_local=departure, scheduled_arrival_local=arrival,
        departure_terminal="A", arrival_terminal="B", departure_gate="1",
        arrival_gate="2", status="Expected", quality=("departure:Basic",),
        codeshare_status="IsOperator", aircraft_type="Test Aircraft",
    )
    values.update(overrides)
    return FutureFlightCandidate(**values)


def standard_flights():
    first = flight(
        "DL1234", "ATL", "JFK",
        datetime(2026, 8, 20, 15, 30), datetime(2026, 8, 20, 17, 45),
    )
    second = flight(
        "DL5678", "JFK", "BOS",
        datetime(2026, 8, 20, 19, 10), datetime(2026, 8, 20, 20, 30),
    )
    return first, second


class FakeProvider:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def lookup_by_number(self, number, travel_date):
        self.calls.append((number, travel_date))
        value = self.values[number]
        if isinstance(value, Exception):
            raise value
        return value


def result(*candidates):
    outcome = "no_match" if not candidates else "unique_match" if len(candidates) == 1 else "multiple_candidates"
    return FutureFlightLookupResult(outcome, tuple(candidates))


def request():
    return V2ConnectionRequest(
        first_flight_number="DL1234", second_flight_number="DL5678", travel_date=DAY,
    )


def service(development_database, first=None, second=None):
    default_first, default_second = standard_flights()
    provider = FakeProvider({
        "DL1234": result(default_first) if first is None else first,
        "DL5678": result(default_second) if second is None else second,
    })
    return V2ItineraryService(
        provider, ConnectionRiskService(development_database, simulations=300, seed=42),
    ), provider


def test_two_unique_flights_use_real_estimator(development_database):
    subject, provider = service(development_database)
    response = subject.estimate(request())
    assert response.status == "success"
    assert response.itinerary.connection_airport == "JFK"
    assert response.itinerary.scheduled_layover_minutes == 85
    assert response.probability_result.connection_probability >= 0
    assert response.probability_result.model.version == "v1"
    assert len(provider.calls) == 2


def test_different_timezones_and_overnight_are_timezone_aware(development_database):
    import duckdb

    with duckdb.connect(str(development_database)) as connection:
        rows = [
            ("2024-08-20", 2024, 8, 2, "DL", "LAX", "ATL", 480, 960,
             "morning", float(delay), "synthetic-v2-test.csv")
            for delay in range(-10, 30)
        ]
        connection.executemany(
            "INSERT INTO historical_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    first = flight(
        "DL1234", "LAX", "ATL",
        datetime(2026, 8, 20, 8), datetime(2026, 8, 20, 16),
    )
    second = flight(
        "DL5678", "ATL", "BOS",
        datetime(2026, 8, 20, 18), datetime(2026, 8, 20, 20, 30),
    )
    subject, _ = service(development_database, result(first), result(second))
    timezone_response = subject.estimate(request())
    assert timezone_response.status == "success"
    assert timezone_response.itinerary.scheduled_layover_minutes == 120

    first, second = standard_flights()
    first = flight("DL1234", "ATL", "JFK", first.scheduled_departure_local,
                   datetime(2026, 8, 20, 23))
    second = flight("DL5678", "JFK", "BOS", datetime(2026, 8, 21, 1),
                    datetime(2026, 8, 21, 2, 15))
    subject, _ = service(development_database, result(first), result(second))
    response = subject.estimate(request())
    assert response.status == "success"
    assert response.itinerary.scheduled_layover_minutes == 120


def test_connection_airport_must_match_exactly(development_database):
    first, second = standard_flights()
    second = flight("DL5678", "LGA", "BOS", second.scheduled_departure_local,
                    second.scheduled_arrival_local)
    subject, _ = service(development_database, result(first), result(second))
    assert subject.estimate(request()).status == "invalid_connection_airport"


def test_reversed_chronology_is_rejected(development_database):
    first, second = standard_flights()
    second = flight("DL5678", "JFK", "BOS", datetime(2026, 8, 20, 16),
                    datetime(2026, 8, 20, 17))
    subject, _ = service(development_database, result(first), result(second))
    assert subject.estimate(request()).status == "invalid_chronology"


def test_first_no_match_short_circuits_second_lookup(development_database):
    subject, provider = service(development_database, result())
    response = subject.estimate(request())
    assert response.status == "schedule_not_found" and response.leg == "first"
    assert provider.calls == [("DL1234", DAY)]


def test_second_no_match_is_distinct(development_database):
    subject, provider = service(development_database, second=result())
    response = subject.estimate(request())
    assert response.status == "schedule_not_found" and response.leg == "second"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("ambiguous_leg", ["first", "second"])
def test_single_ambiguous_leg_preserves_all_candidates(development_database, ambiguous_leg):
    first, second = standard_flights()
    alternative = flight(
        first.marketing_flight_number if ambiguous_leg == "first" else second.marketing_flight_number,
        "ATL" if ambiguous_leg == "first" else "JFK",
        "LGA" if ambiguous_leg == "first" else "PWM",
        first.scheduled_departure_local if ambiguous_leg == "first" else second.scheduled_departure_local,
        first.scheduled_arrival_local if ambiguous_leg == "first" else second.scheduled_arrival_local,
    )
    first_value = result(first, alternative) if ambiguous_leg == "first" else result(first)
    second_value = result(second, alternative) if ambiguous_leg == "second" else result(second)
    subject, _ = service(development_database, first_value, second_value)
    response = subject.estimate(request())
    assert response.status == "ambiguous"
    assert response.ambiguous_legs[0].leg == ambiguous_leg
    assert len(response.ambiguous_legs[0].candidates) == 2


def test_both_legs_ambiguous_are_both_preserved(development_database):
    first, second = standard_flights()
    subject, provider = service(
        development_database, result(first, first), result(second, second),
    )
    response = subject.estimate(request())
    assert [item.leg for item in response.ambiguous_legs] == ["first", "second"]
    assert [len(item.candidates) for item in response.ambiguous_legs] == [2, 2]
    assert len(provider.calls) == 2


def test_missing_terminal_and_gate_do_not_block(development_database):
    first, second = standard_flights()
    first = flight(
        "DL1234", "ATL", "JFK", first.scheduled_departure_local,
        first.scheduled_arrival_local, departure_terminal=None, arrival_terminal=None,
        departure_gate=None, arrival_gate=None,
    )
    subject, _ = service(development_database, result(first), result(second))
    response = subject.estimate(request())
    assert response.status == "success"
    assert response.itinerary.first_flight.departure_gate is None
    assert response.warnings


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MissingRequiredScheduleFields("missing"), "provider_data_quality_error"),
        (ProviderNormalizationError("malformed"), "provider_data_quality_error"),
        (MissingProviderCredential("missing key"), "provider_configuration_error"),
        (ProviderAuthenticationError("auth"), "provider_configuration_error"),
        (ProviderQuotaError("quota"), "provider_temporarily_unavailable"),
        (ProviderRateLimitError("rate"), "provider_temporarily_unavailable"),
        (ProviderTimeoutError("timeout"), "provider_temporarily_unavailable"),
        (ProviderResponseError("network"), "provider_temporarily_unavailable"),
    ],
)
def test_provider_errors_are_safely_mapped_and_short_circuit(development_database, error, status):
    subject, provider = service(development_database, error)
    response = subject.estimate(request())
    assert response.status == status
    assert response.leg == "first"
    assert provider.calls == [("DL1234", DAY)]
    assert response.message != str(error)
    assert response.message.startswith("Future schedule provider")
