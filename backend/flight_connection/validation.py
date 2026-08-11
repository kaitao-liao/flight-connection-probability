"""Strictly temporal model-validation primitives and reproducible replay runner."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from .acquire import time_bucket
from .delay_model import (
    COHORT_LEVELS, DelayDistribution, TemporalHistory, select_temporal_distribution,
    subtract_months, temporal_delay_cohorts,
)
from .simulator import TransferTimeAssumption, simulate_connection


@dataclass(frozen=True)
class ValidationCase:
    flight_date: date
    carrier: str
    origin: str
    destination: str
    departure_minutes: int
    arrival_minutes: int
    realized_delay: float


def temporal_cohort_samples(
    database: str | Path, *, case: ValidationCase, history: TemporalHistory,
) -> dict[str, np.ndarray]:
    """Fetch every cohort with a mandatory strict upper date bound."""
    cohorts, _ = temporal_delay_cohorts(
        database,
        carrier=case.carrier,
        origin=case.origin,
        destination=case.destination,
        prediction_date=case.flight_date,
        scheduled_departure_minutes=case.departure_minutes,
        history=history,
    )
    return cohorts


def quantile_pinball_loss(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    error = actual - predicted
    return float(np.mean(np.maximum(quantile * error, (quantile - 1) * error)))


def brier_score(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    if len(probabilities) == 0 or len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must be nonempty and equal length")
    return float(np.mean((probabilities - outcomes) ** 2))


def calibration_bins(probabilities: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> list[dict]:
    if len(probabilities) != len(outcomes) or bins <= 0:
        raise ValueError("probabilities/outcomes must align and bins must be positive")
    indices = np.minimum((probabilities * bins).astype(int), bins - 1)
    result = []
    for index in range(bins):
        mask = indices == index
        if np.any(mask):
            result.append({
                "bin_start": index / bins, "bin_end": (index + 1) / bins,
                "count": int(np.sum(mask)),
                "mean_predicted_probability": float(np.mean(probabilities[mask])),
                "observed_success_frequency": float(np.mean(outcomes[mask])),
            })
    return result


def expected_calibration_error(bin_rows: list[dict]) -> float:
    total = sum(row["count"] for row in bin_rows)
    if not total:
        raise ValueError("at least one populated calibration bin is required")
    return float(sum(
        row["count"] / total * abs(row["mean_predicted_probability"] - row["observed_success_frequency"])
        for row in bin_rows
    ))


def empirical_crps(samples: np.ndarray, actual: float) -> float:
    """CRPS for an empirical distribution using an O(n log n) pairwise-distance identity."""
    values = np.sort(np.asarray(samples, dtype=float))
    if len(values) == 0:
        raise ValueError("samples must be nonempty")
    first = float(np.mean(np.abs(values - actual)))
    weights = 2 * np.arange(1, len(values) + 1) - len(values) - 1
    pairwise_term = float(2 * np.sum(weights * values) / (len(values) ** 2))
    return first - 0.5 * pairwise_term


def status_probabilities(
    database: str | Path, *, case: ValidationCase, history: TemporalHistory,
    min_observations: int = 30,
) -> dict:
    start, end = history.bounds()
    temporal = "flight_date < $end" + (" AND flight_date >= $start" if start else "")
    params = {"end": end, "start": start, "carrier": case.carrier,
              "origin": case.origin, "destination": case.destination}
    levels = [
        ("route_carrier", "reporting_carrier=$carrier AND origin=$origin AND destination=$destination"),
        ("route", "origin=$origin AND destination=$destination"),
        ("carrier", "reporting_carrier=$carrier"),
        ("global", "TRUE"),
    ]
    with duckdb.connect(str(database), read_only=True) as connection:
        for name, where in levels:
            import re
            parameter_names = set(re.findall(r"\$([a-z_]+)", where + " " + temporal))
            rows = connection.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE flight_status='completed'),
                       count(*) FILTER (WHERE cancelled), count(*) FILTER (WHERE diverted)
                FROM flight_records WHERE {where} AND {temporal}
            """, {key: value for key, value in params.items() if value is not None and key in parameter_names}).fetchone()
            total, completed, cancelled, diverted = map(int, rows)
            if total >= min_observations or (name == "global" and total):
                return {
                    "cohort_level": name, "sample_size": total,
                    "completion_probability": completed / total,
                    "cancellation_probability": cancelled / total,
                    "diversion_probability": diverted / total,
                }
    raise ValueError("no eligible prior status history")


