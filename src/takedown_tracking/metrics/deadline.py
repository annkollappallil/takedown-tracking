"""Configurable deadline and SLA rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from takedown_tracking.models.request import Category, Priority, RequestRecord, RequestType


class DeadlineConfig:
    """Manages configurable deadlines for evaluating platform response SLA compliance."""

    def __init__(
        self,
        deadlines_by_priority_hours: Optional[Dict[str, float]] = None,
        deadlines_by_category_hours: Optional[Dict[str, float]] = None,
        data_request_deadline_hours: float = 72.0,
        default_takedown_deadline_hours: float = 36.0,
    ) -> None:
        self.deadlines_by_priority_hours = deadlines_by_priority_hours or {
            "emergency_life_threat": 2.0,
            "urgent": 24.0,
            "routine": 36.0,
        }
        self.deadlines_by_category_hours = deadlines_by_category_hours or {
            "CSAM": 24.0,
            "NonConsensualImagery": 24.0,
            "Terrorism": 24.0,
            "ViolenceIncitement": 24.0,
            "HateSpeech": 36.0,
            "FinancialFraud": 36.0,
            "Defamation": 36.0,
            "Impersonation": 36.0,
            "Other": 36.0,
        }
        self.data_request_deadline_hours = data_request_deadline_hours
        self.default_takedown_deadline_hours = default_takedown_deadline_hours

    def get_deadline_hours(self, record: RequestRecord) -> float:
        """Determine applicable deadline for a given request record.

        Rule hierarchy:
        1. If data_request: return data_request_deadline_hours.
        2. If emergency priority: emergency deadline takes precedence (2h).
        3. Category-specific statutory deadline (e.g., CSAM = 24h, NonConsensualImagery = 24h).
        4. Priority-specific deadline (urgent = 24h).
        5. Default takedown fallback (36h).
        """
        if record.request_type == RequestType.DATA_REQUEST:
            return self.data_request_deadline_hours

        # Highest urgency: Emergency threat to life
        if record.priority == Priority.EMERGENCY_LIFE_THREAT:
            return self.deadlines_by_priority_hours.get("emergency_life_threat", 2.0)

        # Statutory categories
        cat_key = record.category.value
        if cat_key in self.deadlines_by_category_hours:
            return self.deadlines_by_category_hours[cat_key]

        # Priority level
        prio_key = record.priority.value
        if prio_key in self.deadlines_by_priority_hours:
            return self.deadlines_by_priority_hours[prio_key]

        return self.default_takedown_deadline_hours

    def to_dict(self) -> Dict[str, Any]:
        """Serialize deadline configuration."""
        return {
            "emergency_life_threat_hours": self.deadlines_by_priority_hours.get("emergency_life_threat", 2.0),
            "urgent_hours": self.deadlines_by_priority_hours.get("urgent", 24.0),
            "routine_hours": self.deadlines_by_priority_hours.get("routine", 36.0),
            "csam_ncii_hours": self.deadlines_by_category_hours.get("CSAM", 24.0),
            "data_request_deadline_hours": self.data_request_deadline_hours,
            "default_takedown_deadline_hours": self.default_takedown_deadline_hours,
        }

    @classmethod
    def load_from_file(cls, path: Union[str, Path]) -> DeadlineConfig:
        """Load external deadline configuration from JSON file."""
        file_path = Path(path)
        if not file_path.exists():
            return cls()

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(
            deadlines_by_priority_hours=data.get("deadlines_by_priority_hours"),
            deadlines_by_category_hours=data.get("deadlines_by_category_hours"),
            data_request_deadline_hours=data.get("data_request_deadline_hours", 72.0),
            default_takedown_deadline_hours=data.get("default_takedown_deadline_hours", 36.0),
        )
