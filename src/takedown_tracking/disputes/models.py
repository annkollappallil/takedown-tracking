"""Dispute flow (Half 2): platforms check a draft report and can dispute it before publication.

One ReportCycle covers one reporting period:
  1. Police share the draft scoreboard, and each platform gets a statement of its own
     requests (it already knows these requests; no URLs or content are included).
  2. During the dispute window (default 7 days) a platform raises disputes with a fixed
     reason code, the metric it disagrees with, the requests involved and a short statement.
  3. Police accept or reject every dispute. Accepted disputes change the data in a fixed,
     auditable way (see EFFECTS). Rejected ones are published as rejected.
  4. Publication is blocked while the window is open or any dispute is unresolved.

The public scoreboard only shows dispute counts per platform and which metrics were
disputed. Statements, police notes and request IDs stay in the police-held cycle file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from takedown_tracking.models.request import format_iso_datetime, parse_iso_datetime

MAX_TEXT_LENGTH = 1000
_URL_PATTERN = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|in|org|net|me|io)\b)", re.IGNORECASE)


class DisputeError(ValueError):
    """Raised when a dispute action is not allowed. Messages never echo free text."""


class DisputableMetric(str, Enum):
    MEDIAN_REMOVAL_HOURS = "median_removal_hours"
    P90_REMOVAL_HOURS = "p90_removal_hours"
    DEADLINE_COMPLIANCE_RATE = "deadline_compliance_rate_pct"
    STILL_AVAILABLE_RATE = "still_available_rate_pct"
    DATA_RESPONSE_HOURS = "median_data_response_hours"
    FALSE_CLAIM_RATE = "false_claim_rate_pct"
    REUPLOAD_RATE = "reupload_rate_pct"
    SILENT_REMOVALS = "silent_removals_count"
    OTHER = "other"


class DisputeReason(str, Enum):
    NOT_OUR_PLATFORM = "not_our_platform"          # request was sent to / logged for the wrong platform
    DUPLICATE_REQUEST = "duplicate_request"        # same content requested twice
    OUTSIDE_PERIOD = "outside_period"              # request belongs to another period
    REQUEST_INCOMPLETE = "request_incomplete"      # police request lacked what the law requires
    WRONG_TIMESTAMP = "wrong_timestamp"            # platform's records show a different removal time
    VERIFICATION_ERROR = "verification_error"      # independent check was wrong (e.g. geo-blocked view)
    OTHER = "other"


# What accepting a dispute does to the data before the report is recomputed.
EFFECTS: Dict[DisputeReason, str] = {
    DisputeReason.NOT_OUR_PLATFORM: "exclude_requests",
    DisputeReason.DUPLICATE_REQUEST: "exclude_requests",
    DisputeReason.OUTSIDE_PERIOD: "exclude_requests",
    DisputeReason.REQUEST_INCOMPLETE: "mark_incomplete",
    DisputeReason.WRONG_TIMESTAMP: "correct_claimed_removal",
    DisputeReason.VERIFICATION_ERROR: "set_aside_observation",
    DisputeReason.OTHER: "none",
}

NEEDS_REQUESTS = {reason for reason, effect in EFFECTS.items() if effect != "none"}


class DisputeStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


def clean_text(value: str, field_name: str) -> str:
    """Short plain text only: no URLs, so content links can't end up in the record."""
    text = " ".join((value or "").split())
    if not text:
        raise DisputeError(f"{field_name} is required")
    if len(text) > MAX_TEXT_LENGTH:
        raise DisputeError(f"{field_name} must be at most {MAX_TEXT_LENGTH} characters")
    if _URL_PATTERN.search(text):
        raise DisputeError(f"{field_name} must not contain links; refer to requests by ID")
    return text


