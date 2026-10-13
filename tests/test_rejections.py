"""Regression tests for separate platform rejection reporting (review item 13)."""

from datetime import datetime, timedelta, timezone
import pytest

from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.privacy.filter import PrivacyFilter


class TestRejectionsReporting:
    """Validate that platform rejections are tracked separately from still-available content."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_rejections_tracked_separately_not_as_still_available(self, base_time: datetime) -> None:
        """Rejected requests must NOT be counted as still-available content or successful removal."""
        records = [
            # 1 completed removal
            RequestRecord(
                request_id="R-REMOVED",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=5.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            ),
            # 1 platform rejection (e.g. overbroad request refused)
            RequestRecord(
                request_id="R-REJECTED",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.REJECTED,
                rejection_reason="PlatformPolicy: Overbroad jurisdictional scope.",
            ),
            # 1 unremoved still available
            RequestRecord(
                request_id="R-PENDING",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time - timedelta(hours=40.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            ),
        ]

        engine = MetricsEngine()
        cutoff = base_time + timedelta(hours=50.0)
        metrics = engine.compute_performance_metrics(records, as_of=cutoff)

        # 3 mature takedowns
        assert metrics.mature_takedowns == 3
        assert metrics.completed_takedowns == 1
        # Rejection tracked in its own separate count!
        assert metrics.rejections_count == 1
        # still_available is ONLY the 1 truly pending request, NOT the rejected one!
        assert metrics.still_available_takedowns == 1

    def test_rejections_excluded_from_speed_percentile_cohort(self, base_time: datetime) -> None:
        """Rejections must not distort removal speed percentiles."""
        records = [
            RequestRecord(
                request_id="R-REMOVED-1",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=6.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            ),
            RequestRecord(
                request_id="R-REJECTED-1",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.REJECTED,
            ),
        ]

        engine = MetricsEngine()
        cutoff = base_time + timedelta(hours=10.0)
        metrics = engine.compute_performance_metrics(records, as_of=cutoff)

        # Removal duration is only 6.0h for the completed removal
        assert metrics.completed_takedowns == 1
        assert metrics.still_available_takedowns == 0
        assert metrics.rejections_count == 1
        assert metrics.median_removal_hours == 6.0
        assert metrics.median_removal_censored is False

    def test_rejections_count_privacy_suppression(self, base_time: datetime) -> None:
        """Small rejections count (< k) must be redacted to None in public output."""
        records = [
            RequestRecord(
                request_id=f"R-{i}",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=2.0) if i >= 2 else None,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED if i >= 2 else RequestStatus.REJECTED,
            )
            for i in range(12)  # Total 12 >= 10, but rejections = 2 (< 10)
        ]

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)
        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")

        # Rejection count 2 is below k=10, must be redacted to None
        assert entry.rejections_count is None
