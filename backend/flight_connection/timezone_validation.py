"""Timezone-aware validation for user-entered first-flight schedules."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import airportsdata


MINIMUM_FLIGHT_DURATION_MINUTES = 30
MAXIMUM_FLIGHT_DURATION_MINUTES = 15 * 60
US_DOMESTIC_COUNTRY_CODES = frozenset({"AS", "GU", "MP", "PR", "US", "VI"})

INVALID_CHRONOLOGY_MESSAGE = (
    "These scheduled times are not valid after accounting for the airports' local time zones. "
    "Please check the first-flight departure and arrival times."
)


@lru_cache(maxsize=1)
def _supported_airports() -> dict[str, str]:
    """Load the pinned airportsdata IATA-to-IANA mapping once per process."""
    supported: dict[str, str] = {}
    for iata, airport in airportsdata.load("IATA").items():
        timezone_name = str(airport["tz"])
        if airport["country"] in US_DOMESTIC_COUNTRY_CODES and timezone_name:
            supported[iata] = timezone_name
    return supported


def airport_timezone(airport_code: str) -> ZoneInfo:
    """Return a supported U.S. airport timezone or a controlled validation error."""
    timezone_name = _supported_airports().get(airport_code)
    if timezone_name is None:
        raise ValueError(
            f"Timezone data is unavailable for airport {airport_code}. "
            "Use a supported U.S. airport code."
        )
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(
            f"IANA timezone data is unavailable for configured airport {airport_code}"
        ) from error


def validate_supported_airports(*airport_codes: str) -> None:
    """Ensure every itinerary airport is in the offline U.S. mapping."""
    for airport_code in airport_codes:
        airport_timezone(airport_code)


def first_flight_duration_minutes(
    *, travel_date: date, departure_time: time, arrival_time: time,
    origin: str, connection: str,
) -> tuple[int, bool]:
    """Validate first-flight chronology in UTC and infer at most one arrival-day rollover."""
    origin_timezone = airport_timezone(origin)
    connection_timezone = airport_timezone(connection)
    departure_local = datetime.combine(travel_date, departure_time, origin_timezone)
    departure_utc = departure_local.astimezone(timezone.utc)

    same_day_arrival = datetime.combine(travel_date, arrival_time, connection_timezone)
    same_day_arrival_utc = same_day_arrival.astimezone(timezone.utc)
    arrival_was_rolled = same_day_arrival_utc <= departure_utc
    arrival_utc = (
        (same_day_arrival + timedelta(days=1)).astimezone(timezone.utc)
        if arrival_was_rolled else same_day_arrival_utc
    )
    duration_minutes = int((arrival_utc - departure_utc).total_seconds() // 60)

    if duration_minutes < MINIMUM_FLIGHT_DURATION_MINUTES:
        raise ValueError(
            "Scheduled first-flight duration must be at least 30 minutes after accounting "
            "for airport time zones."
        )
    if duration_minutes > MAXIMUM_FLIGHT_DURATION_MINUTES:
        if arrival_was_rolled:
            raise ValueError(INVALID_CHRONOLOGY_MESSAGE)
        raise ValueError(
            "Scheduled first-flight duration exceeds the 15-hour validation limit after "
            "accounting for airport time zones."
        )
    return duration_minutes, arrival_was_rolled
