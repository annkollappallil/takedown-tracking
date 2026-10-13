"""Unit tests for statistical metric calculations and SLA rules."""

from datetime import datetime, timedelta, timezone
import pytest

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine, calculate_percentile
from takedown_tracking.models.request import (
    Category,
    IncompletionReason,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)


class TestPercentileCalculations:
    """Test suite for calculate_percentile."""

    def test_empty_list(self) -> None:
        assert calculate_percentile([], 50.0) is None
        assert calculate_percentile([], 90.0) is None

    def test_single_element(self) -> None:
        assert calculate_percentile([42.5], 50.0) == 42.5
        assert calculate_percentile([42.5], 90.0) == 42.5

    def test_odd_count_median(self) -> None:
        # [10, 20, 30] -> median 20
        vals = [30.0, 10.0, 20.0]
        assert calculate_percentile(vals, 50.0) == 20.0

    def test_even_count_median(self) -> None:
        # [10, 20, 30, 40] -> median 25.0
        vals = [10.0, 20.0, 30.0, 40.0]
        assert calculate_percentile(vals, 50.0) == 25.0

    def test_p90_interpolation(self) -> None:
        # 10 values from 10 to 100
        vals = [float(i * 10) for i in range(1, 11)]
        # rank for p90 = 0.9 * 9 = 8.1 -> vals[8] + 0.1 * (vals[9] - vals[8]) = 90 + 0.1 * 10 = 91.0
        assert calculate_percentile(vals, 90.0) == 91.0

    def test_invalid_percentile_range(self) -> None:
        with pytest.raises(ValueError):
            calculate_percentile([1.0, 2.0], -1.0)
        with pytest.raises(ValueError):
            calculate_percentile([1.0, 2.0], 101.0)


class TestMetricsEngine:
    """Test suite for MetricsEngine calculations."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_perfect_compliance(self, base_time: datetime) -> None:
        """All takedowns completed well before the 24h/36h deadline."""
        records = []
        for i in range(5):
            records.append(
                RequestRecord(
                    request_id=f"SYN-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.META,
                    category=Category.CSAM,  # 24h deadline
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time + timedelta(hours=10.0),
                    police_unit_id="UNIT-01",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics(records)

        assert metrics.total_requests == 5
        assert metrics.eligible_takedowns == 5
        assert metrics.completed_takedowns == 5
        assert metrics.still_available_takedowns == 0
        assert metrics.median_removal_hours == 10.0
        assert metrics.p90_removal_hours == 10.0
        assert metrics.deadline_compliance_rate_pct == 100.0
        assert metrics.still_available_rate_pct == 0.0

    def test_unresolved_and_overdue_penalties(self, base_time: datetime) -> None:
        """Check that unresolved/pending requests count against compliance."""
        records = [
            # 1 on time removal (10h, deadline 24h)
            RequestRecord(
                request_id="R1",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=10.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            ),
            # 1 late removal (30h, deadline 24h)
            RequestRecord(
                request_id="R2",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=30.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            ),
            # 1 pending (still available)
            RequestRecord(
                request_id="R3",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            ),
            # 1 acknowledged only (still available! Not counted as removed)
            RequestRecord(
                request_id="R4",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                acknowledged_at=base_time + timedelta(hours=1.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.ACKNOWLEDGED,
            ),
        ]

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics(records)

        assert metrics.eligible_takedowns == 4
        assert metrics.completed_takedowns == 2
        assert metrics.still_available_takedowns == 2
        # Only 1 on time out of 4 eligible -> 25.0%
        assert metrics.deadline_compliance_rate_pct == 25.0
        # 2 out of 4 still available -> 50.0%
        assert metrics.still_available_rate_pct == 50.0
        # Right-censored median: 2 completed + 2 unremoved means median rank touches unremoved cases
        assert metrics.median_removal_censored is True
        assert metrics.median_removal_hours is None
        assert "> measurement window" in metrics.median_removal_display

    def test_incomplete_police_request_exclusion(self, base_time: datetime) -> None:
        """Incomplete requests must not be in eligible takedown denominator."""
        records = [
            # 1 valid completed takedown
            RequestRecord(
                request_id="R-VALID",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.HATE_SPEECH,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                claimed_removal_at=base_time + timedelta(hours=12.0),
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
            ),
            # 1 incomplete request (e.g. missing court order)
            RequestRecord(
                request_id="R-INCOMPLETE",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.HATE_SPEECH,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=False,
                incompletion_reason=IncompletionReason.MISSING_COURT_ORDER,
                status=RequestStatus.REJECTED,
            ),
        ]

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics(records)

        assert metrics.total_requests == 2
        # Denominator should be only 1 eligible takedown
        assert metrics.eligible_takedowns == 1
        assert metrics.deadline_compliance_rate_pct == 100.0
        assert metrics.incomplete_police_requests_count == 1

    def test_data_requests_metrics(self, base_time: datetime) -> None:
        """Data request SLA metrics test."""
        records = [
            RequestRecord(
                request_id="DR-1",
                is_synthetic=True,
                request_type=RequestType.DATA_REQUEST,
                platform=Platform.TELEGRAM,
                category=Category.OTHER,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                data_provided_at=base_time + timedelta(hours=48.0),  # < 72h
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.DATA_PROVIDED,
            ),
            RequestRecord(
                request_id="DR-2",
                is_synthetic=True,
                request_type=RequestType.DATA_REQUEST,
                platform=Platform.TELEGRAM,
                category=Category.OTHER,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                data_provided_at=base_time + timedelta(hours=96.0),  # > 72h
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.DATA_PROVIDED,
            ),
        ]

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics(records)

        assert metrics.total_data_requests == 2
        assert metrics.completed_data_requests == 2
        assert metrics.median_data_response_hours == 72.0
        assert metrics.data_deadline_compliance_rate_pct == 50.0

    def test_empty_dataset(self) -> None:
        """Empty list should produce nulls/zeros without exceptions."""
        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([])

        assert metrics.total_requests == 0
        assert metrics.eligible_takedowns == 0
        assert metrics.median_removal_hours is None
        assert metrics.p90_removal_hours is None
        assert metrics.deadline_compliance_rate_pct is None
        assert metrics.still_available_rate_pct == 0.0
