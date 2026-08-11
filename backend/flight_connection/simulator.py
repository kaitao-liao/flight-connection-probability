"""Monte Carlo connection simulator. Transfer times are assumptions, not observations."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class TransferTimeAssumption:
    """Triangular minutes from aircraft arrival to the next gate."""
    minimum: float = 10.0
    mode: float = 20.0
    maximum: float = 35.0

    def validate(self) -> None:
        if not (0 <= self.minimum <= self.mode <= self.maximum):
            raise ValueError("transfer minutes must satisfy 0 <= minimum <= mode <= maximum")


@dataclass(frozen=True)
class SimulationResult:
    probability: float
    layover_minutes: float
    simulations: int
    transfer_quantiles: dict[str, float]
    scenario_probabilities: dict[str, float]


def simulate_connection(
    delay_samples: np.ndarray, *, layover_minutes: float, simulations: int = 20_000,
    boarding_cutoff_minutes: float = 15.0, transfer: TransferTimeAssumption = TransferTimeAssumption(),
    seed: int | None = None,
) -> SimulationResult:
    if len(delay_samples) == 0 or simulations <= 0 or layover_minutes < 0 or boarding_cutoff_minutes < 0:
        raise ValueError("nonempty delays, positive simulations, and nonnegative times are required")
    transfer.validate()
    rng = np.random.default_rng(seed)
    sampled_delays = rng.choice(np.asarray(delay_samples, dtype=float), simulations, replace=True)
    if transfer.minimum == transfer.maximum:
        transfer_times = np.full(simulations, transfer.minimum, dtype=float)
    else:
        transfer_times = rng.triangular(transfer.minimum, transfer.mode, transfer.maximum, simulations)
    deadline = layover_minutes - boarding_cutoff_minutes
    success = sampled_delays + transfer_times <= deadline
    scenarios = {
        f"+{delay} min" if delay else "on time": float(np.mean(delay + transfer_times <= deadline))
        for delay in (0, 15, 30, 45)
    }
    q = np.quantile(transfer_times, [0.1, 0.5, 0.9])
    return SimulationResult(float(np.mean(success)), layover_minutes, simulations,
                            dict(zip(("p10", "p50", "p90"), map(float, q))), scenarios)
