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


REUPLOAD_DETECTORS = ("simulated", "pdq")


def simulate_observations(
    records,
    as_of,
    seed: int = 42,
    config: RunnerConfig = RunnerConfig(),
    reupload_detector: str = "simulated",
):
    """Convenience: run the simulated verifier over synthetic records.

    reupload_detector="simulated" reads re-uploads straight from the hidden ground truth.
    reupload_detector="pdq" finds them by PDQ hash matching on harmless generated images,
    so missed copies and false matches show up as they would in practice.
    """
    records = list(records)
    checker = SimulatedChecker(seed=seed)
    if reupload_detector == "pdq":
        from takedown_tracking.verifier.pdq_detector import simulated_pdq_detector

        detector, _ = simulated_pdq_detector(checker, records)
    elif reupload_detector == "simulated":
        detector = SimulatedReuploadDetector(checker)
    else:
        raise ValueError(f"reupload_detector must be one of {REUPLOAD_DETECTORS}")
    runner = VerificationRunner(checker, detector, config)
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
    "REUPLOAD_DETECTORS",
]
