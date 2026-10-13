"""Regression tests for measurement cutoff (as_of) and cohort maturity rules (review item 5)."""

from datetime import datetime, timedelta, timezone
import pytest

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.request import (
    Category,
    LegalRoute,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)


class TestMeasurementCutoff:
    """Validate explicit as_of cutoff timestamp, cohort maturity, and determinism."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_request_whose_deadline_is_in_future_is_excluded_from_miss_denominator(self, base_time: datetime) -> None:
        """A pending request dispatched 30 mins before report cutoff is IMMATURE and must NOT count as missed deadline."""
        # Request sent at 11:30. Cutoff at 12:00 (30 mins later).
        # Applicable deadline is 24.0h (deadline timestamp is next day at 11:30).
        sent_at = base_time - timedelta(minutes=30)
        cutoff = base_time

        pending_immature = RequestRecord(
            request_id="REQ-IMMATURE",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,  # 24h
            priority=Priority.ROUTINE,
            sent_at=sent_at,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([pending_immature], as_of=cutoff)

        # Because deadline is in future, it has NOT matured
        assert metrics.eligible_takedowns == 1
        assert metrics.mature_takedowns == 0
        assert metrics.still_available_takedowns == 0
        assert metrics.deadline_compliance_rate_pct is None

    def test_request_whose_deadline_just_passed_becomes_mature_and_overdue(self, base_time: datetime) -> None:
        """When cutoff advances past the deadline, the pending request matures and counts as overdue non-compliance."""
        sent_at = base_time - timedelta(hours=25.0)  # Sent 25h ago
        cutoff = base_time  # Deadline was 24h ago, so deadline has elapsed

        pending_overdue = RequestRecord(
            request_id="REQ-MATURE-OVERDUE",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,  # 24h
            priority=Priority.ROUTINE,
            sent_at=sent_at,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([pending_overdue], as_of=cutoff)

        assert metrics.mature_takedowns == 1
        assert metrics.still_available_takedowns == 1
        # Missed deadline
        assert metrics.deadline_compliance_rate_pct == 0.0
        assert metrics.still_available_rate_pct == 100.0

    def test_early_completed_request_enters_cohort_immediately(self, base_time: datetime) -> None:
        """Request removed before deadline enters published cohort immediately with its verified outcome."""
        sent_at = base_time - timedelta(hours=5.0)
        cutoff = base_time

        completed_early = RequestRecord(
            request_id="REQ-EARLY-COMPLETED",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,  # 24h deadline (still 19h away)
            priority=Priority.ROUTINE,
            sent_at=sent_at,
            claimed_removal_at=sent_at + timedelta(hours=2.0),  # Removed in 2h
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.CLAIMED_REMOVED,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([completed_early], as_of=cutoff)

        assert metrics.mature_takedowns == 1
        assert metrics.completed_takedowns == 1
        assert metrics.median_removal_hours == 2.0
        assert metrics.deadline_compliance_rate_pct == 100.0

    def test_determinism_identical_results_under_same_as_of(self, base_time: datetime) -> None:
        """Deterministic reproducibility: identical datasets evaluated under the same as_of produce exact matches."""
        recs = [
            RequestRecord(
                request_id=f"R-{i}",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time - timedelta(hours=i * 5),
                claimed_removal_at=base_time - timedelta(hours=i * 5) + timedelta(hours=2.0) if i % 2 == 0 else None,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED if i % 2 == 0 else RequestStatus.PENDING,
            )
            for i in range(10)
        ]

        engine = MetricsEngine()
        m1 = engine.compute_performance_metrics(recs, as_of=base_time).to_dict()
        m2 = engine.compute_performance_metrics(recs, as_of=base_time).to_dict()

        assert m1 == m2

    def test_data_request_sent_before_cutoff_is_immature_and_not_penalized(self, base_time: datetime) -> None:
        """Data request sent 30 min before cutoff is immature (72h deadline) and does not penalize compliance."""
        cutoff = base_time

        # Data request 1: Sent 80h ago, provided at 20h (completed on-time)
        mature_req = RequestRecord(
            request_id="DATA-01-MATURE",
            is_synthetic=True,
            request_type=RequestType.DATA_REQUEST,
            platform=Platform.PLATFORM_A,
            category=Category.FINANCIAL_FRAUD,
            priority=Priority.ROUTINE,
            sent_at=cutoff - timedelta(hours=80.0),
            data_provided_at=cutoff - timedelta(hours=60.0),
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.DATA_PROVIDED,
        )

        # Data request 2: Sent 30 min ago, pending (71.5 hours remaining)
        immature_req = RequestRecord(
            request_id="DATA-02-IMMATURE",
            is_synthetic=True,
            request_type=RequestType.DATA_REQUEST,
            platform=Platform.PLATFORM_A,
            category=Category.FINANCIAL_FRAUD,
            priority=Priority.ROUTINE,
            sent_at=cutoff - timedelta(minutes=30),
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([mature_req, immature_req], as_of=cutoff)

        assert metrics.total_data_requests == 2
        assert metrics.mature_data_requests == 1
        assert metrics.completed_data_requests == 1
        # Crucial: Compliance is 100%, NOT 50%!
        assert metrics.data_deadline_compliance_rate_pct == 100.0

    def test_data_request_whose_deadline_elapsed_matures_and_fails_compliance(self, base_time: datetime) -> None:
        """Data request sent 75h ago without response has elapsed its 72h deadline and counts as non-compliant."""
        cutoff = base_time

        overdue_req = RequestRecord(
            request_id="DATA-OVERDUE",
            is_synthetic=True,
            request_type=RequestType.DATA_REQUEST,
            platform=Platform.PLATFORM_A,
            category=Category.FINANCIAL_FRAUD,
            priority=Priority.ROUTINE,
            sent_at=cutoff - timedelta(hours=75.0),
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([overdue_req], as_of=cutoff)

        assert metrics.total_data_requests == 1
        assert metrics.mature_data_requests == 1
        assert metrics.completed_data_requests == 0
        assert metrics.data_deadline_compliance_rate_pct == 0.0
