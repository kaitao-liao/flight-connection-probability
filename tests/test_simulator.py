import numpy as np
import pytest
from backend.flight_connection.simulator import (
    DEFAULT_BOARDING_CUTOFF_MINUTES,
    DEFAULT_DEPLANING_MINUTES,
    TransferTimeAssumption,
    simulate_connection,
)


def test_deterministic_transfer_gives_exact_result():
    result = simulate_connection(np.array([0.0]), layover_minutes=30, boarding_cutoff_minutes=10,
                                 deplaning_minutes=0,
                                 transfer=TransferTimeAssumption(20, 20, 20), simulations=100, seed=7)
    assert result.probability == 1.0
    assert result.scenario_probabilities["+15 min"] == 0.0


def test_seed_is_reproducible():
    kwargs = dict(layover_minutes=60, simulations=500, seed=42)
    assert simulate_connection(np.array([0, 30]), **kwargs) == simulate_connection(np.array([0, 30]), **kwargs)


def test_invalid_transfer_assumption():
    with pytest.raises(ValueError):
        simulate_connection(np.array([0]), layover_minutes=30, transfer=TransferTimeAssumption(20, 10, 30))


def test_revised_v1_passenger_time_assumptions_are_explicit():
    transfer = TransferTimeAssumption()
    assert DEFAULT_DEPLANING_MINUTES == 20
    assert (transfer.minimum, transfer.mode, transfer.maximum) == (15, 25, 40)
    assert DEFAULT_BOARDING_CUTOFF_MINUTES == 15


def test_minimum_passenger_requirement_is_50_minutes():
    fixed_transfer = TransferTimeAssumption(15, 15, 15)
    impossible = simulate_connection(
        np.array([0.0]), layover_minutes=49, transfer=fixed_transfer,
        simulations=100, seed=7,
    )
    just_possible = simulate_connection(
        np.array([0.0]), layover_minutes=50, transfer=fixed_transfer,
        simulations=100, seed=7,
    )
    assert impossible.probability == 0
    assert impossible.scenario_probabilities["on time"] == 0
    assert just_possible.probability == 1
    assert just_possible.scenario_probabilities["on time"] == 1


def test_fixed_delay_scenarios_include_deplaning_and_cutoff():
    result = simulate_connection(
        np.array([0.0]), layover_minutes=85, simulations=100,
        transfer=TransferTimeAssumption(25, 25, 25), seed=17,
    )
    assert result.scenario_probabilities["on time"] == 1
    assert result.scenario_probabilities["+15 min"] == 1
    assert result.scenario_probabilities["+30 min"] == 0
    assert result.scenario_probabilities["+45 min"] == 0
