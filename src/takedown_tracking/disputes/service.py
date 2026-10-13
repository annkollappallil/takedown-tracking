"""Applying disputes to the data, platform statements, the public summary and storage."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from takedown_tracking.disputes.models import (
    EFFECTS,
    DisputeError,
    DisputeStatus,
    ReportCycle,
)
from takedown_tracking.integrity.rules import effective_removal_time, is_false_claim
from takedown_tracking.models.observation import ObservationRecord
from takedown_tracking.models.request import (
    IncompletionReason,
    RequestRecord,
    RequestStatus,
    RequestType,
    format_iso_datetime,
)


def records_in_cycle(cycle: ReportCycle, records: Iterable[RequestRecord]) -> List[RequestRecord]:
    return [r for r in records if cycle.period_start <= r.sent_at < cycle.period_end]


def check_request_ids(
    cycle: ReportCycle, records: Iterable[RequestRecord], platform: str, request_ids: Iterable[str]
) -> None:
    """Every disputed request must exist in this cycle and belong to the disputing platform."""
    by_id = {r.request_id: r for r in records_in_cycle(cycle, records)}
    for rid in request_ids:
        record = by_id.get(rid)
        if record is None:
            raise DisputeError("a disputed request is not part of this report cycle")
        if record.platform.value != platform:
            raise DisputeError("a platform can only dispute its own requests")


@dataclass(frozen=True)
class Adjustments:
    requests_excluded: int = 0
    requests_marked_incomplete: int = 0
    removal_times_corrected: int = 0
    observations_set_aside: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "requests_excluded": self.requests_excluded,
            "requests_marked_incomplete": self.requests_marked_incomplete,
            "removal_times_corrected": self.removal_times_corrected,
            "observations_set_aside": self.observations_set_aside,
        }


def apply_resolutions(
    cycle: ReportCycle,
    records: Iterable[RequestRecord],
    observations: Optional[Mapping[str, ObservationRecord]] = None,
) -> Tuple[List[RequestRecord], Optional[Dict[str, ObservationRecord]], Adjustments]:
    """Restrict to the cycle's period and apply every accepted dispute."""
    exclude, incomplete, set_aside = set(), set(), set()
    corrections: Dict[str, Any] = {}
    for d in cycle.disputes:
        if d.status != DisputeStatus.ACCEPTED:
            continue
        effect = EFFECTS[d.reason]
        if effect == "exclude_requests":
            exclude.update(d.request_ids)
        elif effect == "mark_incomplete":
            incomplete.update(d.request_ids)
        elif effect == "set_aside_observation":
            set_aside.update(d.request_ids)
        elif effect == "correct_claimed_removal":
            corrections.update(dict(d.corrections))

    adjusted: List[RequestRecord] = []
    n_incomplete = n_corrected = 0
    for r in records_in_cycle(cycle, records):
        if r.request_id in exclude:
            continue
        if r.request_id in incomplete and r.is_complete_request:
            r = replace(r, is_complete_request=False, incompletion_reason=IncompletionReason.OTHER_DEFICIENCY)
            n_incomplete += 1
        if r.request_id in corrections:
            corrected = corrections[r.request_id]
            if corrected < r.sent_at:
                raise DisputeError("a corrected removal time is before the request was sent")
            r = replace(r, claimed_removal_at=corrected, status=RequestStatus.CLAIMED_REMOVED)
            n_corrected += 1
        adjusted.append(r)

    kept_ids = {r.request_id for r in adjusted}
    adjusted_obs: Optional[Dict[str, ObservationRecord]] = None
    n_set_aside = 0
    if observations is not None:
        adjusted_obs = {}
        for rid, obs in observations.items():
            if rid not in kept_ids:
                continue
            if rid in set_aside:
                n_set_aside += 1
                continue
            adjusted_obs[rid] = obs

    in_period = {r.request_id for r in records_in_cycle(cycle, records)}
    return adjusted, adjusted_obs, Adjustments(
        requests_excluded=len(exclude & in_period),
        requests_marked_incomplete=n_incomplete,
        removal_times_corrected=n_corrected,
        observations_set_aside=n_set_aside,
    )


def platform_statement(
    cycle: ReportCycle,
    records: Iterable[RequestRecord],
    observations: Optional[Mapping[str, ObservationRecord]],
    platform: str,
) -> Dict[str, Any]:
    """What one platform receives with the draft: its own takedown requests as the report sees them.

    The platform already holds these requests. No URLs, URL hashes, police unit IDs or
    content hashes are included.
    """
    observations = observations or {}
    rows = []
    for r in sorted(records_in_cycle(cycle, records), key=lambda x: x.sent_at):
        if r.platform.value != platform or r.request_type != RequestType.TAKEDOWN:
            continue
        obs = observations.get(r.request_id)
        removal = effective_removal_time(r, obs, cycle.as_of)
        rows.append(
            {
                "request_id": r.request_id,
                "sent_at": format_iso_datetime(r.sent_at),
                "counted": r.is_complete_request,
                "platform_status": r.status.value,
                "claimed_removal_at": format_iso_datetime(r.claimed_removal_at),
                "verified_removed_at": format_iso_datetime(obs.verified_removed_at) if obs else None,
                "last_seen_up_at": format_iso_datetime(obs.last_seen_up_at) if obs else None,
                "removal_time_used": format_iso_datetime(removal),
                "flagged_false_claim": is_false_claim(r, obs, as_of=cycle.as_of),
                "reupload_found": bool(obs and obs.reupload_detected_at),
            }
        )
    return {
        "cycle_id": cycle.cycle_id,
        "platform": platform,
        "as_of": format_iso_datetime(cycle.as_of),
        "dispute_window_closes_at": format_iso_datetime(cycle.window_closes_at),
        "requests": rows,
    }


def public_dispute_summary(cycle: ReportCycle, adjustments: Adjustments, draft: bool) -> Dict[str, Any]:
    """Counts only. No statements, notes or request IDs."""
    by_platform: Dict[str, Counter] = {}
    metrics: Dict[str, set] = {}
    for d in cycle.disputes:
        by_platform.setdefault(d.platform, Counter())[d.status.value] += 1
        if d.status in (DisputeStatus.ACCEPTED, DisputeStatus.REJECTED, DisputeStatus.OPEN):
            metrics.setdefault(d.platform, set()).add(d.metric.value)
    return {
        "report_status": "draft" if draft else "published",
        "cycle_id": cycle.cycle_id,
        "dispute_window_closes_at": format_iso_datetime(cycle.window_closes_at),
        "by_platform": [
            {
                "platform": platform,
                "raised": sum(counts.values()),
                "accepted": counts[DisputeStatus.ACCEPTED.value],
                "rejected": counts[DisputeStatus.REJECTED.value],
                "withdrawn": counts[DisputeStatus.WITHDRAWN.value],
                "open": counts[DisputeStatus.OPEN.value],
                "metrics_disputed": sorted(metrics.get(platform, ())),
            }
            for platform, counts in sorted(by_platform.items())
        ],
        "adjustments": adjustments.to_dict(),
    }


# ----------------------------------------------------------------------------- storage


def load_cycle(path: Union[str, Path]) -> ReportCycle:
    with open(path, "r", encoding="utf-8") as f:
        return ReportCycle.from_dict(json.load(f))


def save_cycle(cycle: ReportCycle, path: Union[str, Path]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cycle.to_dict(), f, indent=2)
    tmp.replace(target)  # atomic swap so a crash never leaves half a file
