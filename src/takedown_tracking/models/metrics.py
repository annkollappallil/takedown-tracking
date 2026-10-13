"""Models representing calculated performance and integrity metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class ValidationFailure:
    """Safe validation failure report that does not expose sensitive inputs."""

    row_index: int
    field: str
    error_code: str
    message: str

    def to_dict(self) -> Dict[str, Union[str, int]]:
        return {
            "row_index": self.row_index,
            "field": self.field,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PercentileResult:
    """Right-censored percentile measurement result."""

    numeric_hours: Optional[float]
    is_censored: bool
    display: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "numeric_hours": self.numeric_hours,
            "is_censored": self.is_censored,
            "display": self.display,
        }


@dataclass(frozen=True)
class PerformanceMetrics:
    """Statistical performance metrics for a specific group or overall dataset."""

    total_requests: int
    eligible_takedowns: int
    mature_takedowns: int
    completed_takedowns: int
    still_available_takedowns: int
    rejections_count: int
    rejection_rate_pct: float
    median_removal_hours: Optional[float]
    median_removal_censored: bool
    median_removal_display: str
    p90_removal_hours: Optional[float]
    p90_removal_censored: bool
    p90_removal_display: str
    deadline_compliance_rate_pct: Optional[float]
    still_available_rate_pct: float
    total_data_requests: int
    mature_data_requests: int = 0
    completed_data_requests: int = 0
    median_data_response_hours: Optional[float] = None
    p90_data_response_hours: Optional[float] = None
    data_deadline_compliance_rate_pct: Optional[float] = None
    incomplete_police_requests_count: int = 0
    invalid_records_count: int = 0
    # Integrity metrics (Half 1 / Half 2 shared contract)
    false_removal_claims_count: int = 0
    false_removal_claim_rate_pct: Optional[float] = None
    observed_reuploads_count: int = 0
    verified_takedowns_count: int = 0
    verification_coverage_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "eligible_takedowns": self.eligible_takedowns,
            "mature_takedowns": self.mature_takedowns,
            "completed_takedowns": self.completed_takedowns,
            "still_available_takedowns": self.still_available_takedowns,
            "rejections_count": self.rejections_count,
            "rejection_rate_pct": self.rejection_rate_pct,
            "median_removal_hours": self.median_removal_hours,
            "median_removal_censored": self.median_removal_censored,
            "median_removal_display": self.median_removal_display,
            "p90_removal_hours": self.p90_removal_hours,
            "p90_removal_censored": self.p90_removal_censored,
            "p90_removal_display": self.p90_removal_display,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "still_available_rate_pct": self.still_available_rate_pct,
            "total_data_requests": self.total_data_requests,
            "mature_data_requests": self.mature_data_requests,
            "completed_data_requests": self.completed_data_requests,
            "median_data_response_hours": self.median_data_response_hours,
            "p90_data_response_hours": self.p90_data_response_hours,
            "data_deadline_compliance_rate_pct": self.data_deadline_compliance_rate_pct,
            "incomplete_police_requests_count": self.incomplete_police_requests_count,
            "invalid_records_count": self.invalid_records_count,
            "false_removal_claims_count": self.false_removal_claims_count,
            "false_removal_claim_rate_pct": self.false_removal_claim_rate_pct,
            "observed_reuploads_count": self.observed_reuploads_count,
            "verified_takedowns_count": self.verified_takedowns_count,
            "verification_coverage_pct": self.verification_coverage_pct,
        }
