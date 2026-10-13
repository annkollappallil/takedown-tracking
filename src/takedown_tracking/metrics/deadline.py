"""Configurable deadline and SLA rules incorporating 2026 IT Rules amendments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from takedown_tracking.models.request import (
    Category,
    LegalRoute,
    Priority,
    RequestRecord,
    RequestType,
)


class DeadlineConfig:
    """Manages configurable deadlines for evaluating platform response SLA compliance.

    Incorporate 2026 IT Rules amendments (effective 20 February 2026):
    - Rule 3(1)(d): 3 hours for specified court orders or authorized government intimations.
    - Rule 3(2)(b): 2 hours for qualifying complaints (CSAM, NCII, impersonation).
    - Operational Priority: emergency_life_threat = 2 hours, urgent = 3 hours.
    - Data Requests: 72 hours under statutory assistance provisions.
    - Resolution Order: Strictest applicable deadline, i.e. min(applicable_hours).
    """

    RULE_3_2_B_QUALIFYING_CATEGORIES: Set[Category] = {
        Category.CSAM,
        Category.NON_CONSENSUAL_IMAGERY,
        Category.IMPERSONATION,
    }

    def __init__(
        self,
        deadlines_by_legal_route_hours: Optional[Dict[str, float]] = None,
        deadlines_by_priority_hours: Optional[Dict[str, float]] = None,
        deadlines_by_category_hours: Optional[Dict[str, float]] = None,
        data_request_deadline_hours: float = 72.0,
        default_takedown_deadline_hours: float = 36.0,
    ) -> None:
        self.deadlines_by_legal_route_hours = deadlines_by_legal_route_hours or {
            "court_order_or_govt_intimation": 3.0,
            "grievance_complaint_3_2_b": 2.0,
            "data_assistance_order": 72.0,
            "routine_police_inquiry": 36.0,
            "other": 36.0,
        }
        self.deadlines_by_priority_hours = deadlines_by_priority_hours or {
            "emergency_life_threat": 2.0,
            "urgent": 3.0,
            "routine": 36.0,
        }
        self.deadlines_by_category_hours = deadlines_by_category_hours or {
            "CSAM": 24.0,
            "NonConsensualImagery": 24.0,
            "Impersonation": 24.0,
            "Terrorism": 24.0,
            "ViolenceIncitement": 24.0,
            "HateSpeech": 36.0,
            "FinancialFraud": 36.0,
            "Defamation": 36.0,
            "Other": 36.0,
        }
        self.data_request_deadline_hours = data_request_deadline_hours
        self.default_takedown_deadline_hours = default_takedown_deadline_hours

    def get_deadline_hours(self, record: RequestRecord) -> float:
        """Determine applicable deadline for a given request record.

        Rule hierarchy and resolution principles:
        1. Data Requests: Governed strictly by data_request_deadline_hours (72h).
           Content-removal SLAs are never applied to data requests.
        2. Legal Route:
           - court_order_or_govt_intimation: Rule 3(1)(d) (3h).
           - grievance_complaint_3_2_b: Rule 3(2)(b) (2h) ONLY for qualifying categories
             (CSAM, NCII, Impersonation). Does not automatically apply if category does not qualify.
        3. Priority SLA:
           - emergency_life_threat (2h).
           - urgent (3h).
           - routine (36h).
        4. Category SLA: Configured category deadline if defined (e.g. CSAM=24h default).
        5. Conflict Resolution: Strictest rule resolution. Evaluates all applicable
           statutory route, priority, and category deadlines, and selects the minimum:
           min(applicable_deadlines).
        6. Missing or Ambiguous Route: If legal_route is absent or 'other', the engine
           relies on applicable priority and category rules. It does not fabricate a
           statutory route without supporting information.
        """
        if record.request_type == RequestType.DATA_REQUEST:
            return self.data_request_deadline_hours

        candidates: List[float] = []

        # 1. Operational priority
        prio_key = record.priority.value
        if prio_key in self.deadlines_by_priority_hours:
            candidates.append(self.deadlines_by_priority_hours[prio_key])

        # 2. Statutory legal route
        if record.legal_route:
            route_val = record.legal_route.value
            if route_val == LegalRoute.COURT_ORDER_OR_GOVT_INTIMATION.value:
                candidates.append(self.deadlines_by_legal_route_hours.get(route_val, 3.0))
            elif route_val == LegalRoute.GRIEVANCE_COMPLAINT_3_2_B.value:
                # Rule 3(2)(b) strictly applies to qualifying categories
                if record.category in self.RULE_3_2_B_QUALIFYING_CATEGORIES:
                    candidates.append(self.deadlines_by_legal_route_hours.get(route_val, 2.0))
                else:
                    candidates.append(self.default_takedown_deadline_hours)
            elif route_val in self.deadlines_by_legal_route_hours:
                candidates.append(self.deadlines_by_legal_route_hours[route_val])

        # 3. Harm category
        cat_key = record.category.value
        if cat_key in self.deadlines_by_category_hours:
            candidates.append(self.deadlines_by_category_hours[cat_key])

        if candidates:
            return min(candidates)

        return self.default_takedown_deadline_hours

    def to_dict(self) -> Dict[str, Any]:
        """Serialize deadline configuration."""
        return {
            "emergency_life_threat_hours": self.deadlines_by_priority_hours.get("emergency_life_threat", 2.0),
            "urgent_hours": self.deadlines_by_priority_hours.get("urgent", 3.0),
            "routine_hours": self.deadlines_by_priority_hours.get("routine", 36.0),
            "rule_3_1_d_court_order_hours": self.deadlines_by_legal_route_hours.get("court_order_or_govt_intimation", 3.0),
            "rule_3_2_b_grievance_hours": self.deadlines_by_legal_route_hours.get("grievance_complaint_3_2_b", 2.0),
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
            deadlines_by_legal_route_hours=data.get("deadlines_by_legal_route_hours"),
            deadlines_by_priority_hours=data.get("deadlines_by_priority_hours"),
            deadlines_by_category_hours=data.get("deadlines_by_category_hours"),
            data_request_deadline_hours=data.get("data_request_deadline_hours", 72.0),
            default_takedown_deadline_hours=data.get("default_takedown_deadline_hours", 36.0),
        )
