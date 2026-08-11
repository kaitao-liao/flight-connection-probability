from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from backend.flight_connection.upper_tail_experiments import (
    LazyTemporalStore, benchmark_acceleration, candidate_distributions,
    cluster_bootstrap_interval, empirical_tail_pool,
)
from backend.flight_connection.validation import TemporalHistory, ValidationCase
from backend.flight_connection.stratified_validation import WeightedCase


def cache_database(tmp_path: Path) -> Path:
    database = tmp_path / "cache.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        rows = []
        for index in range(120):
            flight_date = date(2023, 1, 1) + timedelta(days=index * 3)
            rows.append((flight_date, flight_date.year, flight_date.month, flight_date.isoweekday(),
                         "DL", "ATL", "JFK", 930, 1065, "afternoon", float(index), "test.zip"))
        connection.executemany("INSERT INTO historical_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return database


def test_lazy_cache_is_temporally_bounded_and_matches_old_path(tmp_path: Path):
    database = cache_database(tmp_path)
    case = ValidationCase(date(2024, 1, 1), "DL", "ATL", "JFK", 930, 1065, 0)
    weighted = WeightedCase(case, 1.0, "medium_100_999", "winter", "afternoon", "early_or_on_time")
    benchmark = benchmark_acceleration(database, [weighted])
    assert benchmark["cohort_levels_equal"] is True
    assert benchmark["maximum_quantile_difference"] <= benchmark["quantile_tolerance"]
    assert benchmark["approximation_introduced"] is False

    with LazyTemporalStore(database) as store:
        earlier = store.cohort(case, TemporalHistory(case.flight_date, history_end=date(2023, 2, 1)), "global")
        later = store.cohort(case, TemporalHistory(case.flight_date, history_end=date(2023, 3, 1)), "global")
        assert len(earlier) < len(later)
        assert len(store.cache) == 2


def test_tail_pooling_is_empirical_and_does_not_mutate_inputs():
    center = np.arange(50, dtype=float)
    broad = np.arange(200, dtype=float)
    original = center.copy()
    pooled = empirical_tail_pool(center, broad, min_center_tail=100, min_source_tail=20)
    assert np.array_equal(center, original)
    assert np.median(pooled) == np.median(center)
    assert np.quantile(pooled, .9) > np.quantile(center, .9)


def test_tail_pooling_falls_back_when_broader_support_is_insufficient():
    center = np.arange(50, dtype=float)
    assert np.array_equal(
        empirical_tail_pool(center, np.arange(10), min_center_tail=100, min_source_tail=20), center
    )


def test_candidate_methods_are_separate_from_baseline(tmp_path: Path):
    database = cache_database(tmp_path)
    case = ValidationCase(date(2024, 1, 1), "DL", "ATL", "JFK", 930, 1065, 0)
    with LazyTemporalStore(database) as store:
        candidates = candidate_distributions(store, case)
    assert set(candidates) == {
        "baseline", "hierarchical_tail_pooling", "recent_tail_augmentation",
        "tail_aware_carrier_month_bucket",
    }
    assert candidates["baseline"].fallback_level == "route_carrier"


def test_cluster_bootstrap_is_deterministic():
    rows = [
        {"route": "ATL-JFK", "prediction_date": "2024-01-01", "value": 1.0},
        {"route": "ATL-JFK", "prediction_date": "2024-01-02", "value": 2.0},
        {"route": "DFW-LAX", "prediction_date": "2024-02-01", "value": 3.0},
    ]
    metric = lambda indices: float(np.mean([rows[index]["value"] for index in indices]))
    first = cluster_bootstrap_interval(rows, metric, seed=9, replicates=50)
    second = cluster_bootstrap_interval(rows, metric, seed=9, replicates=50)
    assert first == second
    assert first["cluster_unit"] == "directional_route_by_year_month"
