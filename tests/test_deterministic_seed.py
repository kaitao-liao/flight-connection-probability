from datetime import date, time
import subprocess
import sys

from backend.flight_connection.deterministic_seed import (
    canonical_itinerary,
    deterministic_itinerary_seed,
)
from backend.flight_connection.schemas import ConnectionRiskRequest


def itinerary(**overrides) -> ConnectionRiskRequest:
    values = {
        "carrier": "DL",
        "origin": "ATL",
        "connection": "JFK",
        "destination": "BOS",
        "travel_date": date(2026, 8, 20),
        "first_departure_time": time(15, 30),
        "first_arrival_time": time(17, 45),
        "connecting_departure_time": time(19, 10),
    }
    values.update(overrides)
    return ConnectionRiskRequest(**values)


def test_canonical_seed_changes_with_each_relevant_field():
    baseline = itinerary()
    baseline_seed = deterministic_itinerary_seed(baseline)
    variants = [
        (itinerary(carrier="AA"), "v1"),
        (itinerary(origin="BHM"), "v1"),
        (itinerary(connection="LGA"), "v1"),
        (itinerary(destination="MIA"), "v1"),
        (itinerary(travel_date=date(2026, 8, 21)), "v1"),
        (itinerary(first_departure_time=time(15, 31)), "v1"),
        (itinerary(first_arrival_time=time(17, 46)), "v1"),
        (itinerary(connecting_departure_time=time(19, 11)), "v1"),
        (baseline, "v2"),
    ]
    assert all(
        deterministic_itinerary_seed(value, model_version=version) != baseline_seed
        for value, version in variants
    )


def test_canonical_representation_is_normalized_and_stable():
    value = itinerary(carrier="dl", origin="atl")
    assert canonical_itinerary(value) == (
        '{"carrier":"DL","connecting_departure_time":"19:10",'
        '"connection":"JFK","destination":"BOS","first_arrival_time":"17:45",'
        '"first_departure_time":"15:30","model_version":"v1","origin":"ATL",'
        '"travel_date":"2026-08-20"}'
    )


def test_seed_is_stable_across_process_runs():
    code = """
from datetime import date, time
from backend.flight_connection.deterministic_seed import deterministic_itinerary_seed
from backend.flight_connection.schemas import ConnectionRiskRequest
value = ConnectionRiskRequest(
    carrier='DL', origin='ATL', connection='JFK', destination='BOS',
    travel_date=date(2026, 8, 20), first_departure_time=time(15, 30),
    first_arrival_time=time(17, 45), connecting_departure_time=time(19, 10),
)
print(deterministic_itinerary_seed(value))
"""
    outputs = [
        subprocess.check_output([sys.executable, "-c", code], text=True).strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    assert outputs[0] == str(deterministic_itinerary_seed(itinerary()))
    assert outputs[0] == "5695745043292164088"