def deterministic_cases(database: Path, *, test_year: int, limit: int, seed: int) -> list[ValidationCase]:
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute("""
            SELECT flight_date, reporting_carrier, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes, arrival_delay_minutes
            FROM historical_flights WHERE year=$year
            ORDER BY hash(flight_date, reporting_carrier, origin, destination,
                          crs_departure_minutes, $seed)
            LIMIT $limit
        """, {"year": test_year, "seed": seed, "limit": limit}).fetchall()
    return [ValidationCase(*row) for row in rows]


def deterministic_connection_cases(
    database: Path, *, test_year: int, limit: int, seed: int,
) -> list[tuple[ValidationCase, bool]]:
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute("""
            SELECT flight_date, reporting_carrier, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes,
                   coalesce(arrival_delay_minutes, 0), flight_status='completed'
            FROM flight_records WHERE year=$year
            ORDER BY hash(flight_date, reporting_carrier, flight_number, origin, destination,
                          crs_departure_minutes, $seed)
            LIMIT $limit
        """, {"year": test_year, "seed": seed + 1, "limit": limit}).fetchall()
    return [(ValidationCase(*row[:7]), bool(row[7])) for row in rows]


def scheduled_connection_layover(database: Path, case: ValidationCase, *, seed: int) -> int:
    """Use a same-date outbound BTS schedule when available; otherwise deterministic 45–180 minutes."""
    with duckdb.connect(str(database), read_only=True) as connection:
        row = connection.execute("""
            SELECT crs_departure_minutes - $arrival AS layover
            FROM flight_records
            WHERE flight_date=$date AND origin=$connection
              AND crs_departure_minutes - $arrival BETWEEN 45 AND 180
            ORDER BY hash(destination, flight_number, crs_departure_minutes, $seed)
            LIMIT 1
        """, {"arrival": case.arrival_minutes, "date": case.flight_date,
              "connection": case.destination, "seed": seed}).fetchone()
    if row:
        return int(row[0])
    digest = hashlib.sha256(
        f"{case.flight_date}|{case.carrier}|{case.origin}|{case.destination}|{seed}".encode()
    ).digest()
    return 45 + int.from_bytes(digest[:4], "big") % 136


def distribution_metrics(rows: list[dict]) -> dict:
    actual = np.asarray([row["actual"] for row in rows])
    result = {"sample_count": len(rows)}
    for key, quantile in (("p50", 0.5), ("p75", 0.75), ("p90", 0.9)):
        predicted = np.asarray([row[key] for row in rows])
        result[f"{key}_calibration"] = float(np.mean(actual <= predicted))
        result[f"{key}_pinball_loss"] = quantile_pinball_loss(actual, predicted, quantile)
    result["median_mae"] = float(np.mean(np.abs(actual - np.asarray([row["p50"] for row in rows]))))
    result["mean_crps"] = float(np.mean([row["crps"] for row in rows]))
    return result


