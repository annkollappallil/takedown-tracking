"""Integrity metrics (Half 2): how honest and how complete platform removals are.

All metrics cover complete takedown requests sent before `as_of`.

- verification_coverage_pct: share of those requests the verifier has an observation for.
- unverifiable_pct: share of observed requests where checks were inconclusive
  (uncertain or failed_check). High values mean the other integrity figures are less reliable.
- false_claim_rate_pct: of removal claims the verifier could test, the share where the
  content was seen up after the claim (see rules.is_false_claim).
- silent_removals_count: removals the verifier confirmed that the platform never reported.
- reupload_rate_pct: of verified removals whose whole re-upload window has passed, the
  share where a matching copy appeared within the window. Removals whose window is still
  open are left out so recent removals don't make the rate look better than it is.

publish() withholds any figure whose group has fewer than k requests. A count and its
rate are always shown or hidden together, so hidden counts can't be recovered.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from takedown_tracking.integrity.rules import (
    CONCLUSIVE,
    DEFAULT_FALSE_CLAIM_GRACE,
    DEFAULT_REUPLOAD_WINDOW,
    has_claim,
    is_false_claim,
    is_reupload_within_window,
    is_verified_removal,
    reupload_window_closed,
)
from takedown_tracking.models.observation import ObservationRecord
from takedown_tracking.models.request import RequestRecord, RequestStatus, RequestType


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 2) if whole else None


@dataclass(frozen=True)
class IntegrityMetrics:
    eligible_takedowns: int
    monitored_takedowns: int
    verification_coverage_pct: Optional[float]
    unverifiable_count: int
    unverifiable_pct: Optional[float]
    claims_checked: int
    false_claims_count: int
    false_claim_rate_pct: Optional[float]
    verified_removals: int
    silent_removals_count: int
    reupload_cohort: int
    reuploads_within_window: int
    reupload_rate_pct: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def publish(self, k: int) -> Dict[str, Any]:
        """Privacy-filtered copy for public output.

        Each figure is shown only if the group it describes has at least k requests.
        A count and its rate share one gate, so a hidden count can never be worked
        back out from a published rate and denominator.
        """
        eligible, monitored = self.eligible_takedowns, self.monitored_takedowns
        claims, verified, cohort = self.claims_checked, self.verified_removals, self.reupload_cohort

        def gate(value: Any, group_size: int) -> Any:
            return value if group_size >= k else None

        return {
            "eligible_takedowns": gate(eligible, eligible),
            "monitored_takedowns": gate(monitored, eligible),
            "verification_coverage_pct": gate(self.verification_coverage_pct, eligible),
            "unverifiable_pct": gate(self.unverifiable_pct, monitored),
            "claims_checked": gate(claims, eligible),
            "false_claims_count": gate(self.false_claims_count, claims),
            "false_claim_rate_pct": gate(self.false_claim_rate_pct, claims),
            "verified_removals": gate(verified, monitored),
            "silent_removals_count": gate(self.silent_removals_count, verified),
            "reupload_cohort": gate(cohort, verified),
            "reuploads_within_window": gate(self.reuploads_within_window, cohort),
            "reupload_rate_pct": gate(self.reupload_rate_pct, cohort),
        }


def compute_integrity(
    records: Iterable[RequestRecord],
    observations: Mapping[str, ObservationRecord],
    as_of: datetime,
    grace: timedelta = DEFAULT_FALSE_CLAIM_GRACE,
    reupload_window: timedelta = DEFAULT_REUPLOAD_WINDOW,
) -> IntegrityMetrics:
    eligible = [
        r
        for r in records
        if r.request_type == RequestType.TAKEDOWN and r.is_complete_request and r.sent_at <= as_of
    ]

    monitored = unverifiable = 0
    claims_checked = false_claims = 0
    verified = silent = 0
    reupload_cohort = reuploads = 0

    for r in eligible:
        obs = observations.get(r.request_id)
        if obs is None:
            continue
        monitored += 1
        if obs.observation_status not in CONCLUSIVE:
            unverifiable += 1
            continue

        # A claim is testable if the verifier was still checking when it was made.
        if has_claim(r) and r.claimed_removal_at <= as_of and obs.last_checked_at >= r.claimed_removal_at:
            claims_checked += 1
            if is_false_claim(r, obs, grace, as_of):
                false_claims += 1

        if is_verified_removal(obs, as_of):
            verified += 1
            if r.status in (RequestStatus.PENDING, RequestStatus.ACKNOWLEDGED, RequestStatus.EXPIRED):
                silent += 1
            if reupload_window_closed(obs, as_of, reupload_window):
                reupload_cohort += 1
                if is_reupload_within_window(obs, as_of, reupload_window):
                    reuploads += 1

    return IntegrityMetrics(
        eligible_takedowns=len(eligible),
        monitored_takedowns=monitored,
        verification_coverage_pct=_pct(monitored, len(eligible)),
        unverifiable_count=unverifiable,
        unverifiable_pct=_pct(unverifiable, monitored),
        claims_checked=claims_checked,
        false_claims_count=false_claims,
        false_claim_rate_pct=_pct(false_claims, claims_checked),
        verified_removals=verified,
        silent_removals_count=silent,
        reupload_cohort=reupload_cohort,
        reuploads_within_window=reuploads,
        reupload_rate_pct=_pct(reuploads, reupload_cohort),
    )


def compute_integrity_by_platform(
    records: Iterable[RequestRecord],
    observations: Mapping[str, ObservationRecord],
    as_of: datetime,
    grace: timedelta = DEFAULT_FALSE_CLAIM_GRACE,
    reupload_window: timedelta = DEFAULT_REUPLOAD_WINDOW,
) -> Dict[str, IntegrityMetrics]:
    groups: Dict[str, List[RequestRecord]] = defaultdict(list)
    for r in records:
        groups[r.platform.value].append(r)
    return {
        name: compute_integrity(group, observations, as_of, grace, reupload_window)
        for name, group in sorted(groups.items())
    }


def build_integrity_summary(
    records: List[RequestRecord],
    observations: Mapping[str, ObservationRecord],
    as_of: datetime,
    k: int = 10,
    grace: timedelta = DEFAULT_FALSE_CLAIM_GRACE,
    reupload_window: timedelta = DEFAULT_REUPLOAD_WINDOW,
) -> Dict[str, Any]:
    """Public, privacy-filtered integrity summary for the scoreboard's integrity_summary slot."""
    overall = compute_integrity(records, observations, as_of, grace, reupload_window)
    by_platform = compute_integrity_by_platform(records, observations, as_of, grace, reupload_window)
    public_overall = overall.publish(k)

    return {
        # Keys from the original integrity_summary slot, kept for compatibility.
        "false_removal_claims_count": public_overall["false_claims_count"],
        "false_removal_claim_rate_pct": public_overall["false_claim_rate_pct"],
        "observed_reuploads_count": public_overall["reuploads_within_window"],
        "verified_takedowns_count": public_overall["verified_removals"],
        "verification_coverage_pct": public_overall["verification_coverage_pct"],
        "disputes_active": None,
        "disputes_resolved": None,
        # Full figures.
        "method": {
            "false_claim_grace_minutes": grace.total_seconds() / 60.0,
            "reupload_window_days": reupload_window.total_seconds() / 86400.0,
            "k_threshold": k,
            "as_of": as_of.isoformat(),
        },
        "overall": public_overall,
        "by_platform": [
            {"platform": name, **m.publish(k)}
            for name, m in by_platform.items()
            if m.eligible_takedowns > 0
        ],
    }
