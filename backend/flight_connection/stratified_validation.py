"""Larger weighted, stratified temporal validation study."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np

from .simulator import TransferTimeAssumption, simulate_connection
from .validation import (
    TemporalHistory, ValidationCase, empirical_crps, select_temporal_distribution,
    status_probabilities, temporal_cohort_samples,
)

SEED = 20260810


@dataclass(frozen=True)
class WeightedCase:
    case: ValidationCase
    weight: float
    route_volume_group: str
    season: str
    time_bucket: str
    severity: str
    status: str = "completed"


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    if len(values) == 0 or len(values) != len(weights) or np.sum(weights) <= 0:
        raise ValueError("values and positive weights must be nonempty and aligned")
    return float(np.average(values, weights=weights))


def weighted_pinball(actual: np.ndarray, predicted: np.ndarray, weights: np.ndarray, q: float) -> float:
    error = actual - predicted
    return weighted_mean(np.maximum(q * error, (q - 1) * error), weights)


def weighted_brier(probabilities: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> float:
    return weighted_mean((probabilities - outcomes) ** 2, weights)


def weighted_calibration_bins(
    probabilities: np.ndarray, outcomes: np.ndarray, weights: np.ndarray, bins: int = 10,
) -> list[dict]:
    indices = np.minimum((probabilities * bins).astype(int), bins - 1)
    result = []
    for index in range(bins):
        mask = indices == index
        if np.any(mask):
            result.append({
                "bin_start": index / bins, "bin_end": (index + 1) / bins,
                "sample_count": int(np.sum(mask)), "population_weight": float(np.sum(weights[mask])),
                "mean_predicted_probability": weighted_mean(probabilities[mask], weights[mask]),
                "observed_success_frequency": weighted_mean(outcomes[mask], weights[mask]),
            })
    return result


def weighted_ece(bin_rows: list[dict]) -> float:
    total = sum(row["population_weight"] for row in bin_rows)
    return float(sum(
        row["population_weight"] / total
        * abs(row["mean_predicted_probability"] - row["observed_success_frequency"])
        for row in bin_rows
    ))


def extract_error_cases(rows: list[dict], *, probability_key: str, outcome: float, limit: int = 20) -> list[dict]:
    reverse = outcome == 0
    return sorted(
        [row for row in rows if row["outcome"] == outcome],
        key=lambda row: row[probability_key], reverse=reverse,
    )[:limit]


def bootstrap_interval(
    size: int, metric: Callable[[np.ndarray], float], *, seed: int,
    replicates: int = 500, confidence: float = 0.95,
) -> dict:
    if size < 2 or replicates <= 0:
        raise ValueError("bootstrap requires at least two rows and positive replicates")
    rng = np.random.default_rng(seed)
    estimates = np.asarray([metric(rng.integers(0, size, size=size)) for _ in range(replicates)])
    alpha = (1 - confidence) / 2
    return {
        "replicates": replicates, "confidence": confidence,
        "lower": float(np.quantile(estimates, alpha)),
        "upper": float(np.quantile(estimates, 1 - alpha)),
    }


def _sample_query(table: str, test_year: int, target: int, seed: int, status_mode: bool) -> str:
    severity = """CASE
        WHEN arrival_delay_minutes <= 0 THEN 'early_or_on_time'
        WHEN arrival_delay_minutes <= 30 THEN 'moderate_1_30'
        WHEN arrival_delay_minutes <= 180 THEN 'severe_31_180'
        ELSE 'extreme_over_180' END""" if not status_mode else "flight_status"
    status_select = "flight_status" if status_mode else "'completed' AS flight_status"
    return f"""
        WITH route_counts AS (
            SELECT reporting_carrier, origin, destination, count(*) AS route_count
            FROM historical_flights WHERE flight_date < DATE '{test_year}-01-01'
            GROUP BY ALL
        ), candidates AS (
            SELECT f.flight_date, f.reporting_carrier, f.origin, f.destination,
                   f.crs_departure_minutes, f.crs_arrival_minutes,
                   coalesce(f.arrival_delay_minutes, 0) AS arrival_delay_minutes,
                   {status_select},
                   CASE WHEN coalesce(r.route_count,0) < 100 THEN 'low_lt_100'
                        WHEN r.route_count < 1000 THEN 'medium_100_999'
                        ELSE 'high_1000_plus' END AS volume_group,
                   CASE WHEN f.month IN (12,1,2) THEN 'winter'
                        WHEN f.month IN (3,4,5) THEN 'spring'
                        WHEN f.month IN (6,7,8) THEN 'summer' ELSE 'fall' END AS season,
                   f.departure_time_bucket AS time_bucket,
                   {severity} AS severity
            FROM {table} f LEFT JOIN route_counts r USING (reporting_carrier, origin, destination)
            WHERE f.year={test_year}
        ), ranked AS (
            SELECT *, count(*) OVER (PARTITION BY reporting_carrier, volume_group, season,
                                      time_bucket, severity) AS population_count,
                   row_number() OVER (PARTITION BY reporting_carrier, volume_group, season,
                                       time_bucket, severity
                       ORDER BY hash(flight_date, origin, destination, crs_departure_minutes,
                                     arrival_delay_minutes, flight_status, {seed})) AS rank,
                   count(DISTINCT reporting_carrier || '|' || volume_group || '|' || season || '|'
                         || time_bucket || '|' || severity) OVER () AS stratum_count
            FROM candidates
        ), sampled AS (
            SELECT * FROM ranked
            WHERE rank <= ceil({target}::DOUBLE / stratum_count)
            ORDER BY hash(flight_date, reporting_carrier, origin, destination,
                          crs_departure_minutes, arrival_delay_minutes, flight_status, {seed + 1})
            LIMIT {target}
        )
        SELECT *, population_count / count(*) OVER (
            PARTITION BY reporting_carrier, volume_group, season, time_bucket, severity
        ) AS sample_weight
        FROM sampled
        ORDER BY hash(flight_date, reporting_carrier, origin, destination,
                      crs_departure_minutes, arrival_delay_minutes, flight_status, {seed + 2})
    """


def stratified_cases(
    database: Path, *, test_year: int, target: int, seed: int, include_statuses: bool = False,
) -> list[WeightedCase]:
    table = "flight_records" if include_statuses else "historical_flights"
    query = _sample_query(table, test_year, target, seed, include_statuses)
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute(query).fetchall()
    return [WeightedCase(
        case=ValidationCase(*row[:7]), status=row[7], route_volume_group=row[8], season=row[9],
        time_bucket=row[10], severity=row[11], weight=float(row[15]),
    ) for row in rows]


def _arrival_metrics(rows: list[dict]) -> dict:
    actual = np.asarray([row["actual"] for row in rows]); weights = np.asarray([row["weight"] for row in rows])
    result = {"sample_count": len(rows), "population_weight": float(np.sum(weights))}
    for key, q in (("p50", .5), ("p75", .75), ("p90", .9)):
        predicted = np.asarray([row[key] for row in rows])
        result[f"{key}_coverage"] = weighted_mean((actual <= predicted).astype(float), weights)
        result[f"{key}_pinball_loss"] = weighted_pinball(actual, predicted, weights, q)
    result["median_mae"] = weighted_mean(np.abs(actual - np.asarray([r["p50"] for r in rows])), weights)
    result["mean_crps"] = weighted_mean(np.asarray([r["crps"] for r in rows]), weights)
    return result


def evaluate_arrivals(
    database: Path, cases: list[WeightedCase], *, test_year: int, bootstrap_replicates: int,
) -> dict:
    strategies = {
        "previous_24_months": lambda case: TemporalHistory(
            case.flight_date, lookback_months=24, history_start=date(2023, 1, 1)
        ),
        "all_prior_fixed_training": lambda case: TemporalHistory(
            case.flight_date, history_start=date(2023, 1, 1), history_end=date(test_year, 1, 1)
        ),
    }
    output = {}
    for strategy, history_factory in strategies.items():
        rows = []
        fallback = Counter()
        for weighted_case in cases:
            cohorts = temporal_cohort_samples(
                database, case=weighted_case.case, history=history_factory(weighted_case.case)
            )
            selected = select_temporal_distribution(cohorts, min_observations=30)
            q50, q75, q90 = np.quantile(selected.samples_minutes, (.5, .75, .9))
            rows.append({
                "actual": weighted_case.case.realized_delay, "p50": float(q50), "p75": float(q75),
                "p90": float(q90), "crps": empirical_crps(selected.samples_minutes, weighted_case.case.realized_delay),
                "weight": weighted_case.weight, "carrier": weighted_case.case.carrier,
                "route": f"{weighted_case.case.origin}-{weighted_case.case.destination}",
                "route_volume_group": weighted_case.route_volume_group, "season": weighted_case.season,
                "month": weighted_case.case.flight_date.month, "time_bucket": weighted_case.time_bucket,
                "severity": weighted_case.severity, "cohort_level": selected.fallback_level,
                "p90_error": weighted_case.case.realized_delay - float(q90),
            })
            fallback[selected.fallback_level] += 1
        aggregate = _arrival_metrics(rows)
        actual = np.asarray([r["actual"] for r in rows]); p90 = np.asarray([r["p90"] for r in rows]); weights = np.asarray([r["weight"] for r in rows])
        aggregate["p90_coverage_ci"] = bootstrap_interval(
            len(rows), lambda idx: weighted_mean((actual[idx] <= p90[idx]).astype(float), weights[idx]),
            seed=SEED + test_year, replicates=bootstrap_replicates,
        )
        subgroups = {}
        for dimension in ("carrier", "route_volume_group", "season", "month", "time_bucket", "severity", "cohort_level"):
            grouped = defaultdict(list)
            for row in rows:
                grouped[str(row[dimension])].append(row)
            subgroups[dimension] = {key: _arrival_metrics(value) for key, value in grouped.items()}
        worst_tail = sorted(rows, key=lambda row: row["p90_error"], reverse=True)[:20]
        output[strategy] = {
            "aggregate": aggregate, "fallback_usage": dict(fallback), "subgroups": subgroups,
            "worst_p90_undercoverage_cases": worst_tail,
        }
    return output


def _connection_schedule(database: Path, case: ValidationCase, seed: int) -> tuple[int, str, int]:
    with duckdb.connect(str(database), read_only=True) as connection:
        row = connection.execute("""
            SELECT crs_departure_minutes - $arrival, destination, crs_departure_minutes
            FROM flight_records WHERE flight_date=$date AND origin=$airport
              AND crs_departure_minutes - $arrival BETWEEN 45 AND 180
            ORDER BY hash(destination, flight_number, crs_departure_minutes, $seed) LIMIT 1
        """, {"arrival": case.arrival_minutes, "date": case.flight_date,
              "airport": case.destination, "seed": seed}).fetchone()
    if row:
        return int(row[0]), str(row[1]), int(row[2])
    return 90, "UNKNOWN", (case.arrival_minutes + 90) % 1440


def _connection_metrics(rows: list[dict], probability_key: str) -> dict:
    p = np.asarray([row[probability_key] for row in rows]); y = np.asarray([row["outcome"] for row in rows]); w = np.asarray([row["weight"] for row in rows])
    bins = weighted_calibration_bins(p, y, w)
    return {
        "sample_count": len(rows), "mean_predicted_probability": weighted_mean(p, w),
        "observed_success_frequency": weighted_mean(y, w), "brier_score": weighted_brier(p, y, w),
        "mean_calibration_gap": weighted_mean(p - y, w), "ece": weighted_ece(bins), "bins": bins,
    }


def evaluate_connections(
    database: Path, cases: list[WeightedCase], *, test_year: int, simulations: int,
    bootstrap_replicates: int,
) -> dict:
    rng = np.random.default_rng(SEED + test_year)
    transfer = TransferTimeAssumption()
    rows = []
    for index, weighted_case in enumerate(cases):
        case = weighted_case.case
        history = TemporalHistory(case.flight_date, history_start=date(2023, 1, 1), history_end=date(test_year, 1, 1))
        selected = select_temporal_distribution(
            temporal_cohort_samples(database, case=case, history=history), min_observations=30
        )
        layover, final_destination, connecting_departure = _connection_schedule(database, case, SEED + index)
        conditional = simulate_connection(
            selected.samples_minutes, layover_minutes=layover, simulations=simulations,
            transfer=transfer, seed=SEED + index,
        ).probability
        status = status_probabilities(database, case=case, history=history)
        combined = status["completion_probability"] * conditional
        transfer_minutes = rng.triangular(transfer.minimum, transfer.mode, transfer.maximum)
        outcome = float(weighted_case.status == "completed" and case.realized_delay + transfer_minutes <= layover - 15)
        rows.append({
            "prediction_date": str(case.flight_date), "carrier": case.carrier, "origin": case.origin,
            "connection": case.destination, "destination": final_destination,
            "first_departure_minutes": case.departure_minutes, "first_arrival_minutes": case.arrival_minutes,
            "connecting_departure_minutes": connecting_departure, "layover_minutes": layover,
            "conditional_probability": conditional, "combined_probability": combined, "outcome": outcome,
            "realized_arrival_delay": case.realized_delay if weighted_case.status == "completed" else None,
            "cohort_level": selected.fallback_level, "lookback_strategy": "all_prior_fixed_training",
            "flight_status": weighted_case.status, "weight": weighted_case.weight,
            "route_volume_group": weighted_case.route_volume_group, "season": weighted_case.season,
            "month": case.flight_date.month, "layover_bucket": (
                "45_59" if layover < 60 else "60_89" if layover < 90 else "90_119" if layover < 120 else "120_180"
            ),
        })
    output = {}
    for probability_key in ("conditional_probability", "combined_probability"):
        metrics = _connection_metrics(rows, probability_key)
        p = np.asarray([r[probability_key] for r in rows]); y = np.asarray([r["outcome"] for r in rows]); w = np.asarray([r["weight"] for r in rows])
        for name, fn, offset in (
            ("brier_score_ci", lambda idx: weighted_brier(p[idx], y[idx], w[idx]), 1),
            ("ece_ci", lambda idx: weighted_ece(weighted_calibration_bins(p[idx], y[idx], w[idx])), 2),
            ("mean_calibration_gap_ci", lambda idx: weighted_mean(p[idx] - y[idx], w[idx]), 3),
        ):
            metrics[name] = bootstrap_interval(len(rows), fn, seed=SEED + test_year + offset, replicates=bootstrap_replicates)
        output[probability_key] = metrics
    subgroups = {}
    for dimension in ("carrier", "route_volume_group", "layover_bucket", "season", "month"):
        grouped = defaultdict(list)
        for row in rows:
            grouped[str(row[dimension])].append(row)
        subgroups[dimension] = {
            key: _connection_metrics(value, "combined_probability") for key, value in grouped.items() if len(value) >= 5
        }
    output["combined_probability"]["subgroups"] = subgroups
    output["worst_overprediction_cases"] = extract_error_cases(
        rows, probability_key="combined_probability", outcome=0
    )
    output["strongest_underconfidence_cases"] = extract_error_cases(
        rows, probability_key="combined_probability", outcome=1
    )
    return output


def run_study(
    database: Path, *, delay_cases: int = 500, connection_cases: int = 300,
    simulations: int = 2_000, bootstrap_replicates: int = 500, seed: int = SEED,
) -> dict:
    started = time.perf_counter()
    report = {"seed": seed, "bootstrap_replicates": bootstrap_replicates, "holdouts": {}}
    for year in (2024, 2025):
        delay_sample = stratified_cases(database, test_year=year, target=delay_cases, seed=seed)
        connection_sample = stratified_cases(
            database, test_year=year, target=connection_cases, seed=seed + 100, include_statuses=True
        )
        report["holdouts"][str(year)] = {
            "arrival_sample_size": len(delay_sample), "connection_sample_size": len(connection_sample),
            "arrival": evaluate_arrivals(
                database, delay_sample, test_year=year, bootstrap_replicates=bootstrap_replicates
            ),
            "connection": evaluate_connections(
                database, connection_sample, test_year=year, simulations=simulations,
                bootstrap_replicates=bootstrap_replicates,
            ),
        }
    report["runtime_seconds"] = round(time.perf_counter() - started, 2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--delay-cases", type=int, default=500)
    parser.add_argument("--connection-cases", type=int, default=300)
    parser.add_argument("--simulations", type=int, default=2_000)
    parser.add_argument("--bootstrap-replicates", type=int, default=500)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path, default=Path("data/processed/stratified_validation.json"))
    args = parser.parse_args()
    report = run_study(
        args.database, delay_cases=args.delay_cases, connection_cases=args.connection_cases,
        simulations=args.simulations, bootstrap_replicates=args.bootstrap_replicates, seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"runtime_seconds": report["runtime_seconds"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
