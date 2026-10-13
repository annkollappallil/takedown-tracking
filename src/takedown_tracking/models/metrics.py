"""Models representing calculated performance and integrity metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ValidationFailure:
    """Safe validation failure report that does not expose sensitive inputs."""

    row_index: int
    field: str
    error_code: str
    message: str

    def to_dict(self) -> Dict[str, str | int]:
        return {
            "row_index": self.row_index,
            "field": self.field,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PerformanceMetrics:
    """Statistical performance metrics for a specific group or overall dataset."""

    total_requests: int
    eligible_takedowns: int
    completed_takedowns: int
    still_available_takedowns: int
    median_removal_hours: Optional[float]
    p90_removal_hours: Optional[float]
    deadline_compliance_rate_pct: Optional[float]
    still_available_rate_pct: float
    total_data_requests: int
    completed_data_requests: int
    median_data_response_hours: Optional[float]
    p90_data_response_hours: Optional[float]
    data_deadline_compliance_rate_pct: Optional[float]
    incomplete_police_requests_count: int
    invalid_records_count: int

    def to_dict(self) -> Dict[str, Optional[float | int]]:
        return {
            "total_requests": self.total_requests,
            "eligible_takedowns": self.eligible_takedowns,
            "completed_takedowns": self.completed_takedowns,
            "still_available_takedowns": self.still_available_takedowns,
            "median_removal_hours": self.median_removal_hours,
            "p90_removal_hours": self.p90_removal_hours,
            "deadline_compliance_rate_pct": self.deadline_compliance_rate_pct,
            "still_available_rate_pct": self.still_available_rate_pct,
            "total_data_requests": self.total_data_requests,
            "completed_data_requests": self.completed_data_requests,
            "median_data_response_hours": self.median_data_response_hours,
            "p90_data_response_hours": self.p90_data_response_hours,
            "data_deadline_compliance_rate_pct": self.data_deadline_compliance_rate_pct,
            "incomplete_police_requests_count": self.incomplete_police_requests_count,
            "invalid_records_count": self.invalid_records_count,
        }
