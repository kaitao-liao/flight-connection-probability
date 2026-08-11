"""Stable per-itinerary Monte Carlo seed derivation."""
from __future__ import annotations

from datetime import time
import hashlib
import json

from .schemas import ConnectionRiskRequest


MODEL_VERSION = "v1"


def _canonical_time(value: time) -> str:
    if value.second or value.microsecond:
        raise ValueError("scheduled times must have minute precision")
    return value.strftime("%H:%M")


def canonical_itinerary(
    itinerary: ConnectionRiskRequest, *, model_version: str = MODEL_VERSION,
) -> str:
    """Return the normalized, versioned probability-request identity."""
    normalized_version = model_version.strip().lower()
    if not normalized_version:
        raise ValueError("model version must not be empty")
    values = {
        "carrier": itinerary.carrier.strip().upper(),
        "connection": itinerary.connection.strip().upper(),
        "connecting_departure_time": _canonical_time(itinerary.connecting_departure_time),
        "destination": itinerary.destination.strip().upper(),
        "first_arrival_time": _canonical_time(itinerary.first_arrival_time),
        "first_departure_time": _canonical_time(itinerary.first_departure_time),
        "model_version": normalized_version,
        "origin": itinerary.origin.strip().upper(),
        "travel_date": itinerary.travel_date.isoformat(),
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def deterministic_itinerary_seed(
    itinerary: ConnectionRiskRequest, *, model_version: str = MODEL_VERSION,
) -> int:
    """Derive a stable unsigned 64-bit seed from a canonical itinerary."""
    digest = hashlib.sha256(
        canonical_itinerary(itinerary, model_version=model_version).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)
