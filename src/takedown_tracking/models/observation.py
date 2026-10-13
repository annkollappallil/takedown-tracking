"""Shared observation data contract for verification and integrity monitoring (Half 1 / Half 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from takedown_tracking.models.request import format_iso_datetime, parse_iso_datetime


class ObservationStatus(str, Enum):
    CONFIRMED_REMOVED = "confirmed_removed"
    CONFIRMED_AVAILABLE = "confirmed_available"
    UNCERTAIN = "uncertain"
    FAILED_CHECK = "failed_check"


@dataclass(frozen=True)
class ObservationRecord:
    """Represents an independent empirical verification observation of content status."""

    request_id: str
    observation_status: ObservationStatus
    last_checked_at: datetime
    verifier_agent_id: str
    verified_removed_at: Optional[datetime] = None
    reupload_detected_at: Optional[datetime] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary compliant with observation schema."""
        return {
            "request_id": self.request_id,
            "observation_status": self.observation_status.value,
            "last_checked_at": format_iso_datetime(self.last_checked_at),
            "verifier_agent_id": self.verifier_agent_id,
            "verified_removed_at": format_iso_datetime(self.verified_removed_at),
            "reupload_detected_at": format_iso_datetime(self.reupload_detected_at),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ObservationRecord:
        """Parse ObservationRecord from dictionary."""
        last_checked = parse_iso_datetime(data.get("last_checked_at"))
        if last_checked is None:
            raise ValueError(f"Invalid last_checked_at timestamp: {data.get('last_checked_at')}")

        return cls(
            request_id=str(data["request_id"]).strip(),
            observation_status=ObservationStatus(str(data["observation_status"]).strip()),
            last_checked_at=last_checked,
            verifier_agent_id=str(data.get("verifier_agent_id", "verifier-simulator")).strip(),
            verified_removed_at=parse_iso_datetime(data.get("verified_removed_at")),
            reupload_detected_at=parse_iso_datetime(data.get("reupload_detected_at")),
            notes=data.get("notes"),
        )
