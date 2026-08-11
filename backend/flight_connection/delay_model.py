"""Interpretable empirical arrival-delay estimator with strict temporal history."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import re

import duckdb
import numpy as np

from .acquire import time_bucket

V1_LOOKBACK_MONTHS = 24
FRESHNESS_WARNING_DAYS = 90

COHORT_LEVELS = [
    ("exact", "reporting_carrier=$carrier AND origin=$origin AND destination=$destination AND month=$month AND day_of_week=$dow AND departure_time_bucket=$bucket"),
    ("route_carrier_month_bucket", "reporting_carrier=$carrier AND origin=$origin AND destination=$destination AND month=$month AND departure_time_bucket=$bucket"),
    ("route_carrier_season", "reporting_carrier=$carrier AND origin=$origin AND destination=$destination AND ((month - $month + 12) % 12 IN (0,1,11))"),
    ("route_carrier", "reporting_carrier=$carrier AND origin=$origin AND destination=$destination"),
    ("route", "origin=$origin AND destination=$destination"),
    ("carrier", "reporting_carrier=$carrier"),
    ("global", "TRUE"),
]


def subtract_months(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class TemporalHistory:
    prediction_date: date
    lookback_months: int | None = None
    history_start: date | None = None
    history_end: date | None = None

    def bounds(self) -> tuple[date | None, date]:
        if self.history_end and self.history_end > self.prediction_date:
            raise ValueError("history end cannot be later than the prediction date")
        end = self.history_end or self.prediction_date
        rolling_start = (
            subtract_months(self.prediction_date, self.lookback_months)
            if self.lookback_months else None
        )
        starts = [candidate for candidate in (self.history_start, rolling_start) if candidate]
        return (max(starts) if starts else None), end


@dataclass(frozen=True)
class HistoricalCoverage:
    available_start: date
    available_end: date
    prediction_date: date
    effective_start: date
    effective_end: date
    cutoff_exclusive: date
    lookback_months: int | None
    freshness_warning: str | None


def database_date_coverage(database: str) -> tuple[date, date]:
    with duckdb.connect(database, read_only=True) as connection:
        start, end = connection.execute(
            "SELECT min(flight_date), max(flight_date) FROM historical_flights"
        ).fetchone()
    if start is None or end is None:
        raise ValueError("no eligible historical flights: database contains no dated records")
    return start, end


def temporal_delay_cohorts(
    database: str | Path,
    *,
    carrier: str,
    origin: str,
    destination: str,
    prediction_date: date,
    scheduled_departure_minutes: int,
    history: TemporalHistory,
) -> tuple[dict[str, np.ndarray], HistoricalCoverage]:
    """Fetch all cohorts through the single strict temporal query path."""
    start, end = history.bounds()
    if end > prediction_date:
        raise ValueError("history end cannot be later than the prediction date")
    database_path = str(Path(database).resolve())
    available_start, available_end = database_date_coverage(database_path)
    effective_start = max(candidate for candidate in (start, available_start) if candidate)
    effective_end = min(available_end, end - timedelta(days=1))
    days_after_coverage = (prediction_date - available_end).days
    freshness_warning = None
    if days_after_coverage > FRESHNESS_WARNING_DAYS:
        freshness_warning = (
            f"Historical BTS data ends on {available_end.isoformat()}, "
            f"{days_after_coverage} days before the requested prediction date."
        )
    coverage = HistoricalCoverage(
        available_start=available_start,
        available_end=available_end,
        prediction_date=prediction_date,
        effective_start=effective_start,
        effective_end=effective_end,
        cutoff_exclusive=end,
        lookback_months=history.lookback_months,
        freshness_warning=freshness_warning,
    )

    base = {
        "carrier": carrier.upper(), "origin": origin.upper(),
        "destination": destination.upper(), "month": prediction_date.month,
        "dow": prediction_date.isoweekday(), "bucket": time_bucket(scheduled_departure_minutes),
        "history_end": end, "history_start": start,
    }
    temporal = "flight_date < $history_end"
    if start is not None:
        temporal += " AND flight_date >= $history_start"
    result: dict[str, np.ndarray] = {}
    with duckdb.connect(database_path, read_only=True) as connection:
        for name, where in COHORT_LEVELS:
            parameter_names = set(re.findall(r"\$([a-z_]+)", where + " " + temporal))
            parameters = {key: value for key, value in base.items() if key in parameter_names}
            values = connection.execute(
                f"SELECT arrival_delay_minutes FROM historical_flights "
                f"WHERE {where} AND {temporal}",
                parameters,
            ).fetchnumpy()["arrival_delay_minutes"]
            result[name] = np.asarray(values, dtype=float)
    return result, coverage


@dataclass(frozen=True)
class DelayDistribution:
    samples_minutes: np.ndarray
    fallback_level: str
    observation_count: int
    coverage: HistoricalCoverage | None = None

    def quantiles(self) -> dict[str, float]:
        values = np.quantile(self.samples_minutes, [0.1, 0.25, 0.5, 0.75, 0.9])
        return {key: float(value) for key, value in zip(("p10", "p25", "p50", "p75", "p90"), values)}


def select_temporal_distribution(
    cohorts: dict[str, np.ndarray], *, min_observations: int,
    coverage: HistoricalCoverage | None = None,
) -> DelayDistribution:
    for name, _ in COHORT_LEVELS:
        values = cohorts[name]
        if len(values) >= min_observations or (name == "global" and len(values)):
            return DelayDistribution(values, name, len(values), coverage)
    raise ValueError("no eligible strictly prior historical flights")


def historical_delay_distribution(
    database: str | Path, *, carrier: str, origin: str, destination: str,
    travel_date: date, scheduled_departure_minutes: int, min_observations: int = 30,
    lookback_months: int = V1_LOOKBACK_MONTHS,
) -> DelayDistribution:
    """Serve frozen V1: strictly prior observations from the previous 24 months."""
    history = TemporalHistory(travel_date, lookback_months=lookback_months)
    cohorts, coverage = temporal_delay_cohorts(
        database,
        carrier=carrier,
        origin=origin,
        destination=destination,
        prediction_date=travel_date,
        scheduled_departure_minutes=scheduled_departure_minutes,
        history=history,
    )
    return select_temporal_distribution(
        cohorts, min_observations=min_observations, coverage=coverage,
    )


def historical_delay_cohort_counts(
    database: str | Path, *, carrier: str, origin: str, destination: str,
    travel_date: date, scheduled_departure_minutes: int,
    lookback_months: int = V1_LOOKBACK_MONTHS,
) -> dict[str, int]:
    cohorts, _ = temporal_delay_cohorts(
        database,
        carrier=carrier,
        origin=origin,
        destination=destination,
        prediction_date=travel_date,
        scheduled_departure_minutes=scheduled_departure_minutes,
        history=TemporalHistory(travel_date, lookback_months=lookback_months),
    )
    return {name: len(values) for name, values in cohorts.items()}
