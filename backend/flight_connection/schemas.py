"""Versioned HTTP request and response schemas."""
from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConnectionRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carrier: str = Field(min_length=2, max_length=3)
    origin: str
    connection: str
    destination: str
    travel_date: date
    first_departure_time: time
    first_arrival_time: time
    connecting_departure_time: time

    @field_validator("carrier")
    @classmethod
    def normalize_carrier(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalnum():
            raise ValueError("carrier must contain only letters and numbers")
        return normalized

    @field_validator("origin", "connection", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha() or not normalized.isascii():
            raise ValueError("airport codes must be three ASCII letters")
        return normalized

    @model_validator(mode="after")
    def validate_distinct_airports(self) -> "ConnectionRiskRequest":
        if len({self.origin, self.connection, self.destination}) != 3:
            raise ValueError("origin, connection, and destination must be distinct")
        return self


class DelayStatistics(BaseModel):
    median_minutes: float
    p75_minutes: float
    p90_minutes: float


class ScenarioProbabilities(BaseModel):
    on_time: float = Field(ge=0, le=1)
    delay_15: float = Field(ge=0, le=1)
    delay_30: float = Field(ge=0, le=1)
    delay_45: float = Field(ge=0, le=1)


class TransferTimeModel(BaseModel):
    distribution: Literal["triangular"]
    minimum_minutes: float
    mode_minutes: float
    maximum_minutes: float
    evidence_type: Literal["modeling_assumption"]


class HistoricalCoverageDetails(BaseModel):
    lookback_months: Literal[24] = 24
    available_start_date: date
    available_end_date: date
    requested_prediction_date: date
    effective_history_start_date: date
    effective_history_end_date: date
    strict_cutoff_exclusive: date
    freshness_warning: str | None


class ModelDetails(BaseModel):
    version: Literal["v1"] = "v1"
    cohort_level: str
    arrival_delay_evidence: Literal["observed_completed_non_diverted_BTS_flights"]
    transfer_time: TransferTimeModel
    boarding_cutoff_minutes: float
    simulation_count: int
    random_seed: int | None
    exclusions: list[str]
    historical_coverage: HistoricalCoverageDetails


class ConnectionRiskResponse(BaseModel):
    connection_probability: float = Field(ge=0, le=1)
    scheduled_layover_minutes: int
    overnight_connection: bool
    historical_sample_size: int
    delay_statistics: DelayStatistics
    scenarios: ScenarioProbabilities
    model: ModelDetails
