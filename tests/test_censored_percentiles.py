"""Regression tests for survival percentile calculations and right-censoring (review item 4)."""

from datetime import datetime, timedelta, timezone
import json
import pytest

from takedown_tracking.metrics.engine import MetricsEngine, calculate_censored_percentile
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)


class TestCensoredPercentiles:
    """Validate right-censored percentile mathematics and elimination of survivorship bias."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_reviewer_exact_platform_a_vs_platform_b_example(self, base_time: datetime) -> None:
        """Exact reviewer test case: Platform A (20 removed in 10h) vs Platform B (10 in 1h, 10 unremoved).

        Platform B must NOT be reported with median = 1.0 hour.
        """
        engine = MetricsEngine()
        cutoff = base_time + timedelta(hours=100.0)

        # Platform A: 20 eligible requests, all removed in 10 hours
        recs_a = [
            RequestRecord(
                request_id=f"PA-{i}",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=10.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            )
            for i in range(20)
        ]
        metrics_a = engine.compute_performance_metrics(recs_a, as_of=cutoff)
        assert metrics_a.completed_takedowns == 20
        assert metrics_a.still_available_takedowns == 0
        assert metrics_a.median_removal_hours == 10.0
        assert metrics_a.median_removal_censored is False
        assert metrics_a.median_removal_display == "10.0h"

        # Platform B: 20 eligible requests, 10 removed in 1 hour, 10 unresolved past deadline
        recs_b = []
        for i in range(10):
            recs_b.append(
                RequestRecord(
                    request_id=f"PB-REMOVED-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_B,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time + timedelta(hours=1.0),
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )
        for i in range(10):
            recs_b.append(
                RequestRecord(
                    request_id=f"PB-UNRESOLVED-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_B,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.PENDING,
                )
            )

        metrics_b = engine.compute_performance_metrics(recs_b, as_of=cutoff)
        assert metrics_b.completed_takedowns == 10
        assert metrics_b.still_available_takedowns == 10
        # CRITICAL: Platform B's median MUST NOT be reported as 1.0h!
        assert metrics_b.median_removal_hours is not 1.0
        assert metrics_b.median_removal_hours is None
        assert metrics_b.median_removal_censored is True
        assert "> measurement window" in metrics_b.median_removal_display

    def test_all_requests_removed(self) -> None:
        """When all requests in cohort are removed (k = N), percentiles are finite and uncensored."""
        durations = [2.0, 4.0, 6.0, 8.0, 10.0]
        res50 = calculate_censored_percentile(durations, total_cohort_size=5, percentile=50.0)
        assert res50.is_censored is False
        assert res50.numeric_hours == 6.0

        res90 = calculate_censored_percentile(durations, total_cohort_size=5, percentile=90.0)
        assert res90.is_censored is False
        assert res90.numeric_hours == 9.2

    def test_half_removed_and_half_unresolved(self) -> None:
        """When half are removed and half unresolved (k = N/2), median is censored."""
        durations = [2.0, 4.0, 6.0, 8.0, 10.0]
        # Total cohort = 10, finite = 5
        res = calculate_censored_percentile(durations, total_cohort_size=10, percentile=50.0)
        assert res.is_censored is True
        assert res.numeric_hours is None
        assert res.display == "> measurement window"

    def test_more_than_half_unresolved(self) -> None:
        """When more than half are unresolved (k < N/2), median is censored."""
        durations = [1.0, 2.0]
        res = calculate_censored_percentile(durations, total_cohort_size=10, percentile=50.0)
        assert res.is_censored is True
        assert res.numeric_hours is None

    def test_fewer_than_half_unresolved_median_finite_p90_censored(self) -> None:
        """When 8 out of 10 are removed (k = 8, N = 10), median is finite, but p90 is censored."""
        durations = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        # rank for median = 0.5 * 9 = 4.5 -> within finite 8
        res_med = calculate_censored_percentile(durations, total_cohort_size=10, percentile=50.0)
        assert res_med.is_censored is False
        assert res_med.numeric_hours == 5.5

        # rank for p90 = 0.9 * 9 = 8.1 -> upper index is 9 >= k (8), so p90 is censored
        res_p90 = calculate_censored_percentile(durations, total_cohort_size=10, percentile=90.0)
        assert res_p90.is_censored is True
        assert res_p90.numeric_hours is None

    def test_empty_and_singleton_cohorts(self) -> None:
        """Empty and single-element cohorts must be handled safely."""
        # Empty
        res_empty = calculate_censored_percentile([], total_cohort_size=0, percentile=50.0)
        assert res_empty.is_censored is False
        assert res_empty.numeric_hours is None
        assert res_empty.display == "N/A"

        # Singleton: 1 removed
        res_single_rem = calculate_censored_percentile([4.5], total_cohort_size=1, percentile=50.0)
        assert res_single_rem.is_censored is False
        assert res_single_rem.numeric_hours == 4.5
        assert res_single_rem.display == "4.5h"

        # Singleton: 1 unremoved
        res_single_unrem = calculate_censored_percentile([], total_cohort_size=1, percentile=50.0)
        assert res_single_unrem.is_censored is True
        assert res_single_unrem.numeric_hours is None
        assert res_single_unrem.display == "> measurement window"

    def test_json_serialization_never_contains_infinity_or_nan(self, base_time: datetime) -> None:
        """Metrics dictionary serialized to JSON must NEVER produce Infinity or NaN."""
        engine = MetricsEngine()
        cutoff = base_time + timedelta(hours=100.0)

        # Dataset with all unresolved requests
        records = [
            RequestRecord(
                request_id="R-CENSORED-1",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            )
        ]

        metrics = engine.compute_performance_metrics(records, as_of=cutoff)
        m_dict = metrics.to_dict()

        json_str = json.dumps(m_dict)
        assert "Infinity" not in json_str
        assert "NaN" not in json_str
        # Verify valid JSON parse back
        parsed = json.loads(json_str)
        assert parsed["median_removal_hours"] is None
        assert parsed["median_removal_censored"] is True
        assert parsed["median_removal_display"] == "> measurement window"
