"""Regression tests for 2026 IT Rules statutory deadlines and strictest conflict resolution."""

from datetime import datetime, timezone
import pytest

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.models.request import (
    Category,
    LegalRoute,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)


class TestDeadlines2026:
    """Validate 2026 IT Rules statutory timelines (Rule 3(1)(d), Rule 3(2)(b)) and priority resolution."""

    @pytest.fixture
    def base_time(self) -> datetime:
        return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    @pytest.fixture
    def config(self) -> DeadlineConfig:
        return DeadlineConfig()

    def test_rule_3_1_d_court_order_or_govt_intimation_deadline(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Specified court orders or authorized government intimations under Rule 3(1)(d) require 3.0h."""
        record = RequestRecord(
            request_id="REQ-31D",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.TERRORISM,
            priority=Priority.ROUTINE,
            legal_route=LegalRoute.COURT_ORDER_OR_GOVT_INTIMATION,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        # min(Route 3h, Priority 36h, Category 24h) = 3.0h
        assert config.get_deadline_hours(record) == 3.0

    def test_rule_3_2_b_qualifying_complaints_deadline(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Qualifying Rule 3(2)(b) complaints (CSAM, NCII, Impersonation) require 2.0h."""
        for qualifying_cat in [Category.CSAM, Category.NON_CONSENSUAL_IMAGERY, Category.IMPERSONATION]:
            record = RequestRecord(
                request_id=f"REQ-32B-{qualifying_cat.value}",
                is_synthetic=True,
                request_type=RequestType.TAKEDOWN,
                platform=Platform.PLATFORM_A,
                category=qualifying_cat,
                priority=Priority.ROUTINE,
                legal_route=LegalRoute.GRIEVANCE_COMPLAINT_3_2_B,
                sent_at=base_time,
                police_unit_id="U1",
                is_complete_request=True,
                status=RequestStatus.PENDING,
            )
            # min(Route 2h, Priority 36h, Category 24h) = 2.0h
            assert config.get_deadline_hours(record) == 2.0

    def test_rule_3_2_b_does_not_apply_to_non_qualifying_categories(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Rule 3(2)(b) 2-hour deadline must NOT apply to non-qualifying categories (e.g. HateSpeech)."""
        record = RequestRecord(
            request_id="REQ-NON-QUALIFYING",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.HATE_SPEECH,
            priority=Priority.ROUTINE,
            legal_route=LegalRoute.GRIEVANCE_COMPLAINT_3_2_B,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        # Category HateSpeech = 36h, Routine priority = 36h; 2h grievance rule does not apply
        assert config.get_deadline_hours(record) == 36.0

    def test_ncii_without_rule_3_2_b_route_does_not_automatically_get_2h(self, base_time: datetime, config: DeadlineConfig) -> None:
        """NCII request under routine inquiry or unspecified route gets category default 24h, not 2h."""
        record = RequestRecord(
            request_id="REQ-NCII-ROUTINE",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.NON_CONSENSUAL_IMAGERY,
            priority=Priority.ROUTINE,
            legal_route=LegalRoute.ROUTINE_POLICE_INQUIRY,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        # min(Routine route 36h, Routine priority 36h, NCII category 24h) = 24.0h
        assert config.get_deadline_hours(record) == 24.0

    def test_strictest_resolution_urgent_priority_not_overridden_by_category(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Urgent priority (3h) must never be overridden by a looser category deadline (e.g. 36h)."""
        record = RequestRecord(
            request_id="REQ-URGENT-HATE",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.HATE_SPEECH,  # Category SLA = 36h
            priority=Priority.URGENT,       # Priority SLA = 3h
            legal_route=None,               # Missing route
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        # Strictest resolution: min(Category 36h, Priority 3h) = 3.0h
        assert config.get_deadline_hours(record) == 3.0

    def test_reviewer_scenario_three_way_resolution(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Reviewer scenario: Category=36h, Priority=3h, Legal Route=3h -> 3.0h."""
        record = RequestRecord(
            request_id="REQ-THREE-WAY",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.FINANCIAL_FRAUD,  # 36h
            priority=Priority.URGENT,          # 3h
            legal_route=LegalRoute.COURT_ORDER_OR_GOVT_INTIMATION,  # 3h
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        assert config.get_deadline_hours(record) == 3.0

    def test_emergency_life_threat_resolution(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Emergency life threat priority enforces 2.0h across any category."""
        record = RequestRecord(
            request_id="REQ-EMERGENCY",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.DEFAMATION,  # 36h
            priority=Priority.EMERGENCY_LIFE_THREAT,  # 2h
            legal_route=None,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        assert config.get_deadline_hours(record) == 2.0

    def test_data_requests_isolated_from_takedown_slas(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Data assistance requests are governed strictly by 72h data SLA, never content removal SLAs."""
        record = RequestRecord(
            request_id="REQ-DATA",
            is_synthetic=True,
            request_type=RequestType.DATA_REQUEST,
            platform=Platform.PLATFORM_A,
            category=Category.CSAM,  # Even with CSAM category
            priority=Priority.EMERGENCY_LIFE_THREAT,  # Even with emergency priority
            legal_route=LegalRoute.DATA_ASSISTANCE_ORDER,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        assert config.get_deadline_hours(record) == 72.0

    def test_missing_or_ambiguous_legal_route_treatment(self, base_time: datetime, config: DeadlineConfig) -> None:
        """Ambiguous or None route relies on category and priority without fabricating statutory routes."""
        record = RequestRecord(
            request_id="REQ-AMBIGUOUS",
            is_synthetic=True,
            request_type=RequestType.TAKEDOWN,
            platform=Platform.PLATFORM_A,
            category=Category.OTHER,
            priority=Priority.ROUTINE,
            legal_route=LegalRoute.OTHER,
            sent_at=base_time,
            police_unit_id="U1",
            is_complete_request=True,
            status=RequestStatus.PENDING,
        )
        assert config.get_deadline_hours(record) == 36.0
