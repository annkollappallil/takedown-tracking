"""Pluggable interfaces for independent verification.

A real deployment implements these inside the police network: it resolves a
request_id to the reported URL from the police system, checks it, and returns
only a state and an error code. URLs and content never leave the checker.

This repository ships only simulated implementations (see simulator.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol

from takedown_tracking.models.observation import CheckError
from takedown_tracking.models.request import RequestRecord


class CheckState(str, Enum):
    UP = "up"            # content reachable
    DOWN = "down"        # content gone (e.g. 404/410, "content unavailable")
    UNKNOWN = "unknown"  # check could not decide; see CheckResult.error


@dataclass(frozen=True)
class CheckResult:
    state: CheckState
    error: Optional[CheckError] = None

    def __post_init__(self) -> None:
        if self.state == CheckState.UNKNOWN and self.error is None:
            raise ValueError("UNKNOWN check results must carry an error code")
        if self.state != CheckState.UNKNOWN and self.error is not None:
            raise ValueError("Only UNKNOWN check results may carry an error code")


class AvailabilityChecker(Protocol):
    """Checks whether the content behind one takedown request is still up."""

    agent_id: str

    def check(self, record: RequestRecord, at: datetime) -> CheckResult:
        """Return the state of the reported content at time `at`.

        Must not download or store the content. Must be side-effect free apart
        from the network request itself.
        """
        ...


class ReuploadDetector(Protocol):
    """Looks for copies of removed content (e.g. via PDQ perceptual hashes)."""

    def scan(self, record: RequestRecord, at: datetime) -> bool:
        """Return True if a matching copy is live at time `at`.

        Hashes stay inside the detector. Only the boolean leaves it.
        """
        ...
