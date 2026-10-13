"""Scheduled verification: runs checks on a fixed grid and summarises them.

For each complete takedown request the runner checks the content every
`check_interval_minutes` from the moment the request is sent until it is
confirmed gone, monitoring ends, or the `as_of` cutoff. It then writes one
ObservationRecord per request: the summary the metrics engine reads.

Removal is confirmed only after `confirmations_required` DOWN results in a row
(inconclusive checks in between are ignored), so a single glitchy 404 does not
count as removal. verified_removed_at is the first DOWN of that run. A single
DOWN at the very end of monitoring is accepted, since no further check is possible.

Removal times therefore have the resolution of the check interval.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from takedown_tracking.models.observation import (
    VISIBILITY_ERRORS,
    CheckError,
    ObservationRecord,
    ObservationStatus,
)
from takedown_tracking.models.request import RequestRecord, RequestType
from takedown_tracking.verifier.checker import AvailabilityChecker, CheckState, ReuploadDetector


@dataclass(frozen=True)
class RunnerConfig:
    check_interval_minutes: float = 15.0
    max_monitoring_days: float = 30.0
    confirmations_required: int = 2
    reupload_scan_interval_hours: float = 6.0
    reupload_window_days: float = 7.0

    def __post_init__(self) -> None:
        if self.check_interval_minutes <= 0:
            raise ValueError("check_interval_minutes must be positive")
        if self.confirmations_required < 1:
            raise ValueError("confirmations_required must be at least 1")
        if self.reupload_scan_interval_hours <= 0:
            raise ValueError("reupload_scan_interval_hours must be positive")


class VerificationRunner:
    def __init__(
        self,
        checker: AvailabilityChecker,
        reupload_detector: Optional[ReuploadDetector] = None,
        config: RunnerConfig = RunnerConfig(),
    ) -> None:
        self.checker = checker
        self.reupload_detector = reupload_detector
        self.config = config

    @staticmethod
    def should_monitor(record: RequestRecord) -> bool:
        """Only complete takedown requests are monitored. Data requests have nothing to check."""
        return record.request_type == RequestType.TAKEDOWN and record.is_complete_request

    def observe(self, record: RequestRecord, as_of: datetime) -> Optional[ObservationRecord]:
        """Run the check schedule for one request. Returns None if no check has happened yet."""
        if not self.should_monitor(record):
            return None

        as_of = _utc(as_of)
        interval = timedelta(minutes=self.config.check_interval_minutes)
        end = min(as_of, record.sent_at + timedelta(days=self.config.max_monitoring_days))

        t = record.sent_at + interval
        if t > end:
            return None

        first_checked = t
        last_checked = t
        last_up: Optional[datetime] = None
        pending_down: Optional[datetime] = None
        downs_in_a_row = 0
        verified_removed: Optional[datetime] = None
        conclusive = 0
        errors: Counter = Counter()

        while t <= end:
            result = self.checker.check(record, t)
            last_checked = t
            if result.state == CheckState.UNKNOWN:
                errors[result.error] += 1
            elif result.state == CheckState.UP:
                conclusive += 1
                last_up = t
                pending_down = None
                downs_in_a_row = 0
            else:
                conclusive += 1
                if pending_down is None:
                    pending_down = t
                downs_in_a_row += 1
                if downs_in_a_row >= self.config.confirmations_required:
                    verified_removed = pending_down
                    break
            t += interval

        if verified_removed is None and pending_down is not None:
            # Monitoring ended right after a DOWN result: accept it.
            verified_removed = pending_down

        if verified_removed is not None:
            return ObservationRecord(
                request_id=record.request_id,
                observation_status=ObservationStatus.CONFIRMED_REMOVED,
                first_checked_at=first_checked,
                last_seen_up_at=last_up,
                verified_removed_at=verified_removed,
                reupload_detected_at=self._scan_for_reupload(record, verified_removed, as_of),
                last_checked_at=last_checked,
                verifier_agent_id=self.checker.agent_id,
            )

        if conclusive == 0:
            error = errors.most_common(1)[0][0] if errors else CheckError.OTHER
            status = (
                ObservationStatus.UNCERTAIN if error in VISIBILITY_ERRORS else ObservationStatus.FAILED_CHECK
            )
            return ObservationRecord(
                request_id=record.request_id,
                observation_status=status,
                first_checked_at=first_checked,
                last_checked_at=last_checked,
                check_error=error,
                verifier_agent_id=self.checker.agent_id,
            )

        return ObservationRecord(
            request_id=record.request_id,
            observation_status=ObservationStatus.CONFIRMED_AVAILABLE,
            first_checked_at=first_checked,
            last_seen_up_at=last_up,
            last_checked_at=last_checked,
            verifier_agent_id=self.checker.agent_id,
        )

    def _scan_for_reupload(
        self, record: RequestRecord, removed_at: datetime, as_of: datetime
    ) -> Optional[datetime]:
        if self.reupload_detector is None:
            return None
        step = timedelta(hours=self.config.reupload_scan_interval_hours)
        stop = min(as_of, removed_at + timedelta(days=self.config.reupload_window_days))
        t = removed_at + step
        while t <= stop:
            if self.reupload_detector.scan(record, t):
                return t
            t += step
        return None

    def run(self, records: Iterable[RequestRecord], as_of: datetime) -> List[ObservationRecord]:
        observations = []
        for record in records:
            obs = self.observe(record, as_of)
            if obs is not None:
                observations.append(obs)
        return observations


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
