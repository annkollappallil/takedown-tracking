"""Integrity metrics and verification rules (Half 2)."""

from takedown_tracking.integrity.metrics import (
    IntegrityMetrics,
    build_integrity_summary,
    compute_integrity,
    compute_integrity_by_platform,
)
from takedown_tracking.integrity.rules import (
    DEFAULT_FALSE_CLAIM_GRACE,
    DEFAULT_REUPLOAD_WINDOW,
    effective_removal_time,
    is_false_claim,
    is_reupload_within_window,
)

__all__ = [
    "IntegrityMetrics",
    "build_integrity_summary",
    "compute_integrity",
    "compute_integrity_by_platform",
    "DEFAULT_FALSE_CLAIM_GRACE",
    "DEFAULT_REUPLOAD_WINDOW",
    "effective_removal_time",
    "is_false_claim",
    "is_reupload_within_window",
]
