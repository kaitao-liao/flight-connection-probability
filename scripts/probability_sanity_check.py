"""Reproduce the focused V1 probability sanity checks against the production data."""
from __future__ import annotations

import argparse
from datetime import date, time
import json
from pathlib import Path
from statistics import mean, pstdev
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.flight_connection.api import create_app
from backend.flight_connection.delay_model import (
    COHORT_LEVELS,
    TemporalHistory,
    historical_delay_distribution,
    temporal_delay_cohorts,
)
from backend.flight_connection.deterministic_seed import deterministic_itinerary_seed
from backend.flight_connection.schemas import ConnectionRiskRequest
from backend.flight_connection.service import ConnectionRiskService
from backend.flight_connection.simulator import simulate_connection


DATABASE = Path("data/production/flights_production.duckdb")
PREDICTION_DATE = date(2025, 7, 15)
SIMULATIONS = 20_000
LAYOVERS = (20, 25, 30, 45, 60, 75, 90, 120)
ROUTES = {
    "AA": ("LAX", "DFW"),
    "DL": ("MCO", "ATL"),
    "UA": ("LGA", "ORD"),
    "WN": ("PHX", "DEN"),
    "AS": ("ANC", "SEA"),
    "B6": ("BOS", "DCA"),
}


def distribution(carrier: str, origin: str, destination: str, departure_minutes: int):
    return historical_delay_distribution(
        DATABASE,
        carrier=carrier,
        origin=origin,
        destination=destination,
        travel_date=PREDICTION_DATE,
        scheduled_departure_minutes=departure_minutes,
    )


def simulated_summary(delay_distribution, layover: int, *, seed: int | None = 20260811):
    result = simulate_connection(
        delay_distribution.samples_minutes,
        layover_minutes=layover,
        simulations=SIMULATIONS,
        seed=seed,
    )
    return {
        "probability": result.probability,
        "scenarios": result.scenario_probabilities,
    }


def repeated(delay_distribution, layover: int, runs: int, seed: int) -> dict[str, object]:
    values = [
        simulate_connection(
            delay_distribution.samples_minutes,
            layover_minutes=layover,
            simulations=SIMULATIONS,
            seed=seed,
        ).probability
        for _ in range(runs)
    ]
    displayed = sorted({f"{value * 100:.1f}%" for value in values})
    return {
        "runs": runs,
        "mean": mean(values),
        "minimum": min(values),
        "maximum": max(values),
        "standard_deviation": pstdev(values),
        "range": max(values) - min(values),
        "one_decimal_displays": displayed,
    }


