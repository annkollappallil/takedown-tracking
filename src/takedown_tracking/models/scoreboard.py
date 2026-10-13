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
    p90_removal_hours: Optional[float] = None
    deadline_compliance_rate_pct: Optional[float] = None
    still_available_rate_pct: Optional[float] = None
    median_data_response_hours: Optional[float] = None
    incomplete_requests_count: Optional[int] = None
    suppression_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "is_suppressed": self.is_suppressed,
            "sample_size": self.sample_size,
            "median_removal_hours": self.median_removal_hours,
            "p90_removal_hours": self.p90_removal_hours,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "still_available_rate_pct": self.still_available_rate_pct,
            "median_data_response_hours": self.median_data_response_hours,
            "incomplete_requests_count": self.incomplete_requests_count,
            "suppression_reason": self.suppression_reason,
        }


@dataclass(frozen=True)
class CategoryScoreboardEntry:
    """Breakdown entry for an individual harm category, with suppression support."""

    category: str
    is_suppressed: bool
    sample_size: Optional[int] = None
    median_removal_hours: Optional[float] = None
    p90_removal_hours: Optional[float] = None
    deadline_compliance_rate_pct: Optional[float] = None
    suppression_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "is_suppressed": self.is_suppressed,
            "sample_size": self.sample_size,
            "median_removal_hours": self.median_removal_hours,
            "p90_removal_hours": self.p90_removal_hours,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "suppression_reason": self.suppression_reason,
        }


@dataclass(frozen=True)
class ScoreboardMetadata:
    """Metadata regarding scoreboard generation and demonstration status."""

    generated_at: datetime
    measurement_period_start: datetime
    measurement_period_end: datetime
    is_demonstration_mode: bool
    disclaimer: str
    k_anonymity_threshold: int
    total_requests_analyzed: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": format_iso_datetime(self.generated_at),
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
    schema_version: str = "1.0.0"
    integrity_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "metadata": self.metadata.to_dict(),
            "overall_performance": {
                "total_takedown_requests": self.overall_performance.total_requests - self.overall_performance.total_data_requests,
                "eligible_takedown_requests": self.overall_performance.eligible_takedowns,
                "median_removal_hours": self.overall_performance.median_removal_hours,
                "p90_removal_hours": self.overall_performance.p90_removal_hours,
                "deadline_compliance_rate_pct": self.overall_performance.deadline_compliance_rate_pct,
                "still_available_rate_pct": self.overall_performance.still_available_rate_pct,
                "total_data_requests": self.overall_performance.total_data_requests,
                "median_data_response_hours": self.overall_performance.median_data_response_hours,
                "p90_data_response_hours": self.overall_performance.p90_data_response_hours,
                "incomplete_police_requests_count": self.overall_performance.incomplete_police_requests_count,
                "invalid_records_count": self.overall_performance.invalid_records_count,
            },
            "platform_breakdown": [p.to_dict() for p in self.platform_breakdown],
            "category_breakdown": [c.to_dict() for c in self.category_breakdown],
            "configured_deadlines": self.configured_deadlines,
        }
        if self.integrity_summary is not None:
            res["integrity_summary"] = self.integrity_summary
        return res
