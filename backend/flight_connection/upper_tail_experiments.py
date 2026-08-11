"""Leakage-safe accelerated upper-tail estimator experiments."""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from .acquire import time_bucket
from .delay_model import COHORT_LEVELS, DelayDistribution
from .simulator import TransferTimeAssumption, simulate_connection
from .stratified_validation import (
    SEED, WeightedCase, stratified_cases, weighted_brier, weighted_calibration_bins,
    weighted_ece, weighted_mean, weighted_pinball,
)
from .validation import (
    TemporalHistory, ValidationCase, empirical_crps, status_probabilities,
    temporal_cohort_samples,
)

COHORT_SQL = dict(COHORT_LEVELS)
CANDIDATES = ("baseline", "hierarchical_tail_pooling", "recent_tail_augmentation", "tail_aware_carrier_month_bucket")


class LazyTemporalStore:
    """Exact lazy cohort cache. Cache keys include strict temporal bounds."""

    def __init__(self, database: Path):
        self.database = database
        self.connection = duckdb.connect(str(database), read_only=True)
        self.cache: dict[tuple, np.ndarray] = {}

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LazyTemporalStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _base(case: ValidationCase, history: TemporalHistory) -> tuple[dict, str]:
        start, end = history.bounds()
        if end > case.flight_date:
            raise ValueError("history end cannot be later than prediction date")
        parameters = {
            "carrier": case.carrier.upper(), "origin": case.origin.upper(),
            "destination": case.destination.upper(), "month": case.flight_date.month,
            "dow": case.flight_date.isoweekday(), "bucket": time_bucket(case.departure_minutes),
            "start": start, "end": end,
        }
        temporal = "flight_date < $end" + (" AND flight_date >= $start" if start else "")
        return parameters, temporal

    def cohort(self, case: ValidationCase, history: TemporalHistory, level: str) -> np.ndarray:
        parameters, temporal = self._base(case, history)
        key = (case.flight_date, case.carrier, case.origin, case.destination, case.departure_minutes,
               history.bounds(), level)
        if key in self.cache:
            return self.cache[key]
        where = COHORT_SQL[level]
        names = set(re.findall(r"\$([a-z_]+)", where + " " + temporal))
        values = self.connection.execute(
            f"SELECT arrival_delay_minutes FROM historical_flights WHERE {where} AND {temporal}",
            {name: value for name, value in parameters.items() if name in names and value is not None},
        ).fetchnumpy()["arrival_delay_minutes"]
        result = np.asarray(values, dtype=float)
        self.cache[key] = result
        return result

    def custom_cohort(
        self, case: ValidationCase, history: TemporalHistory, *, name: str, where: str,
    ) -> np.ndarray:
        parameters, temporal = self._base(case, history)
        key = (case.flight_date, case.carrier, case.origin, case.destination, case.departure_minutes,
               history.bounds(), name)
        if key in self.cache:
            return self.cache[key]
        names = set(re.findall(r"\$([a-z_]+)", where + " " + temporal))
        values = self.connection.execute(
            f"SELECT arrival_delay_minutes FROM historical_flights WHERE {where} AND {temporal}",
            {name_: value for name_, value in parameters.items() if name_ in names and value is not None},
        ).fetchnumpy()["arrival_delay_minutes"]
        result = np.asarray(values, dtype=float)
        self.cache[key] = result
        return result

    def select(self, case: ValidationCase, history: TemporalHistory, min_observations: int = 30) -> DelayDistribution:
        for level, _ in COHORT_LEVELS:
            values = self.cohort(case, history, level)
            if len(values) >= min_observations or (level == "global" and len(values)):
                return DelayDistribution(values, level, len(values))
        raise ValueError("no eligible strictly prior observations")


def empirical_tail_pool(
    center: np.ndarray, tail_source: np.ndarray, *, fraction: float = 0.20,
    min_center_tail: int = 100, min_source_tail: int = 100,
) -> np.ndarray:
    """Replace the sorted upper fraction with broader empirical tail quantiles."""
    center = np.asarray(center, dtype=float)
    source = np.asarray(tail_source, dtype=float)
    center_tail_count = int(np.floor(len(center) * fraction))
    source_tail_count = int(np.floor(len(source) * fraction))
    if center_tail_count >= min_center_tail or source_tail_count < min_source_tail or center_tail_count == 0:
        return center.copy()
    ordered = np.sort(center.copy())
    source_tail = np.sort(source)[-source_tail_count:]
    probabilities = (np.arange(center_tail_count) + 0.5) / center_tail_count
    ordered[-center_tail_count:] = np.quantile(source_tail, probabilities)
    return ordered


