from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pytest

from backend.flight_connection.delay_model import historical_delay_distribution

from backend.flight_connection.validation import (
    TemporalHistory, ValidationCase, brier_score, calibration_bins,
    deterministic_cases, empirical_crps, expected_calibration_error,
    quantile_pinball_loss, select_temporal_distribution, status_probabilities,
    subtract_months, temporal_cohort_samples,
)


def temporal_database(tmp_path: Path) -> Path:
    database = tmp_path / "temporal.duckdb"
    schema = Path("backend/flight_connection/schema.sql").read_text()
    with duckdb.connect(str(database)) as connection:
        connection.execute(schema)
        for flight_date, delay, number in (
            ("2023-12-30", 5.0, "1"), ("2024-01-09", 999.0, "2"),
        ):
            connection.execute("""
                INSERT INTO historical_flights VALUES (?, year(?::DATE), month(?::DATE),
                    isodow(?::DATE), 'DL', 'ATL', 'JFK', 930, 1065, 'afternoon', ?, 'test.zip')
            """, [flight_date, flight_date, flight_date, flight_date, delay])
            connection.execute("""
                INSERT INTO flight_records VALUES (?, year(?::DATE), month(?::DATE), isodow(?::DATE),
                    'DL', ?, 'ATL', 'JFK', 930, 1065, 'afternoon', 0, ?, false, false,
                    'completed', NULL, NULL, NULL, NULL, NULL, false, 'test.zip')
            """, [flight_date, flight_date, flight_date, flight_date, number, delay])
        connection.execute("""
            INSERT INTO flight_records VALUES ('2023-12-29', 2023, 12, 5, 'DL', '3', 'ATL', 'JFK',
                930, 1065, 'afternoon', NULL, NULL, true, false, 'cancelled',
                NULL, NULL, NULL, NULL, NULL, false, 'test.zip')
        """)
    return database


def test_strict_temporal_cutoff_prevents_future_leakage(tmp_path):
    database = temporal_database(tmp_path)
    case = ValidationCase(date(2024, 1, 8), "DL", "ATL", "JFK", 930, 1065, 0)
    cohorts = temporal_cohort_samples(database, case=case, history=TemporalHistory(case.flight_date))
    assert cohorts["global"].tolist() == [5.0]
    assert 999.0 not in cohorts["global"]


def test_history_end_after_prediction_is_rejected(tmp_path):
    database = temporal_database(tmp_path)
    case = ValidationCase(date(2024, 1, 8), "DL", "ATL", "JFK", 930, 1065, 0)
    with pytest.raises(ValueError, match="later than the prediction"):
        temporal_cohort_samples(
            database, case=case,
            history=TemporalHistory(case.flight_date, history_end=date(2024, 1, 9)),
        )


def test_rolling_window_selection():
    assert subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)
    history = TemporalHistory(date(2025, 1, 15), lookback_months=6, history_start=date(2023, 1, 1))
    assert history.bounds() == (date(2024, 7, 15), date(2025, 1, 15))


def test_quantile_and_crps_metrics():
    actual = np.array([0.0, 2.0])
    predicted = np.array([1.0, 1.0])
    assert quantile_pinball_loss(actual, predicted, 0.5) == 0.5
    assert empirical_crps(np.array([0.0]), 2.0) == 2.0


def test_calibration_bins_and_brier_score():
    probabilities = np.array([0.05, 0.15, 0.95, 1.0])
    outcomes = np.array([0.0, 1.0, 1.0, 1.0])
    bins = calibration_bins(probabilities, outcomes)
    assert [row["count"] for row in bins] == [1, 1, 2]
    assert bins[-1]["bin_start"] == 0.9
    assert brier_score(np.array([0.0, 1.0]), np.array([0.0, 1.0])) == 0.0
    assert 0 <= expected_calibration_error(bins) <= 1


def test_status_probabilities_use_prior_records_only(tmp_path):
    database = temporal_database(tmp_path)
    case = ValidationCase(date(2024, 1, 8), "DL", "ATL", "JFK", 930, 1065, 0)
    result = status_probabilities(
        database, case=case, history=TemporalHistory(case.flight_date), min_observations=1
    )
    assert result["sample_size"] == 2
    assert result["completion_probability"] == 0.5
    assert result["cancellation_probability"] == 0.5
    assert result["diversion_probability"] == 0.0


def test_threshold_selection_and_deterministic_sampling(tmp_path):
    cohorts = {name: np.array([]) for name in (
        "exact", "route_carrier_month_bucket", "route_carrier_season",
        "route_carrier", "route", "carrier", "global",
    )}
    cohorts["exact"] = np.arange(10)
    cohorts["route_carrier"] = np.arange(40)
    cohorts["global"] = np.arange(100)
    assert select_temporal_distribution(cohorts, min_observations=30).fallback_level == "route_carrier"

    database = temporal_database(tmp_path)
    assert deterministic_cases(database, test_year=2024, limit=1, seed=7) == \
           deterministic_cases(database, test_year=2024, limit=1, seed=7)


def test_serving_and_validation_use_identical_24_month_cohorts(tmp_path):
    database = temporal_database(tmp_path)
    case = ValidationCase(date(2024, 1, 8), "DL", "ATL", "JFK", 930, 1065, 0)
    validation_cohorts = temporal_cohort_samples(
        database,
        case=case,
        history=TemporalHistory(case.flight_date, lookback_months=24),
    )
    validated = select_temporal_distribution(validation_cohorts, min_observations=1)
    served = historical_delay_distribution(
        database,
        carrier=case.carrier,
        origin=case.origin,
        destination=case.destination,
        travel_date=case.flight_date,
        scheduled_departure_minutes=case.departure_minutes,
        min_observations=1,
    )
    assert served.fallback_level == validated.fallback_level
    assert served.observation_count == validated.observation_count
    assert served.quantiles() == validated.quantiles()
    assert served.samples_minutes.tolist() == validated.samples_minutes.tolist()
