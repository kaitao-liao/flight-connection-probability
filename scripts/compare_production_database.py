"""Compare frozen-V1 outputs between full and serving-only DuckDB artifacts."""
from __future__ import annotations

import argparse
from datetime import date, time
import json
from pathlib import Path

import duckdb

from backend.flight_connection.schemas import ConnectionRiskRequest
from backend.flight_connection.service import ConnectionRiskService

BUCKET_TIMES = {
    "overnight": (time(3, 0), time(5, 0), time(6, 30)),
    "morning": (time(9, 0), time(11, 0), time(12, 30)),
    "afternoon": (time(15, 0), time(17, 0), time(18, 30)),
    "evening": (time(19, 0), time(21, 0), time(22, 30)),
}


def representative_requests(database: Path, count: int) -> list[ConnectionRiskRequest]:
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
              SELECT reporting_carrier, origin, destination, month, day_of_week,
                     departure_time_bucket, count(*) AS observations
              FROM historical_flights
              GROUP BY ALL
              HAVING count(*) >= 30
            ) cohorts
            ORDER BY hash(reporting_carrier, origin, destination, month,
                          day_of_week, departure_time_bucket)
            LIMIT ?
            """,
            [count],
        ).fetchall()
    requests = []
    for carrier, origin, connection, month, day_of_week, bucket, _ in rows:
        day = 1 + ((int(day_of_week) - date(2025, int(month), 1).isoweekday()) % 7)
        departure, arrival, connecting = BUCKET_TIMES[bucket]
        final_destination = next(
            code for code in ("ZZZ", "YYY", "XXX") if code not in {origin, connection}
        )
        requests.append(ConnectionRiskRequest(
            carrier=carrier,
            origin=origin,
            connection=connection,
            destination=final_destination,
            travel_date=date(2025, int(month), day),
            first_departure_time=departure,
            first_arrival_time=arrival,
            connecting_departure_time=connecting,
        ))
    return requests


def compare(full: Path, production: Path, *, cases: int, seed: int) -> dict:
    full_service = ConnectionRiskService(full, simulations=2_000, seed=seed)
    production_service = ConnectionRiskService(production, simulations=2_000, seed=seed)
    maximum_probability_difference = 0.0
    maximum_statistic_differences = {
        "median_minutes": 0.0, "p75_minutes": 0.0, "p90_minutes": 0.0,
    }
    cohort_mismatches = 0
    sample_size_mismatches = 0
    for request in representative_requests(full, cases):
        expected = full_service.estimate(request)
        actual = production_service.estimate(request)
        maximum_probability_difference = max(
            maximum_probability_difference,
            abs(expected.connection_probability - actual.connection_probability),
            *(abs(expected.scenarios.model_dump()[key] - actual.scenarios.model_dump()[key])
              for key in expected.scenarios.model_dump()),
        )
        for key in maximum_statistic_differences:
            maximum_statistic_differences[key] = max(
                maximum_statistic_differences[key],
                abs(expected.delay_statistics.model_dump()[key] - actual.delay_statistics.model_dump()[key]),
            )
        cohort_mismatches += expected.model.cohort_level != actual.model.cohort_level
        sample_size_mismatches += expected.historical_sample_size != actual.historical_sample_size
    return {
        "cases": cases,
        "random_seed": seed,
        "simulations_per_case": 2_000,
        "maximum_probability_difference": maximum_probability_difference,
        "maximum_p50_difference_minutes": maximum_statistic_differences["median_minutes"],
        "maximum_p75_difference_minutes": maximum_statistic_differences["p75_minutes"],
        "maximum_p90_difference_minutes": maximum_statistic_differences["p90_minutes"],
        "cohort_mismatches": cohort_mismatches,
        "sample_size_mismatches": sample_size_mismatches,
        "exact_match": maximum_probability_difference == 0
            and all(value == 0 for value in maximum_statistic_differences.values())
            and cohort_mismatches == 0 and sample_size_mismatches == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--production", type=Path, default=Path("data/production/flights_production.duckdb"))
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260810)
    args = parser.parse_args()
    print(json.dumps(compare(args.full, args.production, cases=args.cases, seed=args.seed), indent=2))


if __name__ == "__main__":
    main()
