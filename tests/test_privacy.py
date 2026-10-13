"""Unit tests for the PrivacyFilter and k-anonymity suppression."""

from datetime import datetime, timezone
import pytest

from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.privacy.filter import PrivacyFilter


class TestPrivacyFilter:
    """Test suite for PrivacyFilter."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_k_anonymity_suppression_under_threshold(self, base_time: datetime) -> None:
        """Groups with fewer than k (e.g. 10) records must be suppressed."""
        # Create 5 records for YouTube (below k=10) and 15 records for Meta (above k=10)
        records = []
        for i in range(5):
            records.append(
                RequestRecord(
                    request_id=f"SYN-YT-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.YOUTUBE,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="UNIT-01",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        for i in range(15):
            records.append(
                RequestRecord(
                    request_id=f"SYN-META-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.META,
                    category=Category.HATE_SPEECH,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="UNIT-01",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)

        # Find YouTube entry
        yt_entry = next(p for p in scoreboard.platform_breakdown if p.platform == "YouTube")
        assert yt_entry.is_suppressed is True
        assert yt_entry.sample_size is None
        assert yt_entry.median_removal_hours is None
        assert yt_entry.p90_removal_hours is None
        assert yt_entry.deadline_compliance_rate_pct is None
        assert yt_entry.suppression_reason is not None
        assert "SmallGroupSuppression" in yt_entry.suppression_reason

        # Find Meta entry
        meta_entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Meta")
        assert meta_entry.is_suppressed is False
        assert meta_entry.sample_size == 15
        assert meta_entry.median_removal_hours is not None

    def test_zero_leakage_of_microdata(self, base_time: datetime) -> None:
        """Scoreboard dictionary must never expose request IDs, complaint IDs, URLs, or hashes."""
        records = [
            RequestRecord(
                request_id="SECRET-REQ-9999",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.EMERGENCY_LIFE_THREAT,
                sent_at=base_time,
                claimed_removal_at=base_time,
                police_unit_id="SECRET-POLICE-STATION-42",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
                content_url_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
        ]

        pfilter = PrivacyFilter(k_threshold=1)
        scoreboard_dict = pfilter.filter_and_publish(records).to_dict()

        # Convert entire dictionary to string and ensure sensitive identifiers are absent
        dict_str = str(scoreboard_dict)
        assert "SECRET-REQ-9999" not in dict_str
        assert "SECRET-POLICE-STATION-42" not in dict_str
        assert "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" not in dict_str
        assert "url" not in dict_str
        assert "request_id" not in dict_str

    def test_demonstration_mode_flag(self, base_time: datetime) -> None:
        """Synthetic records force is_demonstration_mode=True and attach demo disclaimer."""
        records = [
            RequestRecord(
                request_id="SYN-1",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            )
        ]

        pfilter = PrivacyFilter(is_demonstration_mode=False)  # Even if requested False
        scoreboard = pfilter.filter_and_publish(records)

        # Must automatically be True because records are synthetic
        assert scoreboard.metadata.is_demonstration_mode is True
        assert "DEMONSTRATION NOTICE" in scoreboard.metadata.disclaimer

    def test_configurable_k_threshold(self, base_time: datetime) -> None:
        """Custom k threshold (e.g. k=25) must be respected."""
        records = [
            RequestRecord(
                request_id=f"SYN-{i}",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.META,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            )
            for i in range(20)  # 20 records: above 10, but below 25
        ]

        # Under k=10, not suppressed
        sb10 = PrivacyFilter(k_threshold=10).filter_and_publish(records)
        assert next(p for p in sb10.platform_breakdown if p.platform == "Meta").is_suppressed is False

        # Under k=25, MUST be suppressed
        sb25 = PrivacyFilter(k_threshold=25).filter_and_publish(records)
        assert next(p for p in sb25.platform_breakdown if p.platform == "Meta").is_suppressed is True
