"""Privacy filter enforcing k-anonymity, per-metric denominator checks, and zero-PII leakage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.metrics import PerformanceMetrics
from takedown_tracking.models.observation import ObservationRecord
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
    GENERIC_SUPPRESSION_REASON = (
        "Suppressed because the group is below the publication threshold."
    )
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

    # Narrow default public reporting scope focused strictly on high-impact victim protection
    DEFAULT_PUBLIC_CATEGORIES: Set[Category] = {
        Category.CSAM,
        Category.NON_CONSENSUAL_IMAGERY,
        Category.IMPERSONATION,
        Category.FINANCIAL_FRAUD,
    }

    def __init__(
        self,
        k_threshold: int = DEFAULT_K_THRESHOLD,
        is_demonstration_mode: bool = True,
        deadline_config: Optional[DeadlineConfig] = None,
        published_categories: Optional[Set[Category]] = None,
    ) -> None:
        if k_threshold < 1:
            raise ValueError("k_threshold must be >= 1")
        self.k_threshold = k_threshold
        self.is_demonstration_mode = is_demonstration_mode
        self.deadline_config = deadline_config or DeadlineConfig()
        self.published_categories = (
            published_categories
            if published_categories is not None
            else self.DEFAULT_PUBLIC_CATEGORIES
        )
        self.metrics_engine = MetricsEngine(self.deadline_config)

    def filter_and_publish(
        self,
        records: List[RequestRecord],
        invalid_records_count: int = 0,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        as_of: Optional[datetime] = None,
        observations: Optional[Dict[str, ObservationRecord]] = None,
    ) -> PublicScoreboard:
        """Transform raw records into a privacy-guaranteed PublicScoreboard."""
        now = datetime.now(timezone.utc)
        cutoff = as_of or now
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

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

        # Scope filtering: filter records to published categories before computing metrics
        # so that overall, platform, and category totals are strictly consistent and don't leak out-of-scope counts.
        if self.published_categories is not None:
            scoped_records = [r for r in records if r.category in self.published_categories]
        else:
            scoped_records = records

        # 1. Compute overall metrics (global aggregate is permitted as long as total >= k)
        overall_metrics = self.metrics_engine.compute_performance_metrics(
            scoped_records,
            invalid_records_count=invalid_records_count,
            as_of=cutoff,
            observations=observations,
        )

        # 2. Platform Breakdown with Per-Metric Small-Group Suppression
        platform_metrics = self.metrics_engine.compute_by_platform(
            scoped_records, as_of=cutoff, observations=observations
        )
        platform_entries: List[PlatformScoreboardEntry] = []

        # Determine platforms: in synthetic/demo mode, use fictional platforms Platform A..J
        is_synthetic_run = is_demo or any(r.is_synthetic for r in records)
        if is_synthetic_run:
            platforms_to_report = [
                Platform.PLATFORM_A,
                Platform.PLATFORM_B,
                Platform.PLATFORM_C,
                Platform.PLATFORM_D,
                Platform.PLATFORM_E,
                Platform.PLATFORM_F,
                Platform.PLATFORM_G,
                Platform.PLATFORM_H,
                Platform.PLATFORM_I,
                Platform.PLATFORM_J,
            ]
        else:
            platforms_to_report = [
                Platform.META,
                Platform.INSTAGRAM,
                Platform.X_TWITTER,
                Platform.YOUTUBE,
                Platform.TELEGRAM,
                Platform.WHATSAPP,
                Platform.SNAPCHAT,
                Platform.REDDIT,
                Platform.TIKTOK,
                Platform.OTHER,
            ]

        for plat in platforms_to_report:
            metrics = platform_metrics[plat]
            sample_size = metrics.total_requests

            if sample_size < self.k_threshold:
                # Suppress entire platform group when below k threshold
                entry = PlatformScoreboardEntry(
                    platform=plat.value,
                    is_suppressed=True,
                    sample_size=None,
                    median_removal_hours=None,
                    median_removal_censored=False,
                    median_removal_display=None,
                    p90_removal_hours=None,
                    p90_removal_censored=False,
                    p90_removal_display=None,
                    deadline_compliance_rate_pct=None,
                    still_available_rate_pct=None,
                    rejections_count=None,
                    rejection_rate_pct=None,
                    median_data_response_hours=None,
                    incomplete_requests_count=None,
                    suppression_reason=self.GENERIC_SUPPRESSION_REASON,
                    false_removal_claims_count=None,
                    observed_reuploads_count=None,
                    verified_takedowns_count=None,
                )
            else:
                # Platform has >= k total requests. Now apply independent per-metric k checks!
                # Takedown speed denominator check (speed cohort: completed + still available, excluding rejections):
                speed_cohort_size = metrics.completed_takedowns + metrics.still_available_takedowns
                speed_k = speed_cohort_size >= self.k_threshold
                # Takedown compliance / availability rates denominator check:
                takedowns_k = metrics.mature_takedowns >= self.k_threshold

                med_hours = metrics.median_removal_hours if speed_k else None
                med_censored = metrics.median_removal_censored if speed_k else False
                med_disp = metrics.median_removal_display if speed_k else "[Suppressed < k]"
                p90_hours = metrics.p90_removal_hours if speed_k else None
                p90_censored = metrics.p90_removal_censored if speed_k else False
                p90_disp = metrics.p90_removal_display if speed_k else "[Suppressed < k]"
                comp_pct = metrics.deadline_compliance_rate_pct if takedowns_k else None
                avail_pct = metrics.still_available_rate_pct if takedowns_k else None

                # Small count suppression for rejections:
                rejections = metrics.rejections_count
                rejection_pct = metrics.rejection_rate_pct
                if 0 < rejections < self.k_threshold:
                    rejections = None
                    rejection_pct = None

                # Data metric denominator check:
                # Median data turnaround is built strictly from completed data requests!
                data_k = metrics.completed_data_requests >= self.k_threshold
                data_hours = metrics.median_data_response_hours if data_k else None

                # Small count suppression for incomplete police requests:
                incomplete_count: Optional[int] = metrics.incomplete_police_requests_count
                if 0 < incomplete_count < self.k_threshold:
                    incomplete_count = None

                # Integrity metric denominator check:
                integrity_denom = metrics.completed_takedowns + metrics.false_removal_claims_count
                integrity_k = integrity_denom >= self.k_threshold
                false_claims = metrics.false_removal_claims_count if integrity_k else None
                reuploads = metrics.observed_reuploads_count if integrity_k else None
                verified_td = metrics.verified_takedowns_count if integrity_k else None

                entry = PlatformScoreboardEntry(
                    platform=plat.value,
                    is_suppressed=False,
                    sample_size=sample_size,
                    median_removal_hours=med_hours,
                    median_removal_censored=med_censored,
                    median_removal_display=med_disp,
                    p90_removal_hours=p90_hours,
                    p90_removal_censored=p90_censored,
                    p90_removal_display=p90_disp,
                    deadline_compliance_rate_pct=comp_pct,
                    still_available_rate_pct=avail_pct,
                    rejections_count=rejections,
                    rejection_rate_pct=rejection_pct,
                    median_data_response_hours=data_hours,
                    incomplete_requests_count=incomplete_count,
                    suppression_reason=None,
                    false_removal_claims_count=false_claims,
                    observed_reuploads_count=reuploads,
                    verified_takedowns_count=verified_td,
                )
            platform_entries.append(entry)

        # 3. Category Breakdown (filtered to published categories)
        category_metrics = self.metrics_engine.compute_by_category(
            scoped_records, as_of=cutoff, observations=observations
        )
        category_entries: List[CategoryScoreboardEntry] = []

        for cat in sorted(self.published_categories, key=lambda c: c.value):
            metrics = category_metrics[cat]
            sample_size = metrics.total_requests

            if sample_size < self.k_threshold:
                entry = CategoryScoreboardEntry(
                    category=cat.value,
                    is_suppressed=True,
                    sample_size=None,
                    median_removal_hours=None,
                    median_removal_censored=False,
                    median_removal_display=None,
                    p90_removal_hours=None,
                    p90_removal_censored=False,
                    p90_removal_display=None,
                    deadline_compliance_rate_pct=None,
                    suppression_reason=self.GENERIC_SUPPRESSION_REASON,
                )
            else:
                speed_cohort_size = metrics.completed_takedowns + metrics.still_available_takedowns
                speed_k = speed_cohort_size >= self.k_threshold
                takedowns_k = metrics.mature_takedowns >= self.k_threshold
                entry = CategoryScoreboardEntry(
                    category=cat.value,
                    is_suppressed=False,
                    sample_size=sample_size,
                    median_removal_hours=metrics.median_removal_hours if speed_k else None,
                    median_removal_censored=metrics.median_removal_censored if speed_k else False,
                    median_removal_display=metrics.median_removal_display if speed_k else "[Suppressed < k]",
                    p90_removal_hours=metrics.p90_removal_hours if speed_k else None,
                    p90_removal_censored=metrics.p90_removal_censored if speed_k else False,
                    p90_removal_display=metrics.p90_removal_display if speed_k else "[Suppressed < k]",
                    deadline_compliance_rate_pct=metrics.deadline_compliance_rate_pct if takedowns_k else None,
                    suppression_reason=None,
                )
            category_entries.append(entry)

        metadata = ScoreboardMetadata(
            generated_at=now,
            as_of=cutoff,
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
