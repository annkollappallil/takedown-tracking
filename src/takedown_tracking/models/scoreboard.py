"""Scoreboard data structures for public aggregation and transparency reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from takedown_tracking.models.metrics import PerformanceMetrics
from takedown_tracking.models.request import format_iso_datetime


@dataclass(frozen=True)
class PlatformScoreboardEntry:
    """Breakdown entry for an individual platform, with suppression support."""

    platform: str
    is_suppressed: bool
    sample_size: Optional[int] = None
    median_removal_hours: Optional[float] = None
    median_removal_censored: bool = False
    median_removal_display: Optional[str] = None
    p90_removal_hours: Optional[float] = None
    p90_removal_censored: bool = False
    p90_removal_display: Optional[str] = None
    deadline_compliance_rate_pct: Optional[float] = None
    still_available_rate_pct: Optional[float] = None
    rejections_count: Optional[int] = None
    rejection_rate_pct: Optional[float] = None
    median_data_response_hours: Optional[float] = None
    incomplete_requests_count: Optional[int] = None
    suppression_reason: Optional[str] = None
    # Per-platform integrity breakdown (Half 2 integration)
    false_removal_claims_count: Optional[int] = None
    observed_reuploads_count: Optional[int] = None
    verified_takedowns_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "is_suppressed": self.is_suppressed,
            "sample_size": self.sample_size,
            "median_removal_hours": self.median_removal_hours,
            "median_removal_censored": self.median_removal_censored,
            "median_removal_display": self.median_removal_display,
            "p90_removal_hours": self.p90_removal_hours,
            "p90_removal_censored": self.p90_removal_censored,
            "p90_removal_display": self.p90_removal_display,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "still_available_rate_pct": self.still_available_rate_pct,
            "rejections_count": self.rejections_count,
            "rejection_rate_pct": self.rejection_rate_pct,
            "median_data_response_hours": self.median_data_response_hours,
            "incomplete_requests_count": self.incomplete_requests_count,
            "suppression_reason": self.suppression_reason,
            "false_removal_claims_count": self.false_removal_claims_count,
            "observed_reuploads_count": self.observed_reuploads_count,
            "verified_takedowns_count": self.verified_takedowns_count,
        }


@dataclass(frozen=True)
class CategoryScoreboardEntry:
    """Breakdown entry for an individual harm category, with suppression support."""

    category: str
    is_suppressed: bool
    sample_size: Optional[int] = None
    median_removal_hours: Optional[float] = None
    median_removal_censored: bool = False
    median_removal_display: Optional[str] = None
    p90_removal_hours: Optional[float] = None
    p90_removal_censored: bool = False
    p90_removal_display: Optional[str] = None
    deadline_compliance_rate_pct: Optional[float] = None
    suppression_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "is_suppressed": self.is_suppressed,
            "sample_size": self.sample_size,
            "median_removal_hours": self.median_removal_hours,
            "median_removal_censored": self.median_removal_censored,
            "median_removal_display": self.median_removal_display,
            "p90_removal_hours": self.p90_removal_hours,
            "p90_removal_censored": self.p90_removal_censored,
            "p90_removal_display": self.p90_removal_display,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "suppression_reason": self.suppression_reason,
        }


@dataclass(frozen=True)
class ScoreboardMetadata:
    """Metadata regarding scoreboard generation and demonstration status."""

    generated_at: datetime
    as_of: datetime
    measurement_period_start: datetime
    measurement_period_end: datetime
    is_demonstration_mode: bool
    disclaimer: str
    k_anonymity_threshold: int
    total_requests_analyzed: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": format_iso_datetime(self.generated_at),
            "as_of": format_iso_datetime(self.as_of),
            "measurement_period_start": format_iso_datetime(self.measurement_period_start),
            "measurement_period_end": format_iso_datetime(self.measurement_period_end),
            "is_demonstration_mode": self.is_demonstration_mode,
            "disclaimer": self.disclaimer,
            "k_anonymity_threshold": self.k_anonymity_threshold,
            "total_requests_analyzed": self.total_requests_analyzed,
        }


@dataclass
class PublicScoreboard:
    """Root model for the public accountability scoreboard."""

    metadata: ScoreboardMetadata
    overall_performance: PerformanceMetrics
    platform_breakdown: List[PlatformScoreboardEntry]
    category_breakdown: List[CategoryScoreboardEntry]
    configured_deadlines: Dict[str, float]
    schema_version: str = "2.0.0"
    integrity_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "metadata": self.metadata.to_dict(),
            "overall_performance": {
                "total_takedown_requests": self.overall_performance.total_requests - self.overall_performance.total_data_requests,
                "eligible_takedown_requests": self.overall_performance.eligible_takedowns,
                "mature_takedown_requests": self.overall_performance.mature_takedowns,
                "completed_takedowns": self.overall_performance.completed_takedowns,
                "rejections_count": self.overall_performance.rejections_count,
                "rejection_rate_pct": self.overall_performance.rejection_rate_pct,
                "median_removal_hours": self.overall_performance.median_removal_hours,
                "median_removal_censored": self.overall_performance.median_removal_censored,
                "median_removal_display": self.overall_performance.median_removal_display,
                "p90_removal_hours": self.overall_performance.p90_removal_hours,
                "p90_removal_censored": self.overall_performance.p90_removal_censored,
                "p90_removal_display": self.overall_performance.p90_removal_display,
                "deadline_compliance_rate_pct": self.overall_performance.deadline_compliance_rate_pct,
                "still_available_rate_pct": self.overall_performance.still_available_rate_pct,
                "total_data_requests": self.overall_performance.total_data_requests,
                "mature_data_requests": self.overall_performance.mature_data_requests,
                "completed_data_requests": self.overall_performance.completed_data_requests,
                "median_data_response_hours": self.overall_performance.median_data_response_hours,
                "p90_data_response_hours": self.overall_performance.p90_data_response_hours,
                "data_deadline_compliance_rate_pct": self.overall_performance.data_deadline_compliance_rate_pct,
                "incomplete_police_requests_count": self.overall_performance.incomplete_police_requests_count,
                "invalid_records_count": self.overall_performance.invalid_records_count,
                "false_removal_claims_count": self.overall_performance.false_removal_claims_count,
                "observed_reuploads_count": self.overall_performance.observed_reuploads_count,
                "verified_takedowns_count": self.overall_performance.verified_takedowns_count,
            },
            "platform_breakdown": [p.to_dict() for p in self.platform_breakdown],
            "category_breakdown": [c.to_dict() for c in self.category_breakdown],
            "configured_deadlines": self.configured_deadlines,
        }
        if self.integrity_summary is not None:
            res["integrity_summary"] = self.integrity_summary
        elif self.overall_performance.false_removal_claims_count > 0 or self.overall_performance.observed_reuploads_count > 0 or self.overall_performance.verified_takedowns_count > 0:
            res["integrity_summary"] = {
                "false_removal_claims_count": self.overall_performance.false_removal_claims_count,
                "false_removal_claim_rate_pct": self.overall_performance.false_removal_claim_rate_pct,
                "observed_reuploads_count": self.overall_performance.observed_reuploads_count,
                "verified_takedowns_count": self.overall_performance.verified_takedowns_count,
                "verification_coverage_pct": self.overall_performance.verification_coverage_pct,
            }
        return res
