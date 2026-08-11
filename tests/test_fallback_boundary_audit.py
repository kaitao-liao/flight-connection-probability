import numpy as np

from backend.flight_connection.delay_model import COHORT_LEVELS
from scripts.fallback_boundary_audit import blended_distribution, summarize_boundary


def empty_cohorts() -> dict[str, np.ndarray]:
    return {name: np.array([], dtype=float) for name, _ in COHORT_LEVELS}


def test_research_blend_uses_fixed_n_over_60_weight():
    cohorts = empty_cohorts()
    cohorts["exact"] = np.arange(30, dtype=float)
    cohorts["route_carrier_month_bucket"] = np.arange(100, 200, dtype=float)
    result = blended_distribution(cohorts, size=2_000)
    assert result.fallback_level == "blend_exact_0.500_route_carrier_month_bucket"
    assert result.observation_count == 2_000
    assert np.sum(result.samples_minutes < 100) == 1_000


def test_boundary_summary_separates_below_threshold_displacement():
    rows = [
        {"specific_n": 29, "production_displacement_pp": 12.0,
         "boundary_contrast_pp": 12.0, "carrier": "AA"},
        {"specific_n": 30, "production_displacement_pp": 0.0,
         "boundary_contrast_pp": 8.0, "carrier": "DL"},
    ]
    summary = summarize_boundary(rows)
    assert summary["case_count"] == 2
    assert summary["below_30_displacement_pp"]["case_count"] == 1
    assert summary["production_displacement_pp"]["at_least_10pp"] == 1
