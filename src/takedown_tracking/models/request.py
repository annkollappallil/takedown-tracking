"""Domain models for police takedown and data requests."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import os
from typing import Any, Dict, Optional


class RequestType(str, Enum):
    TAKEDOWN = "takedown"
    DATA_REQUEST = "data_request"


class Platform(str, Enum):
    # Fictional platform identities for demonstration, test, and synthetic data
    PLATFORM_A = "Platform A"
    PLATFORM_B = "Platform B"
    PLATFORM_C = "Platform C"
    PLATFORM_D = "Platform D"
    PLATFORM_E = "Platform E"
    PLATFORM_F = "Platform F"
    PLATFORM_G = "Platform G"
    PLATFORM_H = "Platform H"
    PLATFORM_I = "Platform I"
    PLATFORM_J = "Platform J"

    # Authorized real platform identifiers for production deployment mode
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


class LegalRoute(str, Enum):
    """Statutory or procedural route governing the request."""
    COURT_ORDER_OR_GOVT_INTIMATION = "court_order_or_govt_intimation"  # IT Rules Rule 3(1)(d) (3h)
    GRIEVANCE_COMPLAINT_3_2_B = "grievance_complaint_3_2_b"            # IT Rules Rule 3(2)(b) (2h)
    DATA_ASSISTANCE_ORDER = "data_assistance_order"                    # Statutory 72h data request
    ROUTINE_POLICE_INQUIRY = "routine_police_inquiry"                  # Standard police inquiry
    OTHER = "other"                                                    # Unclassified / general operational


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


NONPROD_TEST_HMAC_KEY = "NONPROD_SYNTHETIC_HMAC_KEY_DO_NOT_USE_IN_PRODUCTION"


def compute_url_hmac(
    url: str,
    secret_key: Optional[str] = None,
    is_synthetic: bool = False,
) -> str:
    """Compute HMAC-SHA-256 for a content URL with strict secret handling.

    Fails closed in production mode if the secret is missing.
    In synthetic / test mode, permits a non-production test key.
    """
    if not url:
        raise ValueError("URL cannot be empty")

    key = secret_key or os.environ.get("TAKEDOWN_HMAC_SECRET")
    if not key:
        if is_synthetic:
            key = NONPROD_TEST_HMAC_KEY
        else:
            raise ValueError(
                "TAKEDOWN_HMAC_SECRET environment variable is required for production URL "
                "pseudonymization. Fail closed: refusing to process real data with fallback secret."
            )

    canonical_url = url.strip()
    return hmac.new(
        key.encode("utf-8"),
        canonical_url.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string into a UTC datetime object."""
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    if not dt_str:
        return None
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
    legal_route: Optional[LegalRoute] = None
    acknowledged_at: Optional[datetime] = None
    claimed_removal_at: Optional[datetime] = None
    data_provided_at: Optional[datetime] = None
    incompletion_reason: Optional[IncompletionReason] = None
    rejection_reason: Optional[str] = None
    content_url_hmac: Optional[str] = None
    content_url_hash: Optional[str] = None  # Backward-compatibility alias

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to JSON/dictionary compliant with the request_log schema."""
        effective_hmac = self.content_url_hmac or self.content_url_hash
        return {
            "request_id": self.request_id,
            "is_synthetic": self.is_synthetic,
            "request_type": self.request_type.value,
            "platform": self.platform.value,
            "category": self.category.value,
            "priority": self.priority.value,
            "legal_route": self.legal_route.value if self.legal_route else None,
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
            "content_url_hmac": effective_hmac,
            "content_url_hash": effective_hmac,  # preserved for compatibility
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RequestRecord:
        """Create a RequestRecord from dictionary with type casting and validation."""
        sent_at_parsed = parse_iso_datetime(data["sent_at"])
        if sent_at_parsed is None:
            raise ValueError(f"Invalid sent_at timestamp: {data.get('sent_at')}")

        incompletion_val = data.get("incompletion_reason")
        inc_reason = IncompletionReason(incompletion_val) if incompletion_val else None

        route_val = data.get("legal_route")
        legal_route = LegalRoute(route_val) if route_val else None

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

        hmac_val = data.get("content_url_hmac") or data.get("content_url_hash")

        return cls(
            request_id=str(data["request_id"]).strip(),
            is_synthetic=is_synth,
            request_type=RequestType(str(data["request_type"]).strip()),
            platform=Platform(str(data["platform"]).strip()),
            category=Category(str(data["category"]).strip()),
            priority=Priority(str(data["priority"]).strip()),
            legal_route=legal_route,
            sent_at=sent_at_parsed,
            acknowledged_at=parse_iso_datetime(data.get("acknowledged_at")),
            claimed_removal_at=parse_iso_datetime(data.get("claimed_removal_at")),
            data_provided_at=parse_iso_datetime(data.get("data_provided_at")),
            police_unit_id=str(data["police_unit_id"]).strip(),
            is_complete_request=is_comp,
            incompletion_reason=inc_reason,
            status=RequestStatus(str(data["status"]).strip()),
            rejection_reason=data.get("rejection_reason"),
            content_url_hmac=hmac_val,
            content_url_hash=hmac_val,
        )
