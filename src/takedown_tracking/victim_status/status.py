"""What a victim sees about their own takedown request.

Only what helps the victim follow progress is shown: when it was sent, the legal
deadline, whether the platform says it removed the content, whether the
independent check confirms it, and whether a copy was found. The content
category, platform, links, hashes and police unit are never included, so a leaked
access code reveals as little as possible.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, Mapping, Optional, Tuple

from takedown_tracking.integrity.rules import effective_removal_time, is_false_claim, is_verified_removal
from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.models.observation import ObservationRecord, ObservationStatus
from takedown_tracking.models.request import (
    RequestRecord,
    RequestStatus,
    RequestType,
    format_iso_datetime,
)
from takedown_tracking.victim_status.access import AccessStore, normalise_reference

STAGE_MESSAGES = {
    "needs_police_action": "The police are completing your request before the platform can act on it.",
    "sent_to_platform": "Your request has been sent to the platform. We are checking regularly whether the content is still online.",
    "platform_acknowledged": "The platform has received your request. We are checking regularly whether the content is still online.",
    "platform_declined": "The platform declined to remove the content. The police can escalate this.",
    "still_online_despite_claim": "The platform said the content was removed, but our independent checks found it still online. This has been recorded against the platform.",
    "platform_says_removed": "The platform says the content was removed. We have not been able to confirm this independently yet.",
    "removal_confirmed": "Independent checks confirm the content is no longer online.",
}


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600.0, 2)


def build_status(
    record: RequestRecord,
    obs: Optional[ObservationRecord],
    now: datetime,
    deadlines: Optional[DeadlineConfig] = None,
) -> Dict[str, Any]:
    deadlines = deadlines or DeadlineConfig()
    deadline_h = deadlines.get_deadline_hours(record)
    deadline_at = record.sent_at + timedelta(hours=deadline_h)
    removal = effective_removal_time(record, obs, now)
    confirmed = is_verified_removal(obs, now)

    if not record.is_complete_request:
        stage = "needs_police_action"
    elif record.status == RequestStatus.REJECTED and removal is None:
        stage = "platform_declined"
    elif removal is not None:
        stage = "removal_confirmed" if confirmed else "platform_says_removed"
    elif is_false_claim(record, obs, as_of=now):
        stage = "still_online_despite_claim"
    elif record.acknowledged_at is not None and record.acknowledged_at <= now:
        stage = "platform_acknowledged"
    else:
        stage = "sent_to_platform"

    end = removal or now
    if removal is not None:
        within: Optional[bool] = removal <= deadline_at
    else:
        within = None if now < deadline_at else False

    check: Optional[Dict[str, Any]] = None
    first_check = (obs.first_checked_at or obs.last_checked_at) if obs is not None else None
    if obs is not None and first_check <= now:
        check = {
            "last_checked_at": format_iso_datetime(min(obs.last_checked_at, now)),
            "result": {
                ObservationStatus.CONFIRMED_REMOVED: "removed" if confirmed else "online",
                ObservationStatus.CONFIRMED_AVAILABLE: "online",
                ObservationStatus.UNCERTAIN: "could_not_check",
                ObservationStatus.FAILED_CHECK: "could_not_check",
            }[obs.observation_status],
        }

    copy_at = obs.reupload_detected_at if obs and obs.reupload_detected_at and obs.reupload_detected_at <= now else None
    message = STAGE_MESSAGES[stage]
    if within is False and removal is None and stage != "needs_police_action":
        if stage == "still_online_despite_claim":
            message += " The legal deadline has also passed."
        else:
            message += " The legal deadline has passed and this is being recorded against the platform."
    if copy_at is not None:
        message += " A copy was later found online and has been flagged for removal."

    return {
        "reference": record.request_id,
        "stage": stage,
        "message": message,
        "sent_at": format_iso_datetime(record.sent_at),
        "legal_deadline_hours": deadline_h,
        "deadline_at": format_iso_datetime(deadline_at),
        "removed_at": format_iso_datetime(removal),
        "removal_confirmed_independently": confirmed,
        "hours_taken": _hours(end - record.sent_at) if removal is not None else None,
        "hours_elapsed": _hours(end - record.sent_at) if removal is None else None,
        "within_deadline": within,
        "independent_check": check,
        "copy_found_at": format_iso_datetime(copy_at),
        "checked_at": format_iso_datetime(now),
    }


class RateLimiter:
    """Blocks repeated failed lookups: per client and per reference."""

    def __init__(self, per_client: int = 5, per_reference: int = 10, window: timedelta = timedelta(minutes=15)) -> None:
        self.per_client = per_client
        self.per_reference = per_reference
        self.window = window
        self._failures: Dict[Tuple[str, str], Deque[datetime]] = {}

    def _recent(self, key: Tuple[str, str], now: datetime) -> Deque[datetime]:
        q = self._failures.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, client: str, reference: str, now: datetime) -> bool:
        return (
            len(self._recent(("client", client), now)) >= self.per_client
            or len(self._recent(("ref", reference), now)) >= self.per_reference
        )

    def record_failure(self, client: str, reference: str, now: datetime) -> None:
        self._recent(("client", client), now).append(now)
        self._recent(("ref", reference), now).append(now)


class LookupResult:
    OK = "ok"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"


NOT_FOUND_MESSAGE = "No matching request. Check the reference number and access code you were given."
RATE_LIMITED_MESSAGE = "Too many attempts. Please wait 15 minutes and try again, or contact the police station."


class StatusService:
    """Looks up a victim's status from reference + access code."""

    def __init__(
        self,
        access: AccessStore,
        records: Mapping[str, RequestRecord],
        observations: Optional[Mapping[str, ObservationRecord]] = None,
        deadlines: Optional[DeadlineConfig] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        limiter: Optional[RateLimiter] = None,
    ) -> None:
        self.access = access
        self.records = {normalise_reference(k): v for k, v in records.items() if v.request_type == RequestType.TAKEDOWN}
        self.observations = dict(observations or {})
        self.deadlines = deadlines or DeadlineConfig()
        self.clock = clock
        self.limiter = limiter or RateLimiter()

    def lookup(self, reference: str, code: str, client: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        now = self.clock()
        ref = normalise_reference(reference)
        if self.limiter.blocked(client, ref, now):
            return LookupResult.RATE_LIMITED, None
        record = self.records.get(ref)
        # Always verify (even for unknown references) so timing doesn't reveal which exist.
        if not self.access.verify(ref, code) or record is None:
            self.limiter.record_failure(client, ref, now)
            return LookupResult.NOT_FOUND, None
        return LookupResult.OK, build_status(record, self.observations.get(record.request_id), now, self.deadlines)
