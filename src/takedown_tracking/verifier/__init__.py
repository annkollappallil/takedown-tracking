"""Independent verification (Half 2): checks whether reported content is really gone."""

from takedown_tracking.verifier.checker import (
    AvailabilityChecker,
    CheckResult,
    CheckState,
    ReuploadDetector,
)
from takedown_tracking.verifier.runner import RunnerConfig, VerificationRunner
from takedown_tracking.verifier.simulator import (
    DEFAULT_PROFILES,
    GroundTruth,
    IntegrityProfile,
    SimulatedChecker,
    SimulatedReuploadDetector,
)


def simulate_observations(records, as_of, seed: int = 42, config: RunnerConfig = RunnerConfig()):
    """Convenience: run the simulated verifier over synthetic records."""
    checker = SimulatedChecker(seed=seed)
    runner = VerificationRunner(checker, SimulatedReuploadDetector(checker), config)
    return runner.run(records, as_of)


__all__ = [
    "AvailabilityChecker",
    "CheckResult",
    "CheckState",
    "ReuploadDetector",
    "RunnerConfig",
    "VerificationRunner",
    "DEFAULT_PROFILES",
    "GroundTruth",
    "IntegrityProfile",
    "SimulatedChecker",
    "SimulatedReuploadDetector",
    "simulate_observations",
]
