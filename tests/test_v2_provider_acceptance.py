from datetime import date, datetime, timezone

import pytest

from backend.flight_connection.future_flight_provider import (
    FutureFlightCandidate, FutureFlightLookupResult, MissingRequiredScheduleFields,
    ProviderAuthenticationError, ProviderNormalizationError, ProviderResponseError,
)
from backend.flight_connection.v2_provider_acceptance import (
    MAX_LIVE_LOOKUPS, AcceptanceCandidate, horizon_bucket, run_acceptance_study,
    study_plan, validate_candidates,
)

REFERENCE = date(2026, 8, 12)


def candidate(number="DL1234", day=date(2026, 8, 20), airline="DL"):
    return AcceptanceCandidate(number, day, airline, True, "manually checked")


def flight(
    number="DL1234", *, departure_terminal="S", arrival_terminal=None,
    departure_gate=None, arrival_gate=None, aircraft_type="Boeing 737-900",
    quality=("departure:Basic",), codeshare_status="IsOperator",
    operating_carrier="DL",
):
    return FutureFlightCandidate(
        source="test", retrieved_at_utc=datetime(2026, 8, 12, tzinfo=timezone.utc),
        flight_date=date(2026, 8, 20), marketing_carrier=number[:2],
        marketing_flight_number=number, operating_carrier=operating_carrier,
        operating_flight_number=number if operating_carrier else None,
        origin_iata="ATL", destination_iata="MIA",
        scheduled_departure_local=datetime(2026, 8, 20, 20, 52),
        scheduled_arrival_local=datetime(2026, 8, 20, 22, 47),
        departure_terminal=departure_terminal, arrival_terminal=arrival_terminal,
        departure_gate=departure_gate, arrival_gate=arrival_gate,
        status="Expected", quality=quality, codeshare_status=codeshare_status,
        aircraft_type=aircraft_type,
    )


class SequenceProvider:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def lookup_by_number(self, flight_number, flight_date):
        self.calls.append((flight_number, flight_date))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_candidate_validation_horizon_and_plan():
    values = [
        candidate(),
        candidate("AA100", date(2026, 9, 12), "AA"),
        candidate("UA200", date(2026, 11, 12), "UA"),
        candidate("WN300", date(2027, 2, 8), "WN"),
    ]
    assert [horizon_bucket(item.flight_date, REFERENCE) for item in values] == [
        "near_term", "approximately_30_days", "approximately_90_days",
        "approximately_180_days",
    ]
    plan = study_plan(values, reference_date=REFERENCE)
    assert plan["live_requests_planned"] == 4
    assert plan["maximum_estimated_units"] == 8
    assert plan["airlines"] == {"AA": 1, "DL": 1, "UA": 1, "WN": 1}


def test_quota_cap_duplicates_and_confirmation_are_enforced():
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_candidates(
            [candidate(f"DL{index + 1}") for index in range(MAX_LIVE_LOOKUPS + 1)],
            reference_date=REFERENCE,
        )
    with pytest.raises(ValueError, match="duplicate"):
        validate_candidates([candidate(), candidate()], reference_date=REFERENCE)
    with pytest.raises(ValueError, match="manually confirmed"):
        AcceptanceCandidate.from_mapping({
            "flight_number": "DL1234", "flight_date": "2026-08-20",
            "manually_confirmed": False, "confirmation_source": "",
        })
    with pytest.raises(ValueError, match="manually confirmed"):
        validate_candidates([
            AcceptanceCandidate("DL1234", date(2026, 8, 20), "DL", False, "")
        ], reference_date=REFERENCE)


def test_explicit_live_confirmation_is_required_before_provider_call():
    provider = SequenceProvider([])
    with pytest.raises(PermissionError, match="explicit live confirmation"):
        run_acceptance_study(
            provider, [candidate()], reference_date=REFERENCE, confirmed_live=False,
        )
    assert provider.calls == []


