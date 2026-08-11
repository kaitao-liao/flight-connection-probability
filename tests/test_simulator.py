import numpy as np
import pytest
from backend.flight_connection.simulator import TransferTimeAssumption, simulate_connection


def test_deterministic_transfer_gives_exact_result():
    result = simulate_connection(np.array([0.0]), layover_minutes=30, boarding_cutoff_minutes=10,
                                 transfer=TransferTimeAssumption(20, 20, 20), simulations=100, seed=7)
    assert result.probability == 1.0
    assert result.scenario_probabilities["+15 min"] == 0.0


def test_seed_is_reproducible():
    kwargs = dict(layover_minutes=60, simulations=500, seed=42)
    assert simulate_connection(np.array([0, 30]), **kwargs) == simulate_connection(np.array([0, 30]), **kwargs)


def test_invalid_transfer_assumption():
    with pytest.raises(ValueError):
        simulate_connection(np.array([0]), layover_minutes=30, transfer=TransferTimeAssumption(20, 10, 30))

