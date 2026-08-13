"""Provider-neutral request and response schemas for the V2 schedule workflow."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import ConnectionRiskResponse


class V2ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_flight_number: str = Field(min_length=3, max_length=8)
    second_flight_number: str = Field(min_length=3, max_length=8)
    travel_date: date
    first_candidate_index: int | None = Field(default=None, ge=0)
    second_candidate_index: int | None = Field(default=None, ge=0)

    @field_validator("first_flight_number", "second_flight_number")
    @classmethod
    def normalize_flight_number(cls, value: str) -> str:
        normalized = value.replace(" ", "").upper()
        if not normalized.isascii() or not normalized.isalnum():
            raise ValueError("flight numbers must contain only ASCII letters and numbers")
        return normalized


class ResolvedFlight(BaseModel):
    marketing_carrier: str
    marketing_flight_number: str
    origin: str
    destination: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    departure_terminal: str | None = None
    arrival_terminal: str | None = None
    departure_gate: str | None = None
    arrival_gate: str | None = None
    aircraft_type: str | None = None
    provider_quality: list[str] = Field(default_factory=list)
    codeshare_status: str | None = None
    operating_carrier: str | None = None
    operating_flight_number: str | None = None


class ResolvedItinerary(BaseModel):
    first_flight: ResolvedFlight
    second_flight: ResolvedFlight
    connection_airport: str
    scheduled_layover_minutes: int


class AmbiguousLeg(BaseModel):
    leg: Literal["first", "second"]
    candidates: list[ResolvedFlight]


V2Status = Literal[
    "success", "ambiguous", "schedule_not_found", "invalid_connection_airport",
    "invalid_chronology", "provider_data_quality_error",
    "provider_temporarily_unavailable", "provider_configuration_error",
    "invalid_candidate_selection",
]


class V2ConnectionResponse(BaseModel):
    status: V2Status
    itinerary: ResolvedItinerary | None = None
    probability_result: ConnectionRiskResponse | None = None
    ambiguous_legs: list[AmbiguousLeg] = Field(default_factory=list)
    leg: Literal["first", "second"] | None = None
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
