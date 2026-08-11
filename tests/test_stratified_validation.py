from pathlib import Path

import numpy as np

from backend.flight_connection.stratified_validation import (
    bootstrap_interval, extract_error_cases, stratified_cases, weighted_brier,
    weighted_calibration_bins, weighted_ece, weighted_mean,
)


def test_weighted_metrics():
    values = np.array([0.0, 1.0])
    weights = np.array([3.0, 1.0])
    assert weighted_mean(values, weights) == 0.25
    assert weighted_brier(np.array([0.0, 0.0]), values, weights) == 0.25
    bins = weighted_calibration_bins(np.array([0.05, 0.95]), values, weights)
    assert bins[0]["population_weight"] == 3.0
    assert weighted_ece(bins) >= 0


def test_bootstrap_interval_is_deterministic():
    values = np.array([0.0, 1.0, 2.0])
    metric = lambda indices: float(np.mean(values[indices]))
    first = bootstrap_interval(3, metric, seed=7, replicates=50)
    second = bootstrap_interval(3, metric, seed=7, replicates=50)
    assert first == second
    assert first["lower"] <= 1.0 <= first["upper"]


def test_error_case_extraction_orders_overprediction():
    rows = [
        {"combined_probability": 0.7, "outcome": 0.0, "id": 1},
        {"combined_probability": 0.9, "outcome": 0.0, "id": 2},
        {"combined_probability": 0.1, "outcome": 1.0, "id": 3},
    ]
    assert extract_error_cases(rows, probability_key="combined_probability", outcome=0, limit=1)[0]["id"] == 2
    assert extract_error_cases(rows, probability_key="combined_probability", outcome=1, limit=1)[0]["id"] == 3


def test_stratified_sampling_is_deterministic(development_database: Path):
    first = stratified_cases(development_database, test_year=2024, target=10, seed=11)
    second = stratified_cases(development_database, test_year=2024, target=10, seed=11)
    assert first == second
    assert len(first) == 10
    assert all(case.route_volume_group in {"low_lt_100", "medium_100_999", "high_1000_plus"} for case in first)
    assert all(case.weight > 0 for case in first)
