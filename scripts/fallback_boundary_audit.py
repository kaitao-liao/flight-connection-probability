"""Audit hard minimum-cohort boundaries without changing production behavior."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.flight_connection.delay_model import (  # noqa: E402
    COHORT_LEVELS, DelayDistribution, TemporalHistory, select_temporal_distribution,
)
from backend.flight_connection.acquire import time_bucket  # noqa: E402
from backend.flight_connection.simulator import (  # noqa: E402
    DEFAULT_BOARDING_CUTOFF_MINUTES, DEFAULT_DEPLANING_MINUTES,
    TransferTimeAssumption, simulate_connection,
)
from backend.flight_connection.validation import (  # noqa: E402
    ValidationCase, brier_score, calibration_bins, deterministic_cases,
    distribution_metrics, empirical_crps, expected_calibration_error, scheduled_connection_layover,
    temporal_cohort_samples,
)


TARGET_COUNTS = (20, 24, 25, 28, 29, 30, 31, 32, 35, 40)
THRESHOLDS = (20, 30, 40, 60)
SEED = 20260811
SIMULATIONS = 20_000
LEVEL_INDEX = {name: index for index, (name, _) in enumerate(COHORT_LEVELS)}


def fixed_history(year: int) -> TemporalHistory:
    return TemporalHistory(
        prediction_date=date(year, 7, 1),
        history_start=date(2023, 1, 1),
        history_end=date(year, 1, 1),
    )


def near_threshold_cases(database: Path, year: int, per_count: int) -> list[ValidationCase]:
    placeholders = ",".join("?" for _ in TARGET_COUNTS)
    query = f"""
        WITH counts AS (
          SELECT reporting_carrier, origin, destination, month, day_of_week,
                 departure_time_bucket, count(*) AS specific_n
          FROM historical_flights
          WHERE flight_date >= DATE '2023-01-01' AND flight_date < make_date(?, 1, 1)
          GROUP BY ALL
          HAVING count(*) IN ({placeholders})
        ), candidates AS (
          SELECT h.flight_date, h.reporting_carrier, h.origin, h.destination,
                 h.crs_departure_minutes, h.crs_arrival_minutes,
                 h.arrival_delay_minutes, c.specific_n,
                 row_number() OVER (
                   PARTITION BY c.specific_n, h.reporting_carrier, h.origin, h.destination,
                                h.month, h.day_of_week, h.departure_time_bucket
                   ORDER BY hash(h.flight_date, h.crs_departure_minutes, h.crs_arrival_minutes, ?)
                 ) AS cohort_row
          FROM historical_flights h JOIN counts c USING (
            reporting_carrier, origin, destination, month, day_of_week, departure_time_bucket
          )
          WHERE year(h.flight_date) = ?
        ), representatives AS (
          SELECT *, row_number() OVER (
            PARTITION BY specific_n
            ORDER BY hash(flight_date, reporting_carrier, origin, destination,
                          crs_departure_minutes, ?)
          ) AS count_row
          FROM candidates WHERE cohort_row = 1
        )
        SELECT flight_date, reporting_carrier, origin, destination,
               crs_departure_minutes, crs_arrival_minutes, arrival_delay_minutes
        FROM representatives WHERE count_row <= ?
        ORDER BY specific_n, count_row
    """
    parameters = [year, *TARGET_COUNTS, SEED, year, SEED + year, per_count]
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [ValidationCase(*row) for row in rows]


def next_broader(cohorts: dict[str, np.ndarray], level: str) -> DelayDistribution:
    for name, _ in COHORT_LEVELS[LEVEL_INDEX[level] + 1:]:
        if len(cohorts[name]):
            return DelayDistribution(cohorts[name], name, len(cohorts[name]))
    raise ValueError("no broader cohort available")


def first_eligible_broader(
    cohorts: dict[str, np.ndarray], level: str, minimum: int = 30,
) -> DelayDistribution:
    for name, _ in COHORT_LEVELS[LEVEL_INDEX[level] + 1:]:
        values = cohorts[name]
        if len(values) >= minimum or (name == "global" and len(values)):
            return DelayDistribution(values, name, len(values))
    raise ValueError("no eligible broader cohort")


def quantile_stats(values: np.ndarray) -> dict[str, float]:
    q = np.quantile(values, (0.5, 0.75, 0.9))
    return {"median": float(q[0]), "p75": float(q[1]), "p90": float(q[2])}


def connection_probability(values: np.ndarray, layover: int, seed: int) -> float:
    return simulate_connection(
        values, layover_minutes=layover, simulations=SIMULATIONS, seed=seed,
    ).probability


def blended_distribution(
    cohorts: dict[str, np.ndarray], *, boundary: int = 60, size: int = 2_000,
) -> DelayDistribution:
    exact = cohorts["exact"]
    if len(exact) >= boundary:
        return DelayDistribution(exact, "blend_exact_100pct", len(exact))
    if not len(exact):
        selected = select_temporal_distribution(cohorts, min_observations=30)
        return DelayDistribution(selected.samples_minutes, f"blend_{selected.fallback_level}",
                                 selected.observation_count)
    broader = first_eligible_broader(cohorts, "exact", minimum=30)
    weight = len(exact) / boundary
    specific_count = round(size * weight)
    specific_q = (np.arange(specific_count) + 0.5) / specific_count if specific_count else np.array([])
    broad_count = size - specific_count
    broad_q = (np.arange(broad_count) + 0.5) / broad_count if broad_count else np.array([])
    values = np.concatenate((
        np.quantile(exact, specific_q) if specific_count else np.array([]),
        np.quantile(broader.samples_minutes, broad_q) if broad_count else np.array([]),
    ))
    return DelayDistribution(values, f"blend_exact_{weight:.3f}_{broader.fallback_level}", len(values))


def boundary_rows(serving: Path, schedules: Path, per_count: int) -> list[dict]:
    rows = []
    for year in (2024, 2025):
        history = fixed_history(year)
        for index, case in enumerate(near_threshold_cases(schedules, year, per_count)):
            cohorts = temporal_cohort_samples(serving, case=case, history=history)
            specific = DelayDistribution(cohorts["exact"], "exact", len(cohorts["exact"]))
            production = select_temporal_distribution(cohorts, min_observations=30)
            broader = next_broader(cohorts, "exact")
            layover = scheduled_connection_layover(schedules, case, seed=SEED + year + index)
            case_seed = SEED + year * 10_000 + index
            specific_p = connection_probability(specific.samples_minutes, layover, case_seed)
            production_p = connection_probability(production.samples_minutes, layover, case_seed)
            broader_p = connection_probability(broader.samples_minutes, layover, case_seed)
            rows.append({
                **asdict(case), "flight_date": str(case.flight_date),
                "month": case.flight_date.month,
                "time_bucket": time_bucket(case.departure_minutes),
                "specific_level": "exact", "specific_n": specific.observation_count,
                "fallback_level": production.fallback_level,
                "fallback_n": production.observation_count,
                "immediate_broader_level": broader.fallback_level,
                "immediate_broader_n": broader.observation_count,
                "layover_minutes": layover,
                "specific_delay_statistics": quantile_stats(specific.samples_minutes),
                "fallback_delay_statistics": quantile_stats(production.samples_minutes),
                "specific_probability": specific_p,
                "production_probability": production_p,
                "immediate_broader_probability": broader_p,
                "production_displacement_pp": abs(specific_p - production_p) * 100,
                "boundary_contrast_pp": abs(specific_p - broader_p) * 100,
            })
    return rows


def method_metrics(rows: list[dict], fallback: Counter) -> dict:
    distribution_rows = [row["distribution"] for row in rows]
    probabilities = np.asarray([row["probability"] for row in rows])
    outcomes = np.asarray([row["outcome"] for row in rows])
    bins = calibration_bins(probabilities, outcomes)
    level_indices = [LEVEL_INDEX[row["level"]] for row in rows if row["level"] in LEVEL_INDEX]
    result = {
        **distribution_metrics(distribution_rows),
        "brier_score": brier_score(probabilities, outcomes),
        "expected_calibration_error": expected_calibration_error(bins),
        "mean_connection_probability": float(np.mean(probabilities)),
        "fallback_usage": dict(fallback),
        "mean_cohort_level_index": float(np.mean(level_indices)) if level_indices else None,
    }
    if all("specific_weight" in row for row in rows):
        result["mean_specific_weight"] = float(np.mean([
            row["specific_weight"] for row in rows
        ]))
    return result


def threshold_validation(serving: Path, schedules: Path, cases_per_year: int) -> dict:
    report = {}
    transfer = TransferTimeAssumption()
    for year in (2024, 2025):
        history = fixed_history(year)
        cases = deterministic_cases(schedules, test_year=year, limit=cases_per_year, seed=SEED)
        cache = [(case, temporal_cohort_samples(serving, case=case, history=history)) for case in cases]
        layovers = [scheduled_connection_layover(schedules, case, seed=SEED + i)
                    for i, (case, _) in enumerate(cache)]
        outcome_rng = np.random.default_rng(SEED + year)
        realized_transfers = outcome_rng.triangular(
            transfer.minimum, transfer.mode, transfer.maximum, len(cache)
        )
        outcomes = [float(
            case.realized_delay + DEFAULT_DEPLANING_MINUTES + realized_transfers[i]
            + DEFAULT_BOARDING_CUTOFF_MINUTES <= layovers[i]
        ) for i, (case, _) in enumerate(cache)]
        methods: dict[str, dict] = {}
        for threshold in THRESHOLDS:
            evaluated, fallback = [], Counter()
            for index, (case, cohorts) in enumerate(cache):
                selected = select_temporal_distribution(cohorts, min_observations=threshold)
                q = np.quantile(selected.samples_minutes, (0.5, 0.75, 0.9))
                probability = connection_probability(
                    selected.samples_minutes, layovers[index], SEED + year * 10_000 + index,
                )
                evaluated.append({
                    "distribution": {
                        "actual": case.realized_delay, "p50": float(q[0]),
                        "p75": float(q[1]), "p90": float(q[2]),
                        "crps": empirical_crps(selected.samples_minutes, case.realized_delay),
                    },
                    "probability": probability, "outcome": outcomes[index],
                    "level": selected.fallback_level,
                })
                fallback[selected.fallback_level] += 1
            methods[f"hard_n{threshold}"] = method_metrics(evaluated, fallback)

        evaluated, fallback = [], Counter()
        for index, (case, cohorts) in enumerate(cache):
            selected = blended_distribution(cohorts)
            q = np.quantile(selected.samples_minutes, (0.5, 0.75, 0.9))
            probability = connection_probability(
                selected.samples_minutes, layovers[index], SEED + year * 10_000 + index,
            )
            evaluated.append({
                "distribution": {
                    "actual": case.realized_delay, "p50": float(q[0]),
                    "p75": float(q[1]), "p90": float(q[2]),
                    "crps": empirical_crps(selected.samples_minutes, case.realized_delay),
                },
                "probability": probability, "outcome": outcomes[index],
                "level": selected.fallback_level,
                "specific_weight": min(len(cohorts["exact"]) / 60, 1),
            })
            fallback[selected.fallback_level] += 1
        methods["research_blend_n_over_60"] = method_metrics(evaluated, fallback)
        report[str(year)] = {"cases": len(cache), "methods": methods}
    return report


def summarize_boundary(rows: list[dict]) -> dict:
    displacement = np.asarray([row["production_displacement_pp"] for row in rows])
    contrast = np.asarray([row["boundary_contrast_pp"] for row in rows])
    below = np.asarray([
        row["production_displacement_pp"] for row in rows if row["specific_n"] < 30
    ])
    carrier_material = Counter(
        row["carrier"] for row in rows if row["production_displacement_pp"] >= 10
    )
    return {
        "case_count": len(rows),
        "counts": dict(Counter(row["specific_n"] for row in rows)),
        "production_displacement_pp": {
            "median": float(np.median(displacement)), "p90": float(np.quantile(displacement, .9)),
            "p95": float(np.quantile(displacement, .95)), "maximum": float(np.max(displacement)),
            "at_least_5pp": int(np.sum(displacement >= 5)),
            "at_least_10pp": int(np.sum(displacement >= 10)),
        },
        "below_30_displacement_pp": {
            "case_count": len(below), "median": float(np.median(below)),
            "p95": float(np.quantile(below, .95)), "maximum": float(np.max(below)),
        },
        "specific_vs_immediate_broader_contrast_pp": {
            "median": float(np.median(contrast)), "p95": float(np.quantile(contrast, .95)),
            "maximum": float(np.max(contrast)),
        },
        "material_jump_carriers": dict(carrier_material),
        "worst_cases": sorted(
            rows, key=lambda row: row["production_displacement_pp"], reverse=True
        )[:15],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serving-database", type=Path,
                        default=Path("data/production/flights_production.duckdb"))
    parser.add_argument("--schedule-database", type=Path,
                        default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--per-count", type=int, default=15)
    parser.add_argument("--validation-cases-per-year", type=int, default=150)
    parser.add_argument("--output", type=Path, default=Path("work/fallback_boundary_audit.json"))
    args = parser.parse_args()
    rows = boundary_rows(args.serving_database, args.schedule_database, args.per_count)
    report = {
        "configuration": {
            "target_specific_counts": TARGET_COUNTS, "hard_thresholds": THRESHOLDS,
            "seed": SEED, "simulations": SIMULATIONS,
            "blend": "exact weight=min(exact_n/60,1); remainder from first broader N>=30",
            "holdouts": "fixed 2023 -> 2024; fixed 2023-2024 -> 2025",
        },
        "boundary_summary": summarize_boundary(rows),
        "near_threshold_cases": rows,
        "threshold_validation": threshold_validation(
            args.serving_database, args.schedule_database, args.validation_cases_per_year,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