def run_validation(
    database: Path, *, cases_per_split: int = 50, seed: int = 20260810,
    simulations: int = 2_000,
) -> dict:
    splits = [
        ("train_2023_test_2024", 2024, date(2023, 1, 1), date(2024, 1, 1)),
        ("train_2023_2024_test_2025", 2025, date(2023, 1, 1), date(2025, 1, 1)),
    ]
    windows = (6, 12, 24, None)
    thresholds = (10, 30, 60)
    report: dict = {"seed": seed, "cases_per_split": cases_per_split, "splits": {}}
    for split_name, test_year, fixed_start, fixed_end in splits:
        cases = deterministic_cases(database, test_year=test_year, limit=cases_per_split, seed=seed)
        split_report = {"windows": {}, "threshold_comparison": {}, "status": {}}
        cache: dict[tuple, dict[str, np.ndarray]] = {}
        for window in windows:
            label = f"{window}_months" if window else "fixed_training_period"
            evaluated = []
            fallback = Counter()
            by_carrier: dict[str, list[dict]] = defaultdict(list)
            by_route_volume: dict[str, list[dict]] = defaultdict(list)
            by_month: dict[str, list[dict]] = defaultdict(list)
            for case in cases:
                history = TemporalHistory(
                    prediction_date=case.flight_date,
                    lookback_months=window,
                    history_start=fixed_start,
                    history_end=None if window else fixed_end,
                )
                cohorts = temporal_cohort_samples(database, case=case, history=history)
                cache[(case, label)] = cohorts
                selected = select_temporal_distribution(cohorts, min_observations=30)
                q50, q75, q90 = np.quantile(selected.samples_minutes, (0.5, 0.75, 0.9))
                row = {"actual": case.realized_delay, "p50": float(q50), "p75": float(q75),
                       "p90": float(q90), "crps": empirical_crps(selected.samples_minutes, case.realized_delay)}
                evaluated.append(row)
                by_carrier[case.carrier].append(row)
                route_count = len(cohorts["route_carrier"])
                volume_group = "low_lt_100" if route_count < 100 else (
                    "medium_100_999" if route_count < 1000 else "high_1000_plus"
                )
                by_route_volume[volume_group].append(row)
                by_month[str(case.flight_date.month)].append(row)
                fallback[selected.fallback_level] += 1
            split_report["windows"][label] = {
                **distribution_metrics(evaluated),
                "fallback_usage": dict(fallback),
                "by_carrier": {carrier: distribution_metrics(values) for carrier, values in by_carrier.items()},
                "by_route_volume": {group: distribution_metrics(values) for group, values in by_route_volume.items()},
                "by_month": {month: distribution_metrics(values) for month, values in by_month.items()},
            }

        fixed_label = "fixed_training_period"
        for threshold in thresholds:
            evaluated, fallback = [], Counter()
            for case in cases:
                selected = select_temporal_distribution(cache[(case, fixed_label)], min_observations=threshold)
                q = np.quantile(selected.samples_minutes, (0.5, 0.75, 0.9))
                evaluated.append({"actual": case.realized_delay, "p50": float(q[0]), "p75": float(q[1]),
                                  "p90": float(q[2]), "crps": empirical_crps(selected.samples_minutes, case.realized_delay)})
                fallback[selected.fallback_level] += 1
            split_report["threshold_comparison"][str(threshold)] = {
                **distribution_metrics(evaluated), "fallback_usage": dict(fallback)
            }

        probabilities, outcomes, conditional_probabilities = [], [], []
        cancellation_rates, diversion_rates = [], []
        rng = np.random.default_rng(seed + test_year)
        transfer = TransferTimeAssumption()
        connection_cases = deterministic_connection_cases(
            database, test_year=test_year, limit=cases_per_split, seed=seed
        )
        for index, (case, actually_completed) in enumerate(connection_cases):
            history = TemporalHistory(case.flight_date, history_start=fixed_start, history_end=fixed_end)
            cohorts = temporal_cohort_samples(database, case=case, history=history)
            distribution = select_temporal_distribution(cohorts, min_observations=30)
            layover = scheduled_connection_layover(database, case, seed=seed + index)
            simulation = simulate_connection(
                distribution.samples_minutes, layover_minutes=layover, simulations=simulations,
                transfer=transfer, seed=seed + index,
            )
            status = status_probabilities(database, case=case, history=history)
            combined = status["completion_probability"] * simulation.probability
            realized_transfer = rng.triangular(transfer.minimum, transfer.mode, transfer.maximum)
            conditional_success = case.realized_delay + realized_transfer <= layover - 15
            realized = float(actually_completed and conditional_success)
            probabilities.append(combined)
            conditional_probabilities.append(simulation.probability)
            outcomes.append(realized)
            cancellation_rates.append(status["cancellation_probability"])
            diversion_rates.append(status["diversion_probability"])
        p = np.asarray(probabilities); y = np.asarray(outcomes)
        bins = calibration_bins(p, y)
        split_report["connection_calibration"] = {
            "interpretation": "model-consistency replay; BTS does not observe passenger transfer time",
            "sample_count": len(p), "brier_score": brier_score(p, y),
            "expected_calibration_error": expected_calibration_error(bins), "bins": bins,
            "mean_conditional_connection_probability": float(np.mean(conditional_probabilities)),
            "mean_combined_probability": float(np.mean(p)),
        }
        split_report["status"] = {
            "mean_prior_cancellation_probability": float(np.mean(cancellation_rates)),
            "mean_prior_diversion_probability": float(np.mean(diversion_rates)),
            "prototype_formula": "P(completed) * P(make connection | completed)",
            "public_api_changed": False,
        }
        report["splits"][split_name] = split_report
    report["extreme_delay_sensitivity"] = extreme_delay_sensitivity(database)
    return report


