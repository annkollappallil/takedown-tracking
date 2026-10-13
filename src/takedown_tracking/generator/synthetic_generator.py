"""Reproducible synthetic data generator for takedown and data requests."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Union

from takedown_tracking.models.request import (
    Category,
    IncompletionReason,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)


class SyntheticDataGenerator:
    """Generates synthetic, privacy-safe records for demonstrations, tests, and calibration.

    All generated records explicitly have `is_synthetic = True` and synthetic identifiers.
    """

    # Realistic behavior profiles per platform:
    # (median_removal_hours, lognorm_sigma, complete_removal_prob, pending_prob, data_req_median_hours)
    PLATFORM_PROFILES: Dict[Platform, Dict[str, float]] = {
        Platform.META: {
            "median_removal_hours": 14.0,
            "removal_sigma": 0.6,
            "removal_success_rate": 0.88,
            "data_response_median_hours": 42.0,
        },
        Platform.INSTAGRAM: {
            "median_removal_hours": 16.0,
            "removal_sigma": 0.7,
            "removal_success_rate": 0.86,
            "data_response_median_hours": 44.0,
        },
        Platform.YOUTUBE: {
            "median_removal_hours": 10.0,
            "removal_sigma": 0.5,
            "removal_success_rate": 0.92,
            "data_response_median_hours": 36.0,
        },
        Platform.X_TWITTER: {
            "median_removal_hours": 28.0,
            "removal_sigma": 0.8,
            "removal_success_rate": 0.76,
            "data_response_median_hours": 68.0,
        },
        Platform.TELEGRAM: {
            "median_removal_hours": 58.0,
            "removal_sigma": 0.9,
            "removal_success_rate": 0.55,
            "data_response_median_hours": 96.0,
        },
        Platform.REDDIT: {
            "median_removal_hours": 22.0,
            "removal_sigma": 0.6,
            "removal_success_rate": 0.82,
            "data_response_median_hours": 52.0,
        },
        Platform.WHATSAPP: {
            "median_removal_hours": 20.0,
            "removal_sigma": 0.7,
            "removal_success_rate": 0.80,
            "data_response_median_hours": 48.0,
        },
        Platform.SNAPCHAT: {
            "median_removal_hours": 18.0,
            "removal_sigma": 0.6,
            "removal_success_rate": 0.84,
            "data_response_median_hours": 46.0,
        },
        Platform.TIKTOK: {
            "median_removal_hours": 12.0,
            "removal_sigma": 0.5,
            "removal_success_rate": 0.90,
            "data_response_median_hours": 38.0,
        },
        Platform.OTHER: {
            "median_removal_hours": 40.0,
            "removal_sigma": 0.8,
            "removal_success_rate": 0.65,
            "data_response_median_hours": 75.0,
        },
    }

    def __init__(self, seed: Optional[int] = 42) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def generate(
        self,
        count: int = 250,
        start_date: Optional[datetime] = None,
        duration_days: int = 30,
    ) -> List[RequestRecord]:
        """Generate a reproducible list of synthetic request records."""
        if count <= 0:
            return []

        if start_date is None:
            # Anchor to a deterministic point in time if reproducible
            start_date = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
        elif start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        records: List[RequestRecord] = []
        platforms = list(self.PLATFORM_PROFILES.keys())
        categories = list(Category)
        priorities = [Priority.ROUTINE, Priority.URGENT, Priority.EMERGENCY_LIFE_THREAT]
        priority_weights = [0.65, 0.25, 0.10]
        units = [f"SYN-UNIT-KL-CYBER-{i:02d}" for i in range(1, 9)]

        for idx in range(1, count + 1):
            req_id = f"SYN-REQ-2026-{idx:05d}"
            platform = self.rng.choice(platforms)
            category = self.rng.choice(categories)
            priority = self.rng.choices(priorities, weights=priority_weights, k=1)[0]
            police_unit = self.rng.choice(units)

            # 75% takedown requests, 25% data requests
            is_data_req = self.rng.random() < 0.25
            req_type = RequestType.DATA_REQUEST if is_data_req else RequestType.TAKEDOWN

            # Distribute sent_at uniformly across duration
            offset_seconds = self.rng.randint(0, duration_days * 86400)
            sent_at = start_date + timedelta(seconds=offset_seconds)

            # Incomplete request simulation (~7% police defect rate)
            is_complete = self.rng.random() > 0.07
            inc_reason: Optional[IncompletionReason] = None
            if not is_complete:
                inc_reason = self.rng.choice([
                    IncompletionReason.MISSING_COURT_ORDER,
                    IncompletionReason.INVALID_URL_FORMAT,
                    IncompletionReason.UNSPECIFIED_JURISDICTION,
                    IncompletionReason.INSUFFICIENT_TARGET_IDENTIFICATION,
                ])

            # Platform profile
            profile = self.PLATFORM_PROFILES[platform]

            # Acknowledgement simulation (95% acknowledged within 0.2 to 8 hours)
            acknowledged_at: Optional[datetime] = None
            if self.rng.random() < 0.95:
                ack_delay = self.rng.uniform(0.1, 6.0)
                if priority == Priority.EMERGENCY_LIFE_THREAT:
                    ack_delay = min(ack_delay, 0.5)
                acknowledged_at = sent_at + timedelta(hours=ack_delay)

            # Target synthetic URL hash (strictly sha256 of synthetic seed string)
            url_seed = f"synthetic://takedown-tracking.internal/evidence/{req_id}"
            content_url_hash = hashlib.sha256(url_seed.encode("utf-8")).hexdigest()

            # Lifecycle outcome simulation
            claimed_removal_at: Optional[datetime] = None
            data_provided_at: Optional[datetime] = None
            rejection_reason: Optional[str] = None

            if not is_complete and self.rng.random() < 0.60:
                # Incomplete requests often rejected or remain pending
                status = RequestStatus.REJECTED
                rejection_reason = "DeficientRequest: Procedural or jurisdictional prerequisite unmet."
            else:
                success_chance = profile["removal_success_rate"]
                outcome_roll = self.rng.random()

                if outcome_roll < success_chance:
                    # Successful response
                    if req_type == RequestType.TAKEDOWN:
                        # Log-normal distribution of removal speed
                        mu = profile["median_removal_hours"]
                        # Adjust by priority
                        if priority == Priority.EMERGENCY_LIFE_THREAT:
                            mu = max(0.5, mu * 0.15)
                        elif priority == Priority.URGENT:
                            mu = max(1.0, mu * 0.50)

                        delay_hours = self.rng.lognormvariate(
                            mu=math.log(mu) if mu > 0 else 0,
                            sigma=profile["removal_sigma"],
                        )
                        delay_hours = max(0.2, min(delay_hours, 360.0))
                        claimed_removal_at = sent_at + timedelta(hours=delay_hours)
                        status = RequestStatus.CLAIMED_REMOVED
                    else:
                        # Data request fulfillment
                        mu_data = profile["data_response_median_hours"]
                        delay_hours = self.rng.lognormvariate(
                            mu=math.log(mu_data) if mu_data > 0 else 0,
                            sigma=0.5,
                        )
                        delay_hours = max(1.0, min(delay_hours, 300.0))
                        data_provided_at = sent_at + timedelta(hours=delay_hours)
                        status = RequestStatus.DATA_PROVIDED
                elif outcome_roll < success_chance + 0.05:
                    # Rejected by platform
                    status = RequestStatus.REJECTED
                    rejection_reason = "PlatformPolicy: Request exceeds legal remit or lacks required court authorization."
                elif outcome_roll < success_chance + 0.12:
                    # Still pending / available
                    status = (
                        RequestStatus.ACKNOWLEDGED
                        if acknowledged_at
                        else RequestStatus.PENDING
                    )
                else:
                    # Expired without action
                    status = RequestStatus.EXPIRED

            record = RequestRecord(
                request_id=req_id,
                is_synthetic=True,
                request_type=req_type,
                platform=platform,
                category=category,
                priority=priority,
                sent_at=sent_at,
                acknowledged_at=acknowledged_at,
                claimed_removal_at=claimed_removal_at,
                data_provided_at=data_provided_at,
                police_unit_id=police_unit,
                is_complete_request=is_complete,
                incompletion_reason=inc_reason,
                status=status,
                rejection_reason=rejection_reason,
                content_url_hash=content_url_hash,
            )
            records.append(record)

        return records

    @staticmethod
    def export_json(records: List[RequestRecord], target_path: Union[str, Path]) -> None:
        """Export records to a JSON file."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in records]
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def export_csv(records: List[RequestRecord], target_path: Union[str, Path]) -> None:
        """Export records to a CSV file."""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            with open(target, "w", encoding="utf-8", newline="") as f:
                pass
            return

        headers = list(records[0].to_dict().keys())
        with open(target, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for r in records:
                writer.writerow(r.to_dict())