def request_seed(
    carrier: str, origin: str, connection: str, destination: str,
    departure: time, arrival: time, connecting_departure: time,
) -> int:
    return deterministic_itinerary_seed(ConnectionRiskRequest(
        carrier=carrier,
        origin=origin,
        connection=connection,
        destination=destination,
        travel_date=PREDICTION_DATE,
        first_departure_time=departure,
        first_arrival_time=arrival,
        connecting_departure_time=connecting_departure,
    ))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("work/probability_sanity_results.json"))
    parser.add_argument("--repeat-runs", type=int, default=50)
    parser.add_argument("--api-runs", type=int, default=12)
    args = parser.parse_args()

    baseline = distribution("DL", "MCO", "ATL", 8 * 60)
    results: dict[str, object] = {
        "configuration": {
            "database": str(DATABASE),
            "prediction_date": PREDICTION_DATE.isoformat(),
            "simulations_per_estimate": SIMULATIONS,
            "deplaning_minutes": 20,
            "gate_transfer_triangular_minutes": [15, 25, 40],
            "boarding_cutoff_minutes": 15,
            "fixed_seed_for_comparisons": 20260811,
            "production_seed_strategy": "sha256_canonical_itinerary",
        }
    }

    results["layover"] = [
        {"layover_minutes": layover, **simulated_summary(baseline, layover)}
        for layover in LAYOVERS
    ]
    results["short_layovers"] = [
        {
            "layover_minutes": layover,
            **simulated_summary(baseline, layover),
            "gate_transfer_deadline_after_deplaning_and_cutoff_minutes": layover - 20 - 15,
        }
        for layover in (30, 45, 60)
    ]
    results["short_layover_delay_evidence"] = {
        "quantiles": baseline.quantiles(),
        "fraction_arriving_at_least_5_minutes_early": float(
            (baseline.samples_minutes <= -5).mean()
        ),
        "fraction_arriving_at_least_10_minutes_early": float(
            (baseline.samples_minutes <= -10).mean()
        ),
        "fraction_arriving_at_least_15_minutes_early": float(
            (baseline.samples_minutes <= -15).mean()
        ),
    }

    time_cases = (("morning", 8 * 60), ("afternoon", 14 * 60), ("evening", 19 * 60))
    time_results = []
    time_distributions = {}
    for label, departure_minutes in time_cases:
        current = distribution("DL", "MCO", "ATL", departure_minutes)
        time_distributions[label] = current
        q = current.quantiles()
        time_results.append({
            "time_bucket": label,
            "cohort_level": current.fallback_level,
            "sample_size": current.observation_count,
            "median_delay": q["p50"],
            "p75_delay": q["p75"],
            "p90_delay": q["p90"],
            **simulated_summary(current, 60),
        })
    results["time_of_day"] = time_results

    fallback_inputs = (
        ("exact", "HA", "HNL", "OGG"),
        ("month_bucket", "AA", "DFW", "LAS"),
        ("season", "AA", "CLT", "BNA"),
        ("route_carrier", "UA", "PBI", "ORD"),
    )
    fallback_results = []
    for intended, carrier, origin, destination in fallback_inputs:
        current = distribution(carrier, origin, destination, 8 * 60)
        cohorts, _ = temporal_delay_cohorts(
            DATABASE,
            carrier=carrier,
            origin=origin,
            destination=destination,
            prediction_date=PREDICTION_DATE,
            scheduled_departure_minutes=8 * 60,
            history=TemporalHistory(PREDICTION_DATE, lookback_months=24),
        )
        cohort_comparison = {}
        for cohort_name, _ in COHORT_LEVELS:
            values = cohorts[cohort_name]
            if len(values):
                cohort_comparison[cohort_name] = {
                    "sample_size": len(values),
                    "probability": simulate_connection(
                        values,
                        layover_minutes=60,
                        simulations=SIMULATIONS,
                        seed=20260811,
                    ).probability,
                }
        fallback_results.append({
            "intended_example": intended,
            "carrier": carrier,
            "route": f"{origin}-{destination}",
            "cohort_level": current.fallback_level,
            "sample_size": current.observation_count,
            "cohort_comparison": cohort_comparison,
            **simulated_summary(current, 60),
        })
    results["fallback"] = fallback_results

    carrier_results = []
    carrier_distributions = {}
    for carrier, (origin, destination) in ROUTES.items():
        current = distribution(carrier, origin, destination, 8 * 60)
        carrier_distributions[carrier] = current
        q = current.quantiles()
        carrier_results.append({
            "carrier": carrier,
            "route": f"{origin}-{destination}",
            "cohort_level": current.fallback_level,
            "sample_size": current.observation_count,
            "median_delay": q["p50"],
            "p90_delay": q["p90"],
            **simulated_summary(current, 60),
        })
    results["carriers"] = carrier_results

    results["direct_repeats"] = {
        "short_DL_MCO_ATL_30": repeated(
            baseline, 30, args.repeat_runs,
            request_seed("DL", "MCO", "ATL", "BOS", time(8), time(9, 30), time(10)),
        ),
        "medium_DL_MCO_ATL_60": repeated(
            baseline, 60, args.repeat_runs,
            request_seed("DL", "MCO", "ATL", "BOS", time(8), time(9, 30), time(10, 30)),
        ),
        "long_B6_BOS_DCA_90": repeated(
            carrier_distributions["B6"], 90, args.repeat_runs,
            request_seed("B6", "BOS", "DCA", "FLL", time(8), time(9, 30), time(11)),
        ),
    }

    api_payload = {
        "carrier": "DL",
        "origin": "MCO",
        "connection": "ATL",
        "destination": "BOS",
        "travel_date": PREDICTION_DATE.isoformat(),
        "first_departure_time": "08:00",
        "first_arrival_time": "09:30",
        "connecting_departure_time": "10:30",
    }
    api_values = []
    with TestClient(create_app(service=ConnectionRiskService(DATABASE))) as client:
        for _ in range(args.api_runs):
            response = client.post("/api/v1/connection-risk", json=api_payload)
            response.raise_for_status()
            api_values.append(response.json()["connection_probability"])
    results["api_repeats"] = {
        "runs": args.api_runs,
        "mean": mean(api_values),
        "minimum": min(api_values),
        "maximum": max(api_values),
        "standard_deviation": pstdev(api_values),
        "range": max(api_values) - min(api_values),
        "one_decimal_displays": sorted({f"{value * 100:.1f}%" for value in api_values}),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
