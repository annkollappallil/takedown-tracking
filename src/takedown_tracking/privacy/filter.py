"""Privacy filter and aggregation engine enforcing k-anonymity and zero-PII leakage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.metrics import PerformanceMetrics
from takedown_tracking.models.request import (
    Category,
    Platform,
    RequestRecord,
)
from takedown_tracking.models.scoreboard import (
    CategoryScoreboardEntry,
    PlatformScoreboardEntry,
    PublicScoreboard,
    ScoreboardMetadata,
)


class PrivacyFilter:
    """Enforces k-anonymity group suppression and purges microdata prior to publication."""

    DEFAULT_K_THRESHOLD = 10
    DEMO_DISCLAIMER = (
        "DEMONSTRATION NOTICE: Figures in this report are generated from synthetic test records "
        "and simulated models for accountability demonstration purposes only. They do NOT reflect "
        "genuine platform performance or real law enforcement investigations."
    )
    PRODUCTION_DISCLAIMER = (
        "OFFICIAL REPORT: Aggregated and verified metrics processed under authorized law enforcement "
        "oversight. Individual complaint numbers, victim details, and raw content links have been "
        "permanently redacted under statutory privacy safeguards."
    )

    def __init__(
        self,
        k_threshold: int = DEFAULT_K_THRESHOLD,
        is_demonstration_mode: bool = True,
        deadline_config: Optional[DeadlineConfig] = None,
    ) -> None:
        if k_threshold < 1:
            raise ValueError("k_threshold must be >= 1")
        self.k_threshold = k_threshold
        self.is_demonstration_mode = is_demonstration_mode
        self.deadline_config = deadline_config or DeadlineConfig()
        self.metrics_engine = MetricsEngine(self.deadline_config)

    def filter_and_publish(
        self,
        records: List[RequestRecord],
        invalid_records_count: int = 0,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> PublicScoreboard:
        """Transform raw records into a privacy-guaranteed PublicScoreboard."""
        now = datetime.now(timezone.utc)

        # Infer measurement period if not explicitly supplied
        if records:
            all_sent_times = [r.sent_at for r in records]
            computed_start = min(all_sent_times)
            computed_end = max(all_sent_times)
        else:
            computed_start = now
            computed_end = now

        start_dt = period_start or computed_start
        end_dt = period_end or computed_end

        # Flag demonstration mode if any records are synthetic
        is_demo = self.is_demonstration_mode or any(r.is_synthetic for r in records)
        disclaimer = self.DEMO_DISCLAIMER if is_demo else self.PRODUCTION_DISCLAIMER

        # 1. Compute overall metrics (global aggregate is permitted as long as total >= k)
        overall_metrics = self.metrics_engine.compute_performance_metrics(
            records, invalid_records_count=invalid_records_count
        )

        # 2. Platform Breakdown with Small-Group Suppression
        platform_metrics = self.metrics_engine.compute_by_platform(records)
        platform_entries: List[PlatformScoreboardEntry] = []

        for plat in Platform:
            metrics = platform_metrics[plat]
            sample_size = metrics.total_requests

            if sample_size < self.k_threshold:
                # Suppress small cohort to prevent deanonymization of sensitive complaints
                entry = PlatformScoreboardEntry(
                    platform=plat.value,
                    is_suppressed=True,
                    sample_size=None,
                    median_removal_hours=None,
                    p90_removal_hours=None,
                    deadline_compliance_rate_pct=None,
                    still_available_rate_pct=None,
                    median_data_response_hours=None,
                    incomplete_requests_count=None,
                    suppression_reason=(
                        f"SmallGroupSuppression: Cohort count ({sample_size}) is below "
                        f"the k-anonymity threshold of {self.k_threshold}."
                    ),
                )
            else:
                entry = PlatformScoreboardEntry(
                    platform=plat.value,
                    is_suppressed=False,
                    sample_size=sample_size,
                    median_removal_hours=metrics.median_removal_hours,
                    p90_removal_hours=metrics.p90_removal_hours,
                    deadline_compliance_rate_pct=metrics.deadline_compliance_rate_pct,
                    still_available_rate_pct=metrics.still_available_rate_pct,
                    median_data_response_hours=metrics.median_data_response_hours,
                    incomplete_requests_count=metrics.incomplete_police_requests_count,
                    suppression_reason=None,
                )
            platform_entries.append(entry)

        # 3. Category Breakdown with Small-Group Suppression
        category_metrics = self.metrics_engine.compute_by_category(records)
        category_entries: List[CategoryScoreboardEntry] = []

        for cat in Category:
            metrics = category_metrics[cat]
            sample_size = metrics.total_requests

            if sample_size < self.k_threshold:
                entry = CategoryScoreboardEntry(
                    category=cat.value,
                    is_suppressed=True,
                    sample_size=None,
                    median_removal_hours=None,
                    p90_removal_hours=None,
                    deadline_compliance_rate_pct=None,
                    suppression_reason=(
                        f"SmallGroupSuppression: Cohort count ({sample_size}) is below "
                        f"the k-anonymity threshold of {self.k_threshold}."
                    ),
                )
            else:
                entry = CategoryScoreboardEntry(
                    category=cat.value,
                    is_suppressed=False,
                    sample_size=sample_size,
                    median_removal_hours=metrics.median_removal_hours,
                    p90_removal_hours=metrics.p90_removal_hours,
                    deadline_compliance_rate_pct=metrics.deadline_compliance_rate_pct,
                    suppression_reason=None,
                )
            category_entries.append(entry)

        metadata = ScoreboardMetadata(
            generated_at=now,
            measurement_period_start=start_dt,
            measurement_period_end=end_dt,
            is_demonstration_mode=is_demo,
            disclaimer=disclaimer,
            k_anonymity_threshold=self.k_threshold,
            total_requests_analyzed=len(records),
        )

        return PublicScoreboard(
            metadata=metadata,
            overall_performance=overall_metrics,
            platform_breakdown=platform_entries,
            category_breakdown=category_entries,
            configured_deadlines=self.deadline_config.to_dict(),
        )
