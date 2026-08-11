from datetime import date, datetime, time

import pytest

from backend.flight_connection.timezone_validation import (
    INVALID_CHRONOLOGY_MESSAGE,
    airport_timezone,
    first_flight_duration_minutes,
    validate_supported_airports,
)


def duration(
    origin: str, connection: str, departure: time, arrival: time,
    *, travel_date: date = date(2026, 8, 9),
) -> tuple[int, bool]:
    return first_flight_duration_minutes(
        travel_date=travel_date,
        departure_time=departure,
        arrival_time=arrival,
        origin=origin,
        connection=connection,
    )


def test_impossible_lax_to_atl_schedule_is_rejected():
    with pytest.raises(ValueError, match="local time zones") as error:
        duration("LAX", "ATL", time(15, 30), time(17, 45))
    assert str(error.value) == INVALID_CHRONOLOGY_MESSAGE


def test_valid_same_day_lax_to_atl_schedule_is_accepted():
    assert duration("LAX", "ATL", time(15, 30), time(23)) == (270, False)


def test_overnight_lax_to_jfk_schedule_is_inferred():
    assert duration("LAX", "JFK", time(23, 30), time(7, 45)) == (315, True)


def test_same_timezone_atl_to_bos_schedule_is_accepted():
    assert duration("ATL", "BOS", time(15, 30), time(17, 45)) == (135, False)


def test_phoenix_does_not_observe_daylight_saving_time():
    phoenix = airport_timezone("PHX")
    los_angeles = airport_timezone("LAX")
    assert datetime(2026, 1, 15, tzinfo=phoenix).utcoffset().total_seconds() == -7 * 3600
    assert datetime(2026, 7, 15, tzinfo=phoenix).utcoffset().total_seconds() == -7 * 3600
    assert datetime(2026, 1, 15, tzinfo=los_angeles).utcoffset().total_seconds() == -8 * 3600
    assert datetime(2026, 7, 15, tzinfo=los_angeles).utcoffset().total_seconds() == -7 * 3600


def test_spring_dst_transition_uses_actual_utc_elapsed_time():
    assert duration(
        "ATL", "BOS", time(1, 30), time(3, 30), travel_date=date(2026, 3, 8)
    ) == (60, False)


def test_unknown_airport_timezone_is_rejected():
    with pytest.raises(ValueError, match="Timezone data is unavailable for airport XYZ"):
        validate_supported_airports("ATL", "XYZ")


def test_excessively_long_first_flight_is_rejected():
    with pytest.raises(ValueError, match="exceeds the 15-hour validation limit"):
        duration("ATL", "HNL", time(0), time(23))