def extreme_delay_sensitivity(database: Path) -> dict:
    with duckdb.connect(str(database), read_only=True) as connection:
        bounds = connection.execute(
            "SELECT quantile_cont(arrival_delay_minutes, 0.001), quantile_cont(arrival_delay_minutes, 0.999) FROM historical_flights"
        ).fetchone()
        rows = connection.execute("""
            SELECT 'all_retained', median(arrival_delay_minutes), quantile_cont(arrival_delay_minutes,.75),
                   quantile_cont(arrival_delay_minutes,.9) FROM historical_flights
            UNION ALL
            SELECT 'winsorized_0.1_99.9', median(greatest(?, least(?, arrival_delay_minutes))),
                   quantile_cont(greatest(?, least(?, arrival_delay_minutes)),.75),
                   quantile_cont(greatest(?, least(?, arrival_delay_minutes)),.9) FROM historical_flights
        """, [bounds[0], bounds[1]] * 3).fetchall()
        all_delays = connection.execute("""
            SELECT arrival_delay_minutes FROM historical_flights
            USING SAMPLE reservoir(1000000 ROWS) REPEATABLE (20260810)
        """).fetchnumpy()["arrival_delay_minutes"]
    clipped = np.clip(all_delays, bounds[0], bounds[1])
    connection_probabilities = []
    for layover in (45, 85, 120):
        original = simulate_connection(all_delays, layover_minutes=layover, simulations=50_000, seed=20260810)
        winsorized = simulate_connection(clipped, layover_minutes=layover, simulations=50_000, seed=20260810)
        connection_probabilities.append({
            "layover_minutes": layover,
            "all_retained_probability": original.probability,
            "winsorized_probability": winsorized.probability,
            "absolute_difference": abs(original.probability - winsorized.probability),
        })
    return {
        "winsorization_bounds_minutes": list(map(float, bounds)),
        "connection_sensitivity_delay_sample_size": len(all_delays),
        "distribution_statistics": [
            {"treatment": row[0], "median": float(row[1]), "p75": float(row[2]), "p90": float(row[3])}
            for row in rows
        ],
        "connection_probability_sensitivity": connection_probabilities,
        "invalid_record_exclusion": "not performed; no clearly impossible delay records were established",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--mode", choices=("sampled", "full"), default="sampled")
    parser.add_argument("--cases-per-split", type=int)
    parser.add_argument("--seed", type=int, default=20260810)
    parser.add_argument("--output", type=Path, default=Path("data/processed/validation_report.json"))
    args = parser.parse_args()
    count = args.cases_per_split or (50 if args.mode == "sampled" else 1000)
    report = run_validation(args.database, cases_per_split=count, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
