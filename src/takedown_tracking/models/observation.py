"""Shared observation data contract between the verifier (Half 2) and the metrics engine (Half 1).

One ObservationRecord summarises everything the verifier learned about one takedown
request. The verifier may keep every individual check internally; this summary is the
only thing it hands over.

Privacy: an observation carries only a request ID, timestamps, a status and an error
code. It never carries URLs, content hashes or free text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from takedown_tracking.models.request import format_iso_datetime, parse_iso_datetime


class ObservationStatus(str, Enum):
    """State of the content at the verifier's most recent conclusive check."""

    CONFIRMED_REMOVED = "confirmed_removed"      # checks confirmed the content is gone
    CONFIRMED_AVAILABLE = "confirmed_available"  # still up at the last conclusive check
    UNCERTAIN = "uncertain"                      # checks ran but could not see the content
    FAILED_CHECK = "failed_check"                # checks themselves failed (timeouts, errors)


class CheckError(str, Enum):
    """Why checks were inconclusive. Replaces free-text notes so nothing sensitive leaks."""

    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    RATE_LIMITED = "rate_limited"
    LOGIN_WALL = "login_wall"
    PRIVATE_OR_RESTRICTED = "private_or_restricted"
    GEO_BLOCKED = "geo_blocked"
    OTHER = "other"


# Errors meaning "we reached the platform but could not see the content".
VISIBILITY_ERRORS = frozenset(
    {CheckError.LOGIN_WALL, CheckError.PRIVATE_OR_RESTRICTED, CheckError.GEO_BLOCKED}
)


class ObservationValidationError(ValueError):
    """Raised when an observation is internally inconsistent. Never echoes field values."""


@dataclass(frozen=True)
class ObservationRecord:
    """Summary of independent verification of one takedown request.

    Timeline invariant (when present):
        first_checked_at <= last_seen_up_at < verified_removed_at <= last_checked_at
    """

    request_id: str
    observation_status: ObservationStatus
    last_checked_at: datetime
    verifier_agent_id: str
    first_checked_at: Optional[datetime] = None
    last_seen_up_at: Optional[datetime] = None
    verified_removed_at: Optional[datetime] = None
    reupload_detected_at: Optional[datetime] = None
    check_error: Optional[CheckError] = None

    def validate(self) -> None:
        """Check status and timeline consistency. Raises ObservationValidationError."""
        s = self.observation_status
        if s == ObservationStatus.CONFIRMED_REMOVED and self.verified_removed_at is None:
            raise ObservationValidationError("confirmed_removed requires verified_removed_at")
        if s != ObservationStatus.CONFIRMED_REMOVED and self.verified_removed_at is not None:
            raise ObservationValidationError("verified_removed_at is only allowed with confirmed_removed")
        if s == ObservationStatus.CONFIRMED_AVAILABLE and self.last_seen_up_at is None and self.first_checked_at is not None:
            raise ObservationValidationError("confirmed_available requires last_seen_up_at")
        if s in (ObservationStatus.UNCERTAIN, ObservationStatus.FAILED_CHECK) and self.check_error is None:
            raise ObservationValidationError(f"{s.value} requires check_error")
        if self.reupload_detected_at is not None:
            if self.verified_removed_at is None:
                raise ObservationValidationError("reupload_detected_at requires a verified removal")
            if self.reupload_detected_at <= self.verified_removed_at:
                raise ObservationValidationError("reupload_detected_at must be after verified_removed_at")

        ordered = [
            ("first_checked_at", self.first_checked_at),
            ("last_seen_up_at", self.last_seen_up_at),
            ("verified_removed_at", self.verified_removed_at),
            ("last_checked_at", self.last_checked_at),
        ]
        present = [(name, ts) for name, ts in ordered if ts is not None]
        for (name_a, a), (name_b, b) in zip(present, present[1:]):
            if a > b:
                raise ObservationValidationError(f"{name_a} must not be later than {name_b}")
        if (
            self.last_seen_up_at is not None
            and self.verified_removed_at is not None
            and self.last_seen_up_at >= self.verified_removed_at
        ):
            raise ObservationValidationError("last_seen_up_at must be before verified_removed_at")

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary compliant with observation schema."""
        return {
            "request_id": self.request_id,
            "observation_status": self.observation_status.value,
            "first_checked_at": format_iso_datetime(self.first_checked_at),
            "last_seen_up_at": format_iso_datetime(self.last_seen_up_at),
            "verified_removed_at": format_iso_datetime(self.verified_removed_at),
            "reupload_detected_at": format_iso_datetime(self.reupload_detected_at),
            "last_checked_at": format_iso_datetime(self.last_checked_at),
            "check_error": self.check_error.value if self.check_error else None,
            "verifier_agent_id": self.verifier_agent_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], validate: bool = True) -> ObservationRecord:
        """Parse ObservationRecord from dictionary and (by default) validate it."""
        last_checked = parse_iso_datetime(data.get("last_checked_at"))
        if last_checked is None:
            raise ObservationValidationError("last_checked_at is required")
        if "notes" in data:
            raise ObservationValidationError(
                "free-text 'notes' is not allowed; use the check_error code instead"
            )

        error_val = data.get("check_error")
        record = cls(
            request_id=str(data["request_id"]).strip(),
            observation_status=ObservationStatus(str(data["observation_status"]).strip()),
            last_checked_at=last_checked,
            verifier_agent_id=str(data.get("verifier_agent_id", "verifier-simulator")).strip(),
            first_checked_at=parse_iso_datetime(data.get("first_checked_at")),
            last_seen_up_at=parse_iso_datetime(data.get("last_seen_up_at")),
            verified_removed_at=parse_iso_datetime(data.get("verified_removed_at")),
            reupload_detected_at=parse_iso_datetime(data.get("reupload_detected_at")),
            check_error=CheckError(str(error_val).strip()) if error_val else None,
        )
        if validate:
            record.validate()
        return record