def candidate_distributions(
    store: LazyTemporalStore, case: ValidationCase,
) -> dict[str, DelayDistribution]:
    history_24 = TemporalHistory(case.flight_date, lookback_months=24, history_start=date(2023, 1, 1))
    baseline = store.select(case, history_24, min_observations=30)

    broad = store.cohort(case, history_24, "route_carrier_season")
    hierarchical = empirical_tail_pool(baseline.samples_minutes, broad)

    recent_history = TemporalHistory(case.flight_date, lookback_months=6, history_start=date(2023, 1, 1))
    recent = store.select(case, recent_history, min_observations=30)
    recent_augmented = empirical_tail_pool(
        baseline.samples_minutes, recent.samples_minutes, min_center_tail=10**12, min_source_tail=30
    )

    carrier_month_bucket = store.custom_cohort(
        case, history_24, name="carrier_month_bucket",
        where="reporting_carrier=$carrier AND month=$month AND departure_time_bucket=$bucket",
    )
    tail_aware = empirical_tail_pool(baseline.samples_minutes, carrier_month_bucket)

    return {
        "baseline": baseline,
        "hierarchical_tail_pooling": DelayDistribution(hierarchical, baseline.fallback_level, len(hierarchical)),
        "recent_tail_augmentation": DelayDistribution(recent_augmented, f"{baseline.fallback_level}+recent_{recent.fallback_level}", len(recent_augmented)),
        "tail_aware_carrier_month_bucket": DelayDistribution(tail_aware, baseline.fallback_level, len(tail_aware)),
    }


def benchmark_acceleration(database: Path, cases: list[WeightedCase]) -> dict:
    old_started = time.perf_counter()
    old_results = []
    for item in cases:
        history = TemporalHistory(item.case.flight_date, lookback_months=24, history_start=date(2023, 1, 1))
        cohorts = temporal_cohort_samples(database, case=item.case, history=history)
        for level, _ in COHORT_LEVELS:
            if len(cohorts[level]) >= 30 or (level == "global" and len(cohorts[level])):
                old_results.append((level, np.quantile(cohorts[level], (.5, .75, .9, .95))))
                break
    old_runtime = time.perf_counter() - old_started
    new_started = time.perf_counter()
    with LazyTemporalStore(database) as store:
        new_results = []
        for item in cases:
            selected = store.select(
                item.case, TemporalHistory(item.case.flight_date, lookback_months=24, history_start=date(2023, 1, 1))
            )
            new_results.append((selected.fallback_level, np.quantile(selected.samples_minutes, (.5, .75, .9, .95))))
        cache_bytes = sum(values.nbytes for values in store.cache.values())
    new_runtime = time.perf_counter() - new_started
    max_difference = max(float(np.max(np.abs(old[1] - new[1]))) for old, new in zip(old_results, new_results))
    levels_equal = all(old[0] == new[0] for old, new in zip(old_results, new_results))
    return {
        "sample_count": len(cases), "old_runtime_seconds": round(old_runtime, 4),
        "new_runtime_seconds": round(new_runtime, 4), "speedup": old_runtime / new_runtime,
        "cache_bytes": cache_bytes, "quantile_tolerance": 1e-12,
        "maximum_quantile_difference": max_difference, "cohort_levels_equal": levels_equal,
        "approximation_introduced": False,
    }


def _distribution_metrics(rows: list[dict]) -> dict:
    actual = np.asarray([row["actual"] for row in rows]); weights = np.asarray([row["weight"] for row in rows])
    result = {"sample_count": len(rows), "median_mae": weighted_mean(
        np.abs(actual - np.asarray([row["p50"] for row in rows])), weights
    )}
    for name, q in (("p50", .5), ("p75", .75), ("p90", .9), ("p95", .95)):
        predicted = np.asarray([row[name] for row in rows])
        coverage = weighted_mean((actual <= predicted).astype(float), weights)
        result[f"{name}_coverage"] = coverage
        result[f"{name}_coverage_absolute_deviation"] = abs(coverage - q)
        result[f"{name}_pinball_loss"] = weighted_pinball(actual, predicted, weights, q)
    result["mean_crps"] = weighted_mean(np.asarray([row["crps"] for row in rows]), weights)
    result["severe_delay_frequency"] = weighted_mean((actual > 30).astype(float), weights)
    return result


def cluster_bootstrap_interval(
    rows: list[dict], metric, *, seed: int, replicates: int = 300,
) -> dict:
    clusters = defaultdict(list)
    for index, row in enumerate(rows):
        clusters[(row["route"], row["prediction_date"][:7])].append(index)
    cluster_values = list(clusters.values())
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(replicates):
        chosen = rng.integers(0, len(cluster_values), size=len(cluster_values))
        indices = np.asarray([index for position in chosen for index in cluster_values[position]], dtype=int)
        estimates.append(metric(indices))
    return {
        "replicates": replicates, "cluster_unit": "directional_route_by_year_month",
        "lower": float(np.quantile(estimates, .025)), "upper": float(np.quantile(estimates, .975)),
    }


