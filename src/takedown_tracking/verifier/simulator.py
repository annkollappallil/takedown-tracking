"""Simulated verifier for demos and tests. Never touches the network.

For each synthetic takedown request the simulator decides a hidden "ground truth":
when the content really disappeared, whether a copy came back, and whether the
content can be checked at all. Checks then report that truth, with occasional
transient failures, so the runner and the integrity metrics can be tested against
known answers.

Scenarios it produces:
- honest claims: content removed just before the platform reports it
- false claims: platform reports removal early; content stays up for hours, or never goes
- silent removals: platform removes content but never reports it
- re-uploads: a copy of removed content appears later
- unverifiable content: login walls, private accounts, geo-blocking, persistent timeouts
- flaky checks: individual checks fail at random

Everything is deterministic for a given seed and request_id, independent of the
order in which requests or checks are processed.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from takedown_tracking.models.observation import CheckError
from takedown_tracking.models.request import Platform, RequestRecord, RequestStatus, RequestType
from takedown_tracking.verifier.checker import CheckResult, CheckState


@dataclass(frozen=True)
class IntegrityProfile:
    """How a simulated platform behaves when it comes to honesty and re-uploads."""

    false_claim_rate: float = 0.05          # share of removal claims made while content is still up
    never_removed_after_false_claim: float = 0.3  # of those, share that never actually go down
    silent_removal_rate: float = 0.15       # share of unclaimed requests removed without a report
    reupload_rate: float = 0.08             # share of removed items that are re-uploaded later
    unverifiable_rate: float = 0.04         # share of items the verifier can never see
    flaky_check_rate: float = 0.03          # share of individual checks that fail transiently


# Fictional platforms only. Spread is deliberate so the demo shows contrast.
DEFAULT_PROFILES: Dict[Platform, IntegrityProfile] = {
    Platform.PLATFORM_A: IntegrityProfile(0.01, 0.2, 0.10, 0.03, 0.02, 0.02),
    Platform.PLATFORM_B: IntegrityProfile(0.03, 0.3, 0.15, 0.06, 0.03, 0.03),
    Platform.PLATFORM_C: IntegrityProfile(0.12, 0.4, 0.05, 0.15, 0.05, 0.04),
    Platform.PLATFORM_D: IntegrityProfile(0.05, 0.3, 0.20, 0.08, 0.03, 0.03),
    Platform.PLATFORM_E: IntegrityProfile(0.20, 0.5, 0.05, 0.25, 0.08, 0.05),
    Platform.PLATFORM_F: IntegrityProfile(0.04, 0.3, 0.12, 0.05, 0.10, 0.03),
    Platform.PLATFORM_G: IntegrityProfile(0.08, 0.3, 0.10, 0.10, 0.04, 0.03),
    Platform.PLATFORM_H: IntegrityProfile(0.02, 0.2, 0.25, 0.04, 0.02, 0.02),
    Platform.PLATFORM_I: IntegrityProfile(0.15, 0.4, 0.08, 0.20, 0.15, 0.06),
    Platform.PLATFORM_J: IntegrityProfile(0.06, 0.3, 0.10, 0.12, 0.05, 0.04),
}

UNVERIFIABLE_ERRORS = (
    (CheckError.LOGIN_WALL, 0.4),
    (CheckError.PRIVATE_OR_RESTRICTED, 0.3),
    (CheckError.GEO_BLOCKED, 0.1),
    (CheckError.TIMEOUT, 0.2),
)
TRANSIENT_ERRORS = (CheckError.TIMEOUT, CheckError.SERVER_ERROR, CheckError.RATE_LIMITED)


@dataclass(frozen=True)
class GroundTruth:
    """Hidden facts about one request. Only the simulator and tests may look at this."""

    removed_at: Optional[datetime]
    reupload_at: Optional[datetime]
    unverifiable_error: Optional[CheckError]
    scenario: str


def _rng(*parts: object) -> random.Random:
    """Order-independent RNG: Python's hash() is salted per process, so use SHA-256."""
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _lognormal_hours(rng: random.Random, median: float, sigma: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, rng.lognormvariate(math.log(median), sigma)))


