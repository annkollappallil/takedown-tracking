"""Comprehensive metric calculation engine for takedown and data requests."""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.models.metrics import PerformanceMetrics
from takedown_tracking.models.request import (
    Category,
    Platform,
    RequestRecord,
    RequestStatus,
    RequestType,
)


def calculate_percentile(values: List[float], percentile: float) -> Optional[float]:
    """Calculate percentile using linear interpolation between closest ranks.

    Compatible with standard NIST / numpy percentile method.
    Percentile must be between 0.0 and 100.0.
    """
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


class MetricsEngine:
    """Calculates scientifically rigorous, reproducible platform performance metrics."""

    def __init__(self, deadline_config: Optional[DeadlineConfig] = None) -> None:
        self.deadline_config = deadline_config or DeadlineConfig()

    def compute_performance_metrics(
        self,
        records: List[RequestRecord],
        invalid_records_count: int = 0,
    ) -> PerformanceMetrics:
        """Compute performance metrics for a collection of records."""
        total_requests = len(records)
        takedowns = [r for r in records if r.request_type == RequestType.TAKEDOWN]
        data_requests = [r for r in records if r.request_type == RequestType.DATA_REQUEST]

        # 1. Procedural Completeness & Exclusions
        incomplete_police_count = sum(1 for r in records if not r.is_complete_request)

        # 2. Takedown Eligibility
        # Eligible takedowns exclude procedurally deficient requests to avoid penalizing
        # intermediaries for police documentation errors.
        eligible_takedowns = [t for t in takedowns if t.is_complete_request]
        num_eligible_takedowns = len(eligible_takedowns)

        completed_takedowns: List[RequestRecord] = []
        removal_durations_hours: List[float] = []
        on_time_takedowns_count = 0
        still_available_takedowns_count = 0

        for r in eligible_takedowns:
            is_claimed_removed = (
                r.status == RequestStatus.CLAIMED_REMOVED and r.claimed_removal_at is not None
            )

            if is_claimed_removed:
                completed_takedowns.append(r)
                assert r.claimed_removal_at is not None
                dur_hours = (r.claimed_removal_at - r.sent_at).total_seconds() / 3600.0
                dur_hours = max(0.0, dur_hours)
                removal_durations_hours.append(dur_hours)

                # Check deadline compliance
                deadline = self.deadline_config.get_deadline_hours(r)
                if dur_hours <= deadline:
                    on_time_takedowns_count += 1
            else:
                # Content not removed: PENDING, ACKNOWLEDGED, REJECTED, EXPIRED
                # Platform acknowledgement is explicitly NOT considered removal.
                still_available_takedowns_count += 1

        median_removal = (
            calculate_percentile(removal_durations_hours, 50.0)
            if removal_durations_hours
            else None
        )
        p90_removal = (
            calculate_percentile(removal_durations_hours, 90.0)
            if removal_durations_hours
            else None
        )

        deadline_compliance_pct: Optional[float] = None
        if num_eligible_takedowns > 0:
            deadline_compliance_pct = round(
                (on_time_takedowns_count / num_eligible_takedowns) * 100.0, 2
            )

        still_available_pct = (
            round((still_available_takedowns_count / num_eligible_takedowns) * 100.0, 2)
            if num_eligible_takedowns > 0
            else 0.0
        )

        # 3. Data Requests Performance
        eligible_data_requests = [d for d in data_requests if d.is_complete_request]
        data_response_durations_hours: List[float] = []
        data_on_time_count = 0

        for d in eligible_data_requests:
            if d.status == RequestStatus.DATA_PROVIDED and d.data_provided_at is not None:
                dur_hours = (d.data_provided_at - d.sent_at).total_seconds() / 3600.0
                dur_hours = max(0.0, dur_hours)
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

        data_deadline_compliance_pct: Optional[float] = None
        if eligible_data_requests:
            data_deadline_compliance_pct = round(
                (data_on_time_count / len(eligible_data_requests)) * 100.0, 2
            )

        return PerformanceMetrics(
            total_requests=total_requests,
            eligible_takedowns=num_eligible_takedowns,
            completed_takedowns=len(completed_takedowns),
            still_available_takedowns=still_available_takedowns_count,
            median_removal_hours=median_removal,
            p90_removal_hours=p90_removal,
            deadline_compliance_rate_pct=deadline_compliance_pct,
            still_available_rate_pct=still_available_pct,
            total_data_requests=len(data_requests),
            completed_data_requests=len(data_response_durations_hours),
            median_data_response_hours=median_data_hours,
            p90_data_response_hours=p90_data_hours,
            data_deadline_compliance_rate_pct=data_deadline_compliance_pct,
            incomplete_police_requests_count=incomplete_police_count,
            invalid_records_count=invalid_records_count,
        )

    def compute_by_platform(
        self, records: List[RequestRecord]
    ) -> Dict[Platform, PerformanceMetrics]:
        """Compute metrics disaggregated by social media platform."""
        groups: Dict[Platform, List[RequestRecord]] = {p: [] for p in Platform}
        for r in records:
            groups[r.platform].append(r)

        return {
            platform: self.compute_performance_metrics(group_records)
            for platform, group_records in groups.items()
        }

    def compute_by_category(
        self, records: List[RequestRecord]
    ) -> Dict[Category, PerformanceMetrics]:
        """Compute metrics disaggregated by incident category."""
        groups: Dict[Category, List[RequestRecord]] = {c: [] for c in Category}
        for r in records:
            groups[r.category].append(r)

        return {
            category: self.compute_performance_metrics(group_records)
            for category, group_records in groups.items()
        }
