"""Regression tests for shared verification observation contract with Half 2 (review item 7)."""

from datetime import datetime, timedelta, timezone
import pytest

from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.observation import CheckError, ObservationRecord, ObservationStatus
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.privacy.filter import PrivacyFilter


class TestObservationsContract:
    """Validate verification observation domain models, false removal claims, and integrity metrics."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_observation_record_serialization(self, base_time: datetime) -> None:
        """ObservationRecord domain model must serialize and deserialize cleanly."""
        obs = ObservationRecord(
            request_id="REQ-TEST-001",
            observation_status=ObservationStatus.CONFIRMED_REMOVED,
            first_checked_at=base_time,
            last_seen_up_at=base_time + timedelta(hours=1.75),
            verified_removed_at=base_time + timedelta(hours=2.0),
            last_checked_at=base_time + timedelta(hours=2.25),
            verifier_agent_id="verifier-unit-01",
        )
        d = obs.to_dict()
        assert d["request_id"] == "REQ-TEST-001"
        assert d["observation_status"] == "confirmed_removed"

        reloaded = ObservationRecord.from_dict(d)
        assert reloaded.request_id == obs.request_id
        assert reloaded.observation_status == obs.observation_status
        assert reloaded.verified_removed_at == obs.verified_removed_at

    def test_verified_removal_overrides_platform_claim(self, base_time: datetime) -> None:
        """Metrics engine uses verified_removed_at when an independent verification exists."""
        req = RequestRecord(
            request_id="REQ-VERIFIED",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,
            priority=Priority.ROUTINE,
            sent_at=base_time,
            claimed_removal_at=base_time + timedelta(hours=10.0),  # Platform claimed 10h
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.CLAIMED_REMOVED,
        )

        # Independent observation observed removal earlier at 4h
        obs = ObservationRecord(
            request_id="REQ-VERIFIED",
            observation_status=ObservationStatus.CONFIRMED_REMOVED,
            last_checked_at=base_time + timedelta(hours=5.0),
            verifier_agent_id="verifier-agent-01",
            verified_removed_at=base_time + timedelta(hours=4.0),
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([req], observations={"REQ-VERIFIED": obs})

        assert metrics.verified_takedowns_count == 1
        assert metrics.median_removal_hours == 4.0  # Uses 4h verified duration, NOT 10h claim!

    def test_false_removal_claim_detected(self, base_time: datetime) -> None:
        """If observation establishes content was available after platform claimed removal, flag as false claim."""
        req = RequestRecord(
            request_id="REQ-FALSE-CLAIM",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,
            priority=Priority.ROUTINE,
            sent_at=base_time,
            claimed_removal_at=base_time + timedelta(hours=2.0),  # Platform falsely claimed removed at 2h
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.CLAIMED_REMOVED,
        )

        # Observation at 6h confirmed content was STILL AVAILABLE
        obs = ObservationRecord(
            request_id="REQ-FALSE-CLAIM",
            observation_status=ObservationStatus.CONFIRMED_AVAILABLE,
            last_checked_at=base_time + timedelta(hours=6.0),
            verifier_agent_id="verifier-agent-01",
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([req], observations={"REQ-FALSE-CLAIM": obs})

        assert metrics.false_removal_claims_count == 1
        assert metrics.false_removal_claim_rate_pct == 100.0
        assert metrics.completed_takedowns == 0
        assert metrics.still_available_takedowns == 1
        assert metrics.deadline_compliance_rate_pct == 0.0

    def test_uncertain_check_not_equated_with_successful_removal(self, base_time: datetime) -> None:
        """Uncertain or failed checks must NOT be equated with successful removal."""
        req = RequestRecord(
            request_id="REQ-UNCERTAIN",
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

        obs = ObservationRecord(
            request_id="REQ-UNCERTAIN",
            observation_status=ObservationStatus.UNCERTAIN,
            last_checked_at=base_time + timedelta(hours=5.0),
            verifier_agent_id="verifier-agent-01",
            check_error=CheckError.LOGIN_WALL,
        )

        engine = MetricsEngine()
        metrics = engine.compute_performance_metrics([req], observations={"REQ-UNCERTAIN": obs})

        # Must remain unremoved/still available
        assert metrics.completed_takedowns == 0
        assert metrics.verified_takedowns_count == 0
        assert metrics.still_available_takedowns == 1

    def test_per_platform_integrity_privacy_suppression(self, base_time: datetime) -> None:
        """Per-platform integrity metrics must be suppressed when cohort is below k-threshold."""
        records = []
        obs_map = {}
        for i in range(12):
            req_id = f"SYN-PA-{i}"
            records.append(
                RequestRecord(
                    request_id=req_id,
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
                )
            )
            obs_map[req_id] = ObservationRecord(
                request_id=req_id,
                observation_status=ObservationStatus.CONFIRMED_REMOVED,
                last_checked_at=base_time + timedelta(hours=6.0),
                verifier_agent_id="verifier-simulator",
                verified_removed_at=base_time + timedelta(hours=5.0),
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records, observations=obs_map)
        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")

        # 12 >= 10, so integrity metrics are published
        assert entry.verified_takedowns_count == 12
        assert entry.false_removal_claims_count == 0

    def test_verifier_wins_over_pending_or_acknowledged_platform_status(self, base_time: datetime) -> None:
        """When an observation is CONFIRMED_REMOVED, it overrides pending/acknowledged status and counts as verified removal."""
        # Platform status is ACKNOWLEDGED (platform has not marked claimed_removed)
        req = RequestRecord(
            request_id="REQ-VERIFIER-WINS",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,  # 24h deadline
            priority=Priority.ROUTINE,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.ACKNOWLEDGED,
        )

        # But external verifier confirmed content was taken down in 3 hours
        obs = ObservationRecord(
            request_id="REQ-VERIFIER-WINS",
            observation_status=ObservationStatus.CONFIRMED_REMOVED,
            last_checked_at=base_time + timedelta(hours=4.0),
            verifier_agent_id="verifier-unit-01",
            verified_removed_at=base_time + timedelta(hours=3.0),
        )

        engine = MetricsEngine()
        cutoff = base_time + timedelta(hours=5.0)
        metrics = engine.compute_performance_metrics([req], as_of=cutoff, observations={"REQ-VERIFIER-WINS": obs})

        assert metrics.mature_takedowns == 1
        assert metrics.completed_takedowns == 1
        assert metrics.verified_takedowns_count == 1
        assert metrics.still_available_takedowns == 0
        assert metrics.median_removal_hours == 3.0
        assert metrics.deadline_compliance_rate_pct == 100.0
