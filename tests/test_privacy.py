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
        """Groups with fewer than k (e.g. 10) records must be suppressed without count leakage."""
        records = []
        # 5 records for Platform A (below k=10)
        for i in range(5):
            records.append(
                RequestRecord(
                    request_id=f"SYN-A-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="UNIT-01",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        # 15 records for Platform B (above k=10)
        for i in range(15):
            records.append(
                RequestRecord(
                    request_id=f"SYN-B-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_B,
                    category=Category.CSAM,
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

        # Find Platform A entry (suppressed)
        pa_entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")
        assert pa_entry.is_suppressed is True
        assert pa_entry.sample_size is None
        assert pa_entry.median_removal_hours is None
        assert pa_entry.p90_removal_hours is None
        assert pa_entry.deadline_compliance_rate_pct is None
        assert pa_entry.suppression_reason is not None
        # Must NEVER leak the exact count in the suppression reason
        assert "5" not in pa_entry.suppression_reason
        assert "below the publication threshold" in pa_entry.suppression_reason

        # Find Platform B entry (published)
        pb_entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform B")
        assert pb_entry.is_suppressed is False
        assert pb_entry.sample_size == 15
        assert pb_entry.median_removal_hours is not None

    def test_zero_leakage_of_microdata(self, base_time: datetime) -> None:
        """Scoreboard dictionary must never expose request IDs, complaint IDs, URLs, or hashes."""
        records = [
            RequestRecord(
                request_id="SECRET-REQ-9999",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.EMERGENCY_LIFE_THREAT,
                sent_at=base_time,
                claimed_removal_at=base_time,
                police_unit_id="SECRET-POLICE-STATION-42",
                is_complete_request=True,
                status=RequestStatus.CLAIMED_REMOVED,
                content_url_hmac="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
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
                platform=Platform.PLATFORM_A,
                category=Category.CSAM,
                priority=Priority.ROUTINE,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            )
        ]

        pfilter = PrivacyFilter(is_demonstration_mode=False)
        scoreboard = pfilter.filter_and_publish(records)

        assert scoreboard.metadata.is_demonstration_mode is True
        assert "DEMONSTRATION NOTICE" in scoreboard.metadata.disclaimer

    def test_configurable_k_threshold(self, base_time: datetime) -> None:
        """Custom k threshold (e.g. k=25) must be respected."""
        records = [
            RequestRecord(
                request_id=f"SYN-{i}",
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
            for i in range(20)  # 20 records: above 10, but below 25
        ]

        # Under k=10, not suppressed
        sb10 = PrivacyFilter(k_threshold=10).filter_and_publish(records)
        assert next(p for p in sb10.platform_breakdown if p.platform == "Platform A").is_suppressed is False

        # Under k=25, MUST be suppressed
        sb25 = PrivacyFilter(k_threshold=25).filter_and_publish(records)
        assert next(p for p in sb25.platform_breakdown if p.platform == "Platform A").is_suppressed is True

    def test_per_metric_denominator_suppression(self, base_time: datetime) -> None:
        """Platform with overall count >= k but takedowns < k must suppress takedown metrics."""
        records = []
        # 12 data requests for Platform A (>= k=10)
        for i in range(12):
            records.append(
                RequestRecord(
                    request_id=f"SYN-DATA-{i}",
                    is_synthetic=True,
                    request_type=RequestType.DATA_REQUEST,
                    platform=Platform.PLATFORM_A,
                    category=Category.FINANCIAL_FRAUD,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    data_provided_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.DATA_PROVIDED,
                )
            )

        # 2 takedown requests for Platform A (< k=10)
        for i in range(2):
            records.append(
                RequestRecord(
                    request_id=f"SYN-TD-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)

        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")
        # Overall entry is NOT suppressed because sample_size == 14 >= 10
        assert entry.is_suppressed is False
        assert entry.sample_size == 14
        # Data requests metric is published (12 >= 10)
        assert entry.median_data_response_hours is not None
        # Takedown removal speed is suppressed because takedown denominator (2) < 10
        assert entry.median_removal_hours is None
        assert entry.median_removal_display == "[Suppressed < k]"
        assert entry.deadline_compliance_rate_pct is None

    def test_small_count_redaction_for_incomplete_and_rejections(self, base_time: datetime) -> None:
        """Incomplete police request count and rejections below k must be redacted to None."""
        records = []
        for i in range(15):
            # 2 incomplete requests, 13 complete
            is_comp = i >= 2
            records.append(
                RequestRecord(
                    request_id=f"SYN-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=is_comp,
                    status=RequestStatus.CLAIMED_REMOVED if is_comp else RequestStatus.REJECTED,
                )
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)
        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")

        # Total is 15 >= 10, but incomplete count is 2 (< 10)
        # Exact small count of 2 MUST be redacted to None to prevent microdata leak
        assert entry.incomplete_requests_count is None

    def test_scope_filtering_excludes_out_of_scope_categories_from_all_metrics(self, base_time: datetime) -> None:
        """Categories outside published_categories must not count in platform or overall scores."""
        records = []
        # 12 CSAM records (in-scope)
        for i in range(12):
            records.append(
                RequestRecord(
                    request_id=f"SYN-CSAM-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        # 25 HateSpeech records (out-of-scope by default)
        for i in range(25):
            records.append(
                RequestRecord(
                    request_id=f"SYN-HATE-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.HATE_SPEECH,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)
        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")

        # Crucial: Platform sample_size must be 12 (in-scope), NOT 37!
        assert entry.sample_size == 12
        assert scoreboard.overall_performance.total_requests == 12

    def test_speed_k_check_excludes_rejections_and_data_k_check_requires_completed(self, base_time: datetime) -> None:
        """Speed k-check must not count rejections, and data k-check must count completed data requests."""
        records = []
        # Platform A: 6 completed removals, 6 rejections -> total 12 mature takedowns
        # But speed cohort is only 6 (< 10), so removal speed must be suppressed!
        for i in range(6):
            records.append(
                RequestRecord(
                    request_id=f"SYN-CR-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    claimed_removal_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.CLAIMED_REMOVED,
                )
            )
        for i in range(6):
            records.append(
                RequestRecord(
                    request_id=f"SYN-REJ-{i}",
                    is_synthetic=True,
                    request_type=RequestType.TAKEDOWN,
                    platform=Platform.PLATFORM_A,
                    category=Category.CSAM,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.REJECTED,
                )
            )

        # Platform A: 12 total data requests, but only 2 completed
        for i in range(2):
            records.append(
                RequestRecord(
                    request_id=f"SYN-DATA-C-{i}",
                    is_synthetic=True,
                    request_type=RequestType.DATA_REQUEST,
                    platform=Platform.PLATFORM_A,
                    category=Category.FINANCIAL_FRAUD,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    data_provided_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.DATA_PROVIDED,
                )
            )
        for i in range(10):
            records.append(
                RequestRecord(
                    request_id=f"SYN-DATA-P-{i}",
                    is_synthetic=True,
                    request_type=RequestType.DATA_REQUEST,
                    platform=Platform.PLATFORM_A,
                    category=Category.FINANCIAL_FRAUD,
                    priority=Priority.ROUTINE,
                    sent_at=base_time,
                    police_unit_id="U1",
                    is_complete_request=True,
                    status=RequestStatus.PENDING,
                )
            )

        pfilter = PrivacyFilter(k_threshold=10)
        scoreboard = pfilter.filter_and_publish(records)
        entry = next(p for p in scoreboard.platform_breakdown if p.platform == "Platform A")

        # Total requests for Platform A = 24 >= 10 -> not suppressed overall
        assert entry.is_suppressed is False
        # Mature takedowns = 12 >= 10, so compliance is published
        assert entry.deadline_compliance_rate_pct is not None
        # But speed cohort size is 6 (< 10) -> removal speed MUST be suppressed!
        assert entry.median_removal_hours is None
        assert entry.median_removal_display == "[Suppressed < k]"
        # Total data requests = 12 >= 10, but completed = 2 (< 10) -> median data turnaround MUST be suppressed!
        assert entry.median_data_response_hours is None