def _arrival_rows(store: LazyTemporalStore, cases: list[WeightedCase]) -> dict[str, list[dict]]:
    output = {candidate: [] for candidate in CANDIDATES}
    for item in cases:
        distributions = candidate_distributions(store, item.case)
        recent = store.select(
            item.case, TemporalHistory(item.case.flight_date, lookback_months=6, history_start=date(2023, 1, 1))
        )
        recent_severe_frequency = float(np.mean(recent.samples_minutes > 30))
        for candidate, distribution in distributions.items():
            q50, q75, q90, q95 = np.quantile(distribution.samples_minutes, (.5, .75, .9, .95))
            output[candidate].append({
                "actual": item.case.realized_delay, "p50": float(q50), "p75": float(q75),
                "p90": float(q90), "p95": float(q95),
                "crps": empirical_crps(distribution.samples_minutes, item.case.realized_delay),
                "weight": item.weight, "carrier": item.case.carrier,
                "route": f"{item.case.origin}-{item.case.destination}",
                "prediction_date": str(item.case.flight_date), "month": item.case.flight_date.month,
                "season": item.season, "time_bucket": item.time_bucket,
                "route_volume_group": item.route_volume_group,
                "cohort_level": distribution.fallback_level,
                "cohort_sample_size": distribution.observation_count,
                "cohort_size_bucket": (
                    "30_99" if distribution.observation_count < 100 else
                    "100_499" if distribution.observation_count < 500 else "500_plus"
                ),
                "recent_severe_frequency_bucket": (
                    "under_10_percent" if recent_severe_frequency < .10 else
                    "10_20_percent" if recent_severe_frequency < .20 else "over_20_percent"
                ),
            })
    return output


def evaluate_arrivals(store: LazyTemporalStore, cases: list[WeightedCase], year: int, bootstrap_replicates: int) -> dict:
    candidate_rows = _arrival_rows(store, cases)
    output = {}
    for candidate, rows in candidate_rows.items():
        metrics = _distribution_metrics(rows)
        actual = np.asarray([r["actual"] for r in rows]); p90 = np.asarray([r["p90"] for r in rows]);
        weights = np.asarray([r["weight"] for r in rows]); crps = np.asarray([r["crps"] for r in rows])
        metrics["p90_coverage_ci"] = cluster_bootstrap_interval(
            rows, lambda idx: weighted_mean((actual[idx] <= p90[idx]).astype(float), weights[idx]),
            seed=SEED + year, replicates=bootstrap_replicates,
        )
        metrics["crps_ci"] = cluster_bootstrap_interval(
            rows, lambda idx: weighted_mean(crps[idx], weights[idx]),
            seed=SEED + year + 1, replicates=bootstrap_replicates,
        )
        subgroups = {}
        for dimension in ("carrier", "month", "season", "time_bucket", "route_volume_group", "cohort_level",
                          "cohort_size_bucket", "recent_severe_frequency_bucket"):
            grouped = defaultdict(list)
            for row in rows:
                grouped[str(row[dimension])].append(row)
            subgroups[dimension] = {key: _distribution_metrics(value) for key, value in grouped.items()}
        metrics["subgroups"] = subgroups
        output[candidate] = metrics
    return output


def _schedule(store: LazyTemporalStore, case: ValidationCase, seed: int) -> tuple[int, str]:
    row = store.connection.execute("""
        SELECT crs_departure_minutes-$arrival, destination FROM flight_records
        WHERE flight_date=$date AND origin=$airport
          AND crs_departure_minutes-$arrival BETWEEN 45 AND 180
        ORDER BY hash(destination, flight_number, crs_departure_minutes, $seed) LIMIT 1
    """, {"arrival": case.arrival_minutes, "date": case.flight_date,
          "airport": case.destination, "seed": seed}).fetchone()
    return (int(row[0]), str(row[1])) if row else (90, "UNKNOWN")