class SimulatedChecker:
    """AvailabilityChecker that reports simulated ground truth."""

    agent_id = "verifier-simulator-v1"

    def __init__(
        self,
        seed: int = 42,
        profiles: Optional[Dict[Platform, IntegrityProfile]] = None,
        default_profile: IntegrityProfile = IntegrityProfile(),
    ) -> None:
        self.seed = seed
        self.profiles = profiles if profiles is not None else DEFAULT_PROFILES
        self.default_profile = default_profile
        self._truth: Dict[str, GroundTruth] = {}

    def profile_for(self, record: RequestRecord) -> IntegrityProfile:
        return self.profiles.get(record.platform, self.default_profile)

    def ground_truth(self, record: RequestRecord) -> GroundTruth:
        cached = self._truth.get(record.request_id)
        if cached is None:
            cached = self._decide_truth(record)
            self._truth[record.request_id] = cached
        return cached

    def _decide_truth(self, record: RequestRecord) -> GroundTruth:
        if record.request_type != RequestType.TAKEDOWN:
            return GroundTruth(None, None, None, "not_a_takedown")

        p = self.profile_for(record)
        rng = _rng(self.seed, "truth", record.request_id)

        unverifiable: Optional[CheckError] = None
        if rng.random() < p.unverifiable_rate:
            errors, weights = zip(*UNVERIFIABLE_ERRORS)
            unverifiable = rng.choices(errors, weights=weights, k=1)[0]

        removed_at: Optional[datetime] = None
        claim = record.claimed_removal_at
        if record.status == RequestStatus.CLAIMED_REMOVED and claim is not None:
            if rng.random() < p.false_claim_rate:
                if rng.random() < p.never_removed_after_false_claim:
                    scenario = "false_claim_never_removed"
                else:
                    scenario = "false_claim_removed_late"
                    removed_at = claim + timedelta(hours=_lognormal_hours(rng, 12.0, 0.8, 1.0, 240.0))
            else:
                scenario = "honest_claim"
                # Platforms usually log the removal a few minutes after it happens.
                removed_at = max(record.sent_at, claim - timedelta(minutes=rng.uniform(0.0, 10.0)))
        elif record.status in (RequestStatus.PENDING, RequestStatus.ACKNOWLEDGED, RequestStatus.EXPIRED):
            if rng.random() < p.silent_removal_rate:
                scenario = "silent_removal"
                removed_at = record.sent_at + timedelta(hours=_lognormal_hours(rng, 20.0, 0.9, 0.5, 400.0))
            else:
                scenario = "never_removed"
        else:
            scenario = "rejected_stays_up" if record.status == RequestStatus.REJECTED else "other"

        reupload_at: Optional[datetime] = None
        if removed_at is not None and rng.random() < p.reupload_rate:
            # Spread over two weeks so some fall outside the 7-day counting window.
            reupload_at = removed_at + timedelta(hours=rng.uniform(2.0, 14 * 24.0))

        return GroundTruth(removed_at, reupload_at, unverifiable, scenario)

    def check(self, record: RequestRecord, at: datetime) -> CheckResult:
        truth = self.ground_truth(record)
        if truth.unverifiable_error is not None:
            return CheckResult(CheckState.UNKNOWN, truth.unverifiable_error)

        rng = _rng(self.seed, "check", record.request_id, at.isoformat())
        if rng.random() < self.profile_for(record).flaky_check_rate:
            return CheckResult(CheckState.UNKNOWN, rng.choice(TRANSIENT_ERRORS))

        if truth.removed_at is not None and at >= truth.removed_at:
            return CheckResult(CheckState.DOWN)
        return CheckResult(CheckState.UP)


class SimulatedReuploadDetector:
    """ReuploadDetector backed by the simulator's ground truth.

    A later PR adds a PDQ-based detector that works on harmless test images.
    """

    def __init__(self, checker: SimulatedChecker) -> None:
        self.checker = checker

    def scan(self, record: RequestRecord, at: datetime) -> bool:
        truth = self.checker.ground_truth(record)
        return truth.reupload_at is not None and at >= truth.reupload_at
