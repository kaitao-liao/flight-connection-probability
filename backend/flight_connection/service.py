"""Application service for connection-risk estimates."""
from __future__ import annotations

from datetime import time
from pathlib import Path
import logging

import duckdb

from .delay_model import historical_delay_distribution
from .schemas import ConnectionRiskRequest, ConnectionRiskResponse
from .simulator import TransferTimeAssumption, simulate_connection
from .timezone_validation import first_flight_duration_minutes, validate_supported_airports

LOGGER = logging.getLogger("flight_connection.service")

EVENING_START = 18 * 60
NEXT_DAY_END = 12 * 60


def minutes_after_midnight(value: time) -> int:
    if value.second or value.microsecond:
        raise ValueError("scheduled times must have minute precision")
    return value.hour * 60 + value.minute


def elapsed_minutes(start: time, end: time, *, label: str) -> tuple[int, bool]:
    """Calculate local-clock elapsed minutes using the documented MVP rollover rule."""
    start_minutes = minutes_after_midnight(start)
    end_minutes = minutes_after_midnight(end)
    if end_minutes >= start_minutes:
        return end_minutes - start_minutes, False
    if start_minutes >= EVENING_START and end_minutes <= NEXT_DAY_END:
        return 24 * 60 - start_minutes + end_minutes, True
    raise ValueError(
        f"{label} is earlier than its start time and does not satisfy the overnight rule "
        "(start at or after 18:00 and end at or before 12:00)"
    )


class ConnectionRiskService:
    def __init__(
        self, database: str | Path, *, simulations: int = 20_000,
        boarding_cutoff_minutes: float = 15.0,
        transfer: TransferTimeAssumption = TransferTimeAssumption(),
        min_observations: int = 30, seed: int | None = None,
    ) -> None:
        self.database = Path(database)
        self.simulations = simulations
        self.boarding_cutoff_minutes = boarding_cutoff_minutes
        self.transfer = transfer
        self.min_observations = min_observations
        self.seed = seed

    def validate_database(self) -> None:
        """Fail fast when the serving artifact is absent or structurally invalid."""
        if not self.database.is_file():
            raise RuntimeError(f"historical flight database does not exist: {self.database}")
        required = {
            "flight_date", "month", "day_of_week", "reporting_carrier", "origin", "destination",
            "departure_time_bucket", "arrival_delay_minutes",
        }
        try:
            with duckdb.connect(str(self.database), read_only=True) as connection:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info('historical_flights')"
                    ).fetchall()
                }
                if not columns:
                    raise RuntimeError("historical_flights table is missing")
                missing = required - columns
                if missing:
                    raise RuntimeError(
                        f"historical_flights is missing required columns: {sorted(missing)}"
                    )
                if connection.execute(
                    "SELECT count(*) FROM historical_flights"
                ).fetchone()[0] == 0:
                    raise RuntimeError("historical_flights table is empty")
        except duckdb.Error as error:
            raise RuntimeError(f"historical flight database is unreadable: {error}") from error

    def estimate(self, itinerary: ConnectionRiskRequest) -> ConnectionRiskResponse:
        validate_supported_airports(itinerary.origin, itinerary.connection, itinerary.destination)
        first_flight_duration_minutes(
            travel_date=itinerary.travel_date,
            departure_time=itinerary.first_departure_time,
            arrival_time=itinerary.first_arrival_time,
            origin=itinerary.origin,
            connection=itinerary.connection,
        )

        layover, overnight = elapsed_minutes(
            itinerary.first_arrival_time,
            itinerary.connecting_departure_time,
            label="connecting departure",
        )
        if layover < self.boarding_cutoff_minutes:
            raise ValueError("scheduled layover must not be shorter than the boarding cutoff")
        if layover > 12 * 60:
            raise ValueError("scheduled layover must not exceed 12 hours for this MVP")

        departure_minutes = minutes_after_midnight(itinerary.first_departure_time)
        distribution = historical_delay_distribution(
            self.database,
            carrier=itinerary.carrier,
            origin=itinerary.origin,
            destination=itinerary.connection,
            travel_date=itinerary.travel_date,
            scheduled_departure_minutes=departure_minutes,
            min_observations=self.min_observations,
        )
        simulation = simulate_connection(
            distribution.samples_minutes,
            layover_minutes=layover,
            simulations=self.simulations,
            boarding_cutoff_minutes=self.boarding_cutoff_minutes,
            transfer=self.transfer,
            seed=self.seed,
        )
        quantiles = distribution.quantiles()
        coverage = distribution.coverage
        if coverage is None:
            raise RuntimeError("arrival-delay distribution omitted temporal coverage metadata")
        LOGGER.info(
            "Historical coverage prediction=%s available=%s..%s effective=%s..%s cutoff_exclusive=%s",
            coverage.prediction_date,
            coverage.available_start,
            coverage.available_end,
            coverage.effective_start,
            coverage.effective_end,
            coverage.cutoff_exclusive,
        )
        if coverage.freshness_warning:
            LOGGER.warning(coverage.freshness_warning)
        scenarios = simulation.scenario_probabilities
        return ConnectionRiskResponse(
            connection_probability=simulation.probability,
            scheduled_layover_minutes=layover,
            overnight_connection=overnight,
            historical_sample_size=distribution.observation_count,
            delay_statistics={
                "median_minutes": quantiles["p50"],
                "p75_minutes": quantiles["p75"],
                "p90_minutes": quantiles["p90"],
            },
            scenarios={
                "on_time": scenarios["on time"],
                "delay_15": scenarios["+15 min"],
                "delay_30": scenarios["+30 min"],
                "delay_45": scenarios["+45 min"],
            },
            model={
                "cohort_level": distribution.fallback_level,
                "arrival_delay_evidence": "observed_completed_non_diverted_BTS_flights",
                "transfer_time": {
                    "distribution": "triangular",
                    "minimum_minutes": self.transfer.minimum,
                    "mode_minutes": self.transfer.mode,
                    "maximum_minutes": self.transfer.maximum,
                    "evidence_type": "modeling_assumption",
                },
                "boarding_cutoff_minutes": self.boarding_cutoff_minutes,
                "simulation_count": self.simulations,
                "random_seed": self.seed,
                "exclusions": ["cancelled_flights", "diverted_flights"],
                "historical_coverage": {
                    "lookback_months": 24,
                    "available_start_date": coverage.available_start,
                    "available_end_date": coverage.available_end,
                    "requested_prediction_date": coverage.prediction_date,
                    "effective_history_start_date": coverage.effective_start,
                    "effective_history_end_date": coverage.effective_end,
                    "strict_cutoff_exclusive": coverage.cutoff_exclusive,
                    "freshness_warning": coverage.freshness_warning,
                },
            },
        )
