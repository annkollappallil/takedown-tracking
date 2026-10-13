"""Domain models for police takedown and data requests."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class RequestType(str, Enum):
    TAKEDOWN = "takedown"
    DATA_REQUEST = "data_request"


class Platform(str, Enum):
    META = "Meta"
    INSTAGRAM = "Instagram"
    X_TWITTER = "X_Twitter"
    YOUTUBE = "YouTube"
    TELEGRAM = "Telegram"
    WHATSAPP = "WhatsApp"
    SNAPCHAT = "Snapchat"
    REDDIT = "Reddit"
    TIKTOK = "TikTok"
    OTHER = "Other"


class Category(str, Enum):
    CSAM = "CSAM"
    NON_CONSENSUAL_IMAGERY = "NonConsensualImagery"
    HATE_SPEECH = "HateSpeech"
    TERRORISM = "Terrorism"
    FINANCIAL_FRAUD = "FinancialFraud"
    DEFAMATION = "Defamation"
    IMPERSONATION = "Impersonation"
    VIOLENCE_INCITEMENT = "ViolenceIncitement"
    OTHER = "Other"


class Priority(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    EMERGENCY_LIFE_THREAT = "emergency_life_threat"


class RequestStatus(str, Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    CLAIMED_REMOVED = "claimed_removed"
    DATA_PROVIDED = "data_provided"
    REJECTED = "rejected"
    EXPIRED = "expired"


class IncompletionReason(str, Enum):
    MISSING_COURT_ORDER = "MissingCourtOrder"
    INVALID_URL_FORMAT = "InvalidURLFormat"
    UNSPECIFIED_JURISDICTION = "UnspecifiedJurisdiction"
    MISSING_SIGNATURE_OR_SEAL = "MissingSignatureOrSeal"
    INSUFFICIENT_TARGET_IDENTIFICATION = "InsufficientTargetIdentification"
    OTHER_DEFICIENCY = "OtherDeficiency"


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string into a UTC datetime object."""
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    if not dt_str:
        return None
    # Handle 'Z' suffix
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_iso_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime to standard ISO 8601 UTC string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RequestRecord:
    """Represents a single request log record."""

    request_id: str
    is_synthetic: bool
    request_type: RequestType
    platform: Platform
    category: Category
    priority: Priority
    sent_at: datetime
    police_unit_id: str
    is_complete_request: bool
    status: RequestStatus
    acknowledged_at: Optional[datetime] = None
    claimed_removal_at: Optional[datetime] = None
    data_provided_at: Optional[datetime] = None
    incompletion_reason: Optional[IncompletionReason] = None
    rejection_reason: Optional[str] = None
    content_url_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to JSON/dictionary compliant with the request_log schema."""
        return {
            "request_id": self.request_id,
            "is_synthetic": self.is_synthetic,
            "request_type": self.request_type.value,
            "platform": self.platform.value,
            "category": self.category.value,
            "priority": self.priority.value,
            "sent_at": format_iso_datetime(self.sent_at),
            "acknowledged_at": format_iso_datetime(self.acknowledged_at),
            "claimed_removal_at": format_iso_datetime(self.claimed_removal_at),
            "data_provided_at": format_iso_datetime(self.data_provided_at),
            "police_unit_id": self.police_unit_id,
            "is_complete_request": self.is_complete_request,
            "incompletion_reason": (
                self.incompletion_reason.value if self.incompletion_reason else None
            ),
            "status": self.status.value,
            "rejection_reason": self.rejection_reason,
            "content_url_hash": self.content_url_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RequestRecord:
        """Create a RequestRecord from dictionary with type casting and validation."""
        sent_at_parsed = parse_iso_datetime(data["sent_at"])
        if sent_at_parsed is None:
            raise ValueError(f"Invalid sent_at timestamp: {data.get('sent_at')}")

        incompletion_val = data.get("incompletion_reason")
        inc_reason = IncompletionReason(incompletion_val) if incompletion_val else None

        is_synth = data.get("is_synthetic")
        if isinstance(is_synth, str):
            is_synth = is_synth.strip().lower() in ("true", "1", "yes")
        else:
            is_synth = bool(is_synth)

        is_comp = data.get("is_complete_request")
        if isinstance(is_comp, str):
            is_comp = is_comp.strip().lower() in ("true", "1", "yes")
        else:
            is_comp = bool(is_comp)

        return cls(
            request_id=str(data["request_id"]).strip(),
            is_synthetic=is_synth,
            request_type=RequestType(str(data["request_type"]).strip()),
            platform=Platform(str(data["platform"]).strip()),
            category=Category(str(data["category"]).strip()),
            priority=Priority(str(data["priority"]).strip()),
            sent_at=sent_at_parsed,
            acknowledged_at=parse_iso_datetime(data.get("acknowledged_at")),
            claimed_removal_at=parse_iso_datetime(data.get("claimed_removal_at")),
            data_provided_at=parse_iso_datetime(data.get("data_provided_at")),
            police_unit_id=str(data["police_unit_id"]).strip(),
            is_complete_request=is_comp,
            incompletion_reason=inc_reason,
            status=RequestStatus(str(data["status"]).strip()),
            rejection_reason=data.get("rejection_reason"),
            content_url_hash=data.get("content_url_hash"),
        )
