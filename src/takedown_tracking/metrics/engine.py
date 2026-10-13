"""Comprehensive metric calculation engine with right-censoring, as_of cutoff, and observation verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Dict, List, Optional

from takedown_tracking.integrity.rules import (
    effective_removal_time,
    has_claim,
    is_false_claim,
    is_reupload_within_window,
    is_verified_removal,
)
from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.models.metrics import PerformanceMetrics, PercentileResult
from takedown_tracking.models.observation import ObservationRecord
from takedown_tracking.models.request import (
    Category,
    Platform,
    RequestRecord,
    RequestStatus,
    RequestType,
)


def calculate_percentile(values: List[float], percentile: float) -> Optional[float]:
    """Calculate percentile using linear interpolation between closest ranks (NIST/numpy method)."""
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    if percentile < 0.0 or percentile > 100.0:
        raise ValueError("Percentile must be between 0.0 and 100.0")

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    rank = (percentile / 100.0) * (n - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))
    fraction = rank - lower_idx

    if lower_idx == upper_idx:
        return round(sorted_vals[lower_idx], 2)

    interpolated = sorted_vals[lower_idx] + fraction * (
        sorted_vals[upper_idx] - sorted_vals[lower_idx]
    )
    return round(interpolated, 2)


def calculate_censored_percentile(
    finite_durations: List[float],
    total_cohort_size: int,
    percentile: float,
) -> PercentileResult:
    """Calculate survival percentile accounting for right-censored unresolved outcomes.

    Eliminates survivorship bias. In a mature cohort of size N:
    - k requests have finite observed removal durations.
    - N - k requests remain unresolved past deadline and are right-censored (+infinity).
    If the percentile rank touches or exceeds k, the outcome is censored (> measurement window).
    Never serializes Infinity or NaN into output.
    """
    if total_cohort_size <= 0:
        return PercentileResult(numeric_hours=None, is_censored=False, display="N/A")

    if percentile < 0.0 or percentile > 100.0:
        raise ValueError("Percentile must be between 0.0 and 100.0")

    sorted_vals = sorted(finite_durations)
    k = len(sorted_vals)

    if total_cohort_size == 1:
        if k == 1:
            val = round(sorted_vals[0], 2)
            return PercentileResult(numeric_hours=val, is_censored=False, display=f"{val:.1f}h")
        else:
            return PercentileResult(
                numeric_hours=None, is_censored=True, display="> measurement window"
            )

    rank = (percentile / 100.0) * (total_cohort_size - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))

    # If the percentile rank touches or falls within the censored cohort
    if upper_idx >= k:
        return PercentileResult(
            numeric_hours=None, is_censored=True, display="> measurement window"
        )

    fraction = rank - lower_idx
    if lower_idx == upper_idx:
        val = round(sorted_vals[lower_idx], 2)
        return PercentileResult(numeric_hours=val, is_censored=False, display=f"{val:.1f}h")

    interpolated = sorted_vals[lower_idx] + fraction * (
        sorted_vals[upper_idx] - sorted_vals[lower_idx]
    )
    res_val = round(interpolated, 2)
    return PercentileResult(numeric_hours=res_val, is_censored=False, display=f"{res_val:.1f}h")


class MetricsEngine:
    """Calculates scientifically rigorous, reproducible platform performance metrics."""

    def __init__(self, deadline_config: Optional[DeadlineConfig] = None) -> None:
        self.deadline_config = deadline_config or DeadlineConfig()

    def compute_performance_metrics(
        self,
        records: List[RequestRecord],
        invalid_records_count: int = 0,
        as_of: Optional[datetime] = None,
        observations: Optional[Dict[str, ObservationRecord]] = None,
    ) -> PerformanceMetrics:
        """Compute performance metrics for a collection of records.

        Parameters
        ----------
        records : List[RequestRecord]
            Request log records to evaluate.
        invalid_records_count : int
            Count of malformed or quarantined records.
        as_of : Optional[datetime]
            Measurement cutoff. Immature requests whose deadline has not passed are excluded.
        observations : Optional[Dict[str, ObservationRecord]]
            External verification observations keyed by request_id (Half 2 shared contract).
        """
        now_utc = datetime.now(timezone.utc)
        cutoff = as_of or now_utc
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

        obs_map = observations or {}

        total_requests = len(records)
        takedowns = [r for r in records if r.request_type == RequestType.TAKEDOWN]
        data_requests = [r for r in records if r.request_type == RequestType.DATA_REQUEST]

        # 1. Procedural completeness
        incomplete_police_count = sum(1 for r in records if not r.is_complete_request)

        # 2. Takedown Eligibility and Cohort Maturity
        eligible_takedowns = [t for t in takedowns if t.is_complete_request]
        num_eligible_takedowns = len(eligible_takedowns)

        # Apply measurement cutoff and maturity rule:
        # A request enters the mature cohort if:
        # - It was verified removed before cutoff, OR
        # - It was claimed removed before cutoff (and not contradicted by the verifier), OR
        # - It was rejected before cutoff, OR
        # - Its applicable deadline has elapsed by cutoff.
        mature_takedowns: List[RequestRecord] = []
        for r in eligible_takedowns:
            deadline_h = self.deadline_config.get_deadline_hours(r)
            deadline_dt = r.sent_at + timedelta(hours=deadline_h)

            # Removed before cutoff (verified removal, or an uncontradicted platform claim)
            is_early_completed = (
                effective_removal_time(r, obs_map.get(r.request_id), cutoff) is not None
            )
            is_rejected = r.status == RequestStatus.REJECTED and r.sent_at <= cutoff
            is_deadline_passed = deadline_dt <= cutoff

            if is_early_completed or is_rejected or is_deadline_passed:
                mature_takedowns.append(r)

        num_mature_takedowns = len(mature_takedowns)

        completed_takedowns: List[RequestRecord] = []
        removal_durations_hours: List[float] = []
        on_time_takedowns_count = 0
        still_available_takedowns_count = 0
        rejections_count = 0

        # Integrity tracking
        false_removal_claims_count = 0
        observed_reuploads_count = 0
        verified_takedowns_count = 0

        claimed_count = 0
        for r in mature_takedowns:
            obs = obs_map.get(r.request_id)

            if r.status == RequestStatus.REJECTED:
                # Platform rejections reported separately; not equated with removal or availability
                rejections_count += 1
                continue

            # Verification rules live in takedown_tracking.integrity.rules (Half 2).
            if has_claim(r):
                claimed_count += 1
                if is_false_claim(r, obs, as_of=cutoff):
                    # Platform claimed removal while the verifier still saw the content up
                    false_removal_claims_count += 1

            if is_reupload_within_window(obs, cutoff):
                observed_reuploads_count += 1

            # Verified removal beats the platform's claim; a contradicted claim counts as not removed.
            # Platform acknowledgement is explicitly NOT considered removal.
            removal_at = effective_removal_time(r, obs, cutoff)
            if removal_at is None:
                still_available_takedowns_count += 1
                continue

            if is_verified_removal(obs, cutoff):
                verified_takedowns_count += 1

            completed_takedowns.append(r)
            dur_hours = max(0.0, (removal_at - r.sent_at).total_seconds() / 3600.0)
            removal_durations_hours.append(dur_hours)

            deadline = self.deadline_config.get_deadline_hours(r)
            if dur_hours <= deadline:
                on_time_takedowns_count += 1

        # Censored percentiles over the mature removal cohort (completed removals + still available unremoved)
        speed_cohort_size = len(removal_durations_hours) + still_available_takedowns_count
        median_res = calculate_censored_percentile(removal_durations_hours, speed_cohort_size, 50.0)
        p90_res = calculate_censored_percentile(removal_durations_hours, speed_cohort_size, 90.0)

        # Deadline compliance rate: evaluated over mature actionable takedowns (excluding rejections)
        actionable_mature = num_mature_takedowns - rejections_count
        deadline_compliance_pct: Optional[float] = None
        if actionable_mature > 0:
            deadline_compliance_pct = round(
                (on_time_takedowns_count / actionable_mature) * 100.0, 2
            )

        still_available_pct = (
            round((still_available_takedowns_count / actionable_mature) * 100.0, 2)
            if actionable_mature > 0
            else 0.0
        )

        rejection_rate_pct = (
            round((rejections_count / num_mature_takedowns) * 100.0, 2)
            if num_mature_takedowns > 0
            else 0.0
        )

        # Integrity rates (claimed_count counted in the loop above)
        false_claim_rate_pct: Optional[float] = None
        if claimed_count > 0:
            false_claim_rate_pct = round(
                (false_removal_claims_count / claimed_count) * 100.0, 2
            )

        verification_coverage_pct: Optional[float] = None
        if len(mature_takedowns) > 0:
            verified_checks = sum(1 for r in mature_takedowns if r.request_id in obs_map)
            verification_coverage_pct = round(
                (verified_checks / len(mature_takedowns)) * 100.0, 2
            )

        # 3. Data Requests Performance with as_of Cohort Maturity
        eligible_data_requests = [d for d in data_requests if d.is_complete_request]
        mature_data_requests: List[RequestRecord] = []
        for d in eligible_data_requests:
            deadline_h = self.deadline_config.get_deadline_hours(d)
            deadline_dt = d.sent_at + timedelta(hours=deadline_h)

            is_early_provided = (
                d.status == RequestStatus.DATA_PROVIDED
                and d.data_provided_at is not None
                and d.data_provided_at <= cutoff
            )
            is_rejected = d.status == RequestStatus.REJECTED and d.sent_at <= cutoff
            is_deadline_passed = deadline_dt <= cutoff

            if is_early_provided or is_rejected or is_deadline_passed:
                mature_data_requests.append(d)

        num_mature_data = len(mature_data_requests)
        data_response_durations_hours: List[float] = []
        data_on_time_count = 0
        data_rejections_count = 0

        for d in mature_data_requests:
            if d.status == RequestStatus.REJECTED:
                data_rejections_count += 1
                continue

            if (
                d.status == RequestStatus.DATA_PROVIDED
                and d.data_provided_at is not None
                and d.data_provided_at <= cutoff
            ):
                dur_hours = max(0.0, (d.data_provided_at - d.sent_at).total_seconds() / 3600.0)
                data_response_durations_hours.append(dur_hours)
                deadline = self.deadline_config.get_deadline_hours(d)
                if dur_hours <= deadline:
                    data_on_time_count += 1

        median_data_hours = (
            calculate_percentile(data_response_durations_hours, 50.0)
            if data_response_durations_hours
            else None
        )
        p90_data_hours = (
            calculate_percentile(data_response_durations_hours, 90.0)
            if data_response_durations_hours
            else None
        )

        data_actionable_mature = num_mature_data - data_rejections_count
        data_deadline_compliance_pct: Optional[float] = None
        if data_actionable_mature > 0:
            data_deadline_compliance_pct = round(
                (data_on_time_count / data_actionable_mature) * 100.0, 2
            )

        return PerformanceMetrics(
            total_requests=total_requests,
            eligible_takedowns=num_eligible_takedowns,
            mature_takedowns=num_mature_takedowns,
            completed_takedowns=len(completed_takedowns),
            still_available_takedowns=still_available_takedowns_count,
            rejections_count=rejections_count,
            rejection_rate_pct=rejection_rate_pct,
            median_removal_hours=median_res.numeric_hours,
            median_removal_censored=median_res.is_censored,
            median_removal_display=median_res.display,
            p90_removal_hours=p90_res.numeric_hours,
            p90_removal_censored=p90_res.is_censored,
            p90_removal_display=p90_res.display,
            deadline_compliance_rate_pct=deadline_compliance_pct,
            still_available_rate_pct=still_available_pct,
            total_data_requests=len(data_requests),
            mature_data_requests=num_mature_data,
            completed_data_requests=len(data_response_durations_hours),
            median_data_response_hours=median_data_hours,
            p90_data_response_hours=p90_data_hours,
            data_deadline_compliance_rate_pct=data_deadline_compliance_pct,
            incomplete_police_requests_count=incomplete_police_count,
            invalid_records_count=invalid_records_count,
            false_removal_claims_count=false_removal_claims_count,
            false_removal_claim_rate_pct=false_claim_rate_pct,
            observed_reuploads_count=observed_reuploads_count,
            verified_takedowns_count=verified_takedowns_count,
            verification_coverage_pct=verification_coverage_pct,
        )

    def compute_by_platform(
        self,
        records: List[RequestRecord],
        as_of: Optional[datetime] = None,
        observations: Optional[Dict[str, ObservationRecord]] = None,
    ) -> Dict[Platform, PerformanceMetrics]:
        """Compute metrics disaggregated by social media platform."""
        groups: Dict[Platform, List[RequestRecord]] = {p: [] for p in Platform}
        for r in records:
            groups[r.platform].append(r)

        return {
            platform: self.compute_performance_metrics(
                group_records, as_of=as_of, observations=observations
            )
            for platform, group_records in groups.items()
        }

    def compute_by_category(
        self,
        records: List[RequestRecord],
        as_of: Optional[datetime] = None,
        observations: Optional[Dict[str, ObservationRecord]] = None,
    ) -> Dict[Category, PerformanceMetrics]:
        """Compute metrics disaggregated by incident category."""
        groups: Dict[Category, List[RequestRecord]] = {c: [] for c in Category}
        for r in records:
            groups[r.category].append(r)

        return {
            category: self.compute_performance_metrics(
                group_records, as_of=as_of, observations=observations
            )
            for category, group_records in groups.items()
        }