def evaluate_connections(
    store: LazyTemporalStore, cases: list[WeightedCase], year: int, simulations: int,
    bootstrap_replicates: int,
) -> dict:
    transfer = TransferTimeAssumption(); rng = np.random.default_rng(SEED + year)
    rows = {candidate: [] for candidate in CANDIDATES}
    for index, item in enumerate(cases):
        distributions = candidate_distributions(store, item.case)
        layover, final_destination = _schedule(store, item.case, SEED + index)
        transfer_minutes = rng.triangular(transfer.minimum, transfer.mode, transfer.maximum)
        outcome = float(item.status == "completed" and item.case.realized_delay + transfer_minutes <= layover - 15)
        for candidate, distribution in distributions.items():
            probability = simulate_connection(
                distribution.samples_minutes, layover_minutes=layover, simulations=simulations,
                transfer=transfer, seed=SEED + index,
            ).probability
            rows[candidate].append({
                "probability": probability, "outcome": outcome, "weight": item.weight,
                "route": f"{item.case.origin}-{item.case.destination}",
                "prediction_date": str(item.case.flight_date), "carrier": item.case.carrier,
                "connection": item.case.destination, "destination": final_destination,
                "layover_minutes": layover, "time_bucket": item.time_bucket,
                "season": item.season, "status": item.status,
                "layover_bucket": (
                    "short_45_59" if layover < 60 else "medium_60_119" if layover < 120 else "long_120_180"
                ),
            })
    output = {}
    for candidate, candidate_rows in rows.items():
        p = np.asarray([r["probability"] for r in candidate_rows]); y = np.asarray([r["outcome"] for r in candidate_rows]); w = np.asarray([r["weight"] for r in candidate_rows])
        bins = weighted_calibration_bins(p, y, w)
        metrics = {
            "sample_count": len(candidate_rows), "mean_predicted_probability": weighted_mean(p, w),
            "observed_success_frequency": weighted_mean(y, w), "brier_score": weighted_brier(p, y, w),
            "ece": weighted_ece(bins), "mean_calibration_gap": weighted_mean(p-y, w), "bins": bins,
        }
        subgroups = {}
        for dimension in ("carrier", "layover_bucket", "time_bucket", "season", "status"):
            grouped = defaultdict(list)
            for row in candidate_rows:
                grouped[str(row[dimension])].append(row)
            subgroups[dimension] = {}
            for key, values in grouped.items():
                p_g = np.asarray([row["probability"] for row in values]); y_g = np.asarray([row["outcome"] for row in values]); w_g = np.asarray([row["weight"] for row in values])
                bins_g = weighted_calibration_bins(p_g, y_g, w_g)
                subgroups[dimension][key] = {
                    "sample_count": len(values), "brier_score": weighted_brier(p_g, y_g, w_g),
                    "ece": weighted_ece(bins_g), "mean_calibration_gap": weighted_mean(p_g-y_g, w_g),
                }
        metrics["subgroups"] = subgroups
        for name, function, offset in (
            ("brier_score_ci", lambda idx: weighted_brier(p[idx], y[idx], w[idx]), 1),
            ("ece_ci", lambda idx: weighted_ece(weighted_calibration_bins(p[idx], y[idx], w[idx])), 2),
            ("mean_calibration_gap_ci", lambda idx: weighted_mean(p[idx]-y[idx], w[idx]), 3),
        ):
            metrics[name] = cluster_bootstrap_interval(
                candidate_rows, function, seed=SEED + year + offset, replicates=bootstrap_replicates
            )
        output[candidate] = metrics
    return output


def run_experiment(
    database: Path, *, arrival_cases: int = 2_000, connection_cases: int = 1_000,
    simulations: int = 2_000, bootstrap_replicates: int = 300,
) -> dict:
    started = time.perf_counter()
    benchmark_cases = stratified_cases(database, test_year=2025, target=30, seed=SEED)
    report = {"benchmark": benchmark_acceleration(database, benchmark_cases), "holdouts": {}}
    for year in (2024, 2025):
        arrivals = stratified_cases(database, test_year=year, target=arrival_cases, seed=SEED)
        connections = stratified_cases(
            database, test_year=year, target=connection_cases, seed=SEED + 100, include_statuses=True
        )
        with LazyTemporalStore(database) as store:
            report["holdouts"][str(year)] = {
                "arrival_sample_size": len(arrivals), "connection_sample_size": len(connections),
                "arrival": evaluate_arrivals(store, arrivals, year, bootstrap_replicates),
                "connection": evaluate_connections(store, connections, year, simulations, bootstrap_replicates),
                "cache_bytes": sum(values.nbytes for values in store.cache.values()),
            }
    report["runtime_seconds"] = round(time.perf_counter() - started, 2)
    report["production_estimator_changed"] = False
    report["public_api_changed"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--arrival-cases", type=int, default=2_000)
    parser.add_argument("--connection-cases", type=int, default=1_000)
    parser.add_argument("--simulations", type=int, default=2_000)
    parser.add_argument("--bootstrap-replicates", type=int, default=300)
    parser.add_argument("--output", type=Path, default=Path("data/processed/upper_tail_experiments.json"))
    args = parser.parse_args()
    report = run_experiment(
        args.database, arrival_cases=args.arrival_cases, connection_cases=args.connection_cases,
        simulations=args.simulations, bootstrap_replicates=args.bootstrap_replicates,
    )
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"runtime_seconds": report["runtime_seconds"], "benchmark": report["benchmark"]}, indent=2))


if __name__ == "__main__":
    main()