@dataclass(frozen=True)
class Dispute:
    dispute_id: str
    platform: str
    metric: DisputableMetric
    reason: DisputeReason
    statement: str
    raised_at: datetime
    request_ids: Tuple[str, ...] = ()
    status: DisputeStatus = DisputeStatus.OPEN
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    corrections: Tuple[Tuple[str, datetime], ...] = ()  # (request_id, corrected claimed_removal_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "platform": self.platform,
            "metric": self.metric.value,
            "reason": self.reason.value,
            "statement": self.statement,
            "raised_at": format_iso_datetime(self.raised_at),
            "request_ids": list(self.request_ids),
            "status": self.status.value,
            "resolved_at": format_iso_datetime(self.resolved_at),
            "resolution_note": self.resolution_note,
            "corrections": [
                {"request_id": rid, "claimed_removal_at": format_iso_datetime(ts)} for rid, ts in self.corrections
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dispute":
        return cls(
            dispute_id=str(data["dispute_id"]),
            platform=str(data["platform"]),
            metric=DisputableMetric(data["metric"]),
            reason=DisputeReason(data["reason"]),
            statement=str(data["statement"]),
            raised_at=parse_iso_datetime(data["raised_at"]),
            request_ids=tuple(data.get("request_ids") or ()),
            status=DisputeStatus(data.get("status", "open")),
            resolved_at=parse_iso_datetime(data.get("resolved_at")),
            resolution_note=data.get("resolution_note"),
            corrections=tuple(
                (c["request_id"], parse_iso_datetime(c["claimed_removal_at"])) for c in data.get("corrections") or ()
            ),
        )


@dataclass
class ReportCycle:
    cycle_id: str
    period_start: datetime
    period_end: datetime
    as_of: datetime
    draft_shared_at: datetime
    dispute_window_days: float = 7.0
    disputes: List[Dispute] = field(default_factory=list)
    published_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.period_start < self.period_end <= self.as_of:
            raise DisputeError("need period_start < period_end <= as_of")
        if self.draft_shared_at < self.as_of:
            raise DisputeError("the draft can only be shared after the as_of cutoff")
        if self.dispute_window_days <= 0:
            raise DisputeError("dispute_window_days must be positive")

    # --------------------------------------------------------------------- timing

    @property
    def window_closes_at(self) -> datetime:
        return self.draft_shared_at + timedelta(days=self.dispute_window_days)

    def window_open(self, now: datetime) -> bool:
        return self.published_at is None and self.draft_shared_at <= now < self.window_closes_at

    # --------------------------------------------------------------------- lookups

    def get(self, dispute_id: str) -> Dispute:
        for d in self.disputes:
            if d.dispute_id == dispute_id:
                return d
        raise DisputeError("unknown dispute_id")

    def _replace(self, updated: Dispute) -> None:
        self.disputes = [updated if d.dispute_id == updated.dispute_id else d for d in self.disputes]

    def open_disputes(self) -> List[Dispute]:
        return [d for d in self.disputes if d.status == DisputeStatus.OPEN]

    # --------------------------------------------------------------------- actions

    def raise_dispute(
        self,
        platform: str,
        metric: DisputableMetric,
        reason: DisputeReason,
        statement: str,
        now: datetime,
        request_ids: Tuple[str, ...] = (),
    ) -> Dispute:
        if not self.window_open(now):
            raise DisputeError("the dispute window is not open")
        if reason in NEEDS_REQUESTS and not request_ids:
            raise DisputeError(f"reason '{reason.value}' needs the request IDs it concerns")
        dispute = Dispute(
            dispute_id=f"DSP-{self.cycle_id}-{len(self.disputes) + 1:03d}",
            platform=platform,
            metric=metric,
            reason=reason,
            statement=clean_text(statement, "statement"),
            raised_at=now,
            request_ids=tuple(dict.fromkeys(request_ids)),
        )
        self.disputes.append(dispute)
        return dispute

    def resolve(
        self,
        dispute_id: str,
        accept: bool,
        note: str,
        now: datetime,
        corrections: Optional[Dict[str, datetime]] = None,
    ) -> Dispute:
        if self.published_at is not None:
            raise DisputeError("the report is already published")
        dispute = self.get(dispute_id)
        if dispute.status != DisputeStatus.OPEN:
            raise DisputeError("only open disputes can be resolved")
        if now < dispute.raised_at:
            raise DisputeError("cannot resolve a dispute before it was raised")

        fixed: Tuple[Tuple[str, datetime], ...] = ()
        if accept and dispute.reason == DisputeReason.WRONG_TIMESTAMP:
            corrections = corrections or {}
            if set(corrections) != set(dispute.request_ids):
                raise DisputeError("accepting a wrong_timestamp dispute needs a corrected time for each request")
            fixed = tuple(sorted(corrections.items()))
        elif corrections:
            raise DisputeError("corrections are only used when accepting wrong_timestamp disputes")

        updated = replace(
            dispute,
            status=DisputeStatus.ACCEPTED if accept else DisputeStatus.REJECTED,
            resolved_at=now,
            resolution_note=clean_text(note, "note"),
            corrections=fixed,
        )
        self._replace(updated)
        return updated

    def withdraw(self, dispute_id: str, now: datetime) -> Dispute:
        dispute = self.get(dispute_id)
        if dispute.status != DisputeStatus.OPEN:
            raise DisputeError("only open disputes can be withdrawn")
        updated = replace(dispute, status=DisputeStatus.WITHDRAWN, resolved_at=now)
        self._replace(updated)
        return updated

    def publish_blockers(self, now: datetime) -> List[str]:
        blockers = []
        if self.published_at is not None:
            blockers.append("already published")
        if now < self.window_closes_at:
            blockers.append(f"dispute window open until {self.window_closes_at.isoformat()}")
        if self.open_disputes():
            blockers.append(f"{len(self.open_disputes())} dispute(s) still open")
        return blockers

    def mark_published(self, now: datetime) -> None:
        blockers = self.publish_blockers(now)
        if blockers:
            raise DisputeError("cannot publish: " + "; ".join(blockers))
        self.published_at = now

    # --------------------------------------------------------------------- persistence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "period_start": format_iso_datetime(self.period_start),
            "period_end": format_iso_datetime(self.period_end),
            "as_of": format_iso_datetime(self.as_of),
            "draft_shared_at": format_iso_datetime(self.draft_shared_at),
            "dispute_window_days": self.dispute_window_days,
            "published_at": format_iso_datetime(self.published_at),
            "disputes": [d.to_dict() for d in self.disputes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReportCycle":
        return cls(
            cycle_id=str(data["cycle_id"]),
            period_start=parse_iso_datetime(data["period_start"]),
            period_end=parse_iso_datetime(data["period_end"]),
            as_of=parse_iso_datetime(data["as_of"]),
            draft_shared_at=parse_iso_datetime(data["draft_shared_at"]),
            dispute_window_days=float(data.get("dispute_window_days", 7.0)),
            disputes=[Dispute.from_dict(d) for d in data.get("disputes", [])],
            published_at=parse_iso_datetime(data.get("published_at")),
        )