def test_aggregation_coverage_airline_horizon_and_outcomes():
    candidates = [
        candidate(), candidate("AA100", date(2026, 9, 12), "AA"),
        candidate("B6123", date(2026, 11, 12), "B6"),
    ]
    provider = SequenceProvider([
        FutureFlightLookupResult("unique_match", (flight(),)),
        FutureFlightLookupResult("no_match", ()),
        FutureFlightLookupResult("multiple_candidates", (
            flight("B6123", departure_terminal=None, operating_carrier=None,
                   codeshare_status="IsCodeshared", quality=()),
            flight("B6123", arrival_terminal="5", departure_gate="A1", arrival_gate="B2"),
        )),
    ])
    sleeps = []
    report = run_acceptance_study(
        provider, candidates, reference_date=REFERENCE, confirmed_live=True,
        sleep=sleeps.append,
    )
    assert len(provider.calls) == 3
    assert sleeps == [1.0, 1.0]
    assert report["totals"]["unique_match"] == 1
    assert report["totals"]["no_match"] == 1
    assert report["totals"]["multiple_candidates"] == 1
    assert report["coverage"]["required_complete_percent"] == 100.0
    assert report["coverage"]["has_departure_terminal_percent"] == pytest.approx(66.667)
    assert report["coverage"]["has_arrival_terminal_percent"] == pytest.approx(33.333)
    assert report["coverage"]["has_operating_carrier_percent"] == pytest.approx(66.667)
    assert report["coverage"]["has_operating_flight_number_percent"] == pytest.approx(66.667)
    assert report["coverage"]["has_status_percent"] == 100.0
    assert report["by_airline"]["AA"]["coverage"]["candidate_denominator"] == 0
    assert "approximately_90_days" in report["by_horizon"]


def test_missing_required_and_malformed_are_recorded_without_guessing():
    provider = SequenceProvider([
        MissingRequiredScheduleFields("missing"),
        ProviderNormalizationError("malformed"),
        FutureFlightLookupResult("no_match", ()),
    ])
    report = run_acceptance_study(
        provider,
        [
            candidate(), candidate("AA100", date(2026, 9, 12), "AA"),
            candidate("UA200", date(2026, 11, 12), "UA"),
        ],
        reference_date=REFERENCE, confirmed_live=True, sleep=lambda _: None,
    )
    assert report["totals"]["missing_required_fields"] == 1
    assert report["totals"]["malformed_response"] == 1
    assert report["totals"]["no_match"] == 1
    assert report["stopped_early"] is False


def test_systemic_provider_failure_stops_remaining_requests():
    provider = SequenceProvider([
        ProviderAuthenticationError("safe"),
        FutureFlightLookupResult("unique_match", (flight(),)),
    ])
    report = run_acceptance_study(
        provider,
        [candidate(), candidate("AA100", date(2026, 9, 12), "AA")],
        reference_date=REFERENCE, confirmed_live=True, sleep=lambda _: None,
    )
    assert len(provider.calls) == 1
    assert report["attempted"] == 1
    assert report["stopped_early"] is True
    assert report["stop_reason"] == "ProviderAuthenticationError"
    assert report["totals"]["provider_errors"] == 1


def test_systemic_error_report_includes_only_attached_sanitized_diagnostic():
    provider = SequenceProvider([
        ProviderResponseError(
            "Future-flight provider returned HTTP 403",
            diagnostic={"status": 403, "body_kind": "html", "body_marker": "cloudflare"},
        ),
    ])
    report = run_acceptance_study(
        provider, [candidate()], reference_date=REFERENCE, confirmed_live=True,
    )
    assert report["lookups"][0]["diagnostic"] == {
        "status": 403, "body_kind": "html", "body_marker": "cloudflare",
    }


def test_multiple_candidates_are_preserved_as_a_count_not_selected():
    provider = SequenceProvider([
        FutureFlightLookupResult("multiple_candidates", (flight(), flight())),
    ])
    report = run_acceptance_study(
        provider, [candidate()], reference_date=REFERENCE, confirmed_live=True,
    )
    assert report["lookups"][0]["candidate_count"] == 2
    assert report["totals"]["returned_candidates"] == 2
