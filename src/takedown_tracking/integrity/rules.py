"""Rules that combine a platform's claims with the verifier's observations.

These are the single source of truth for how verification affects the numbers.
The speed metrics engine and the integrity metrics both use them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from takedown_tracking.models.observation import ObservationRecord, ObservationStatus
from takedown_tracking.models.request import RequestRecord, RequestStatus

# Allowance for caches and clock differences: content seen up within this long
# after a removal claim does not make the claim false.
DEFAULT_FALSE_CLAIM_GRACE = timedelta(minutes=15)

# A copy found within this long after verified removal counts as a re-upload.
DEFAULT_REUPLOAD_WINDOW = timedelta(days=7)

CONCLUSIVE = (ObservationStatus.CONFIRMED_REMOVED, ObservationStatus.CONFIRMED_AVAILABLE)


def has_claim(record: RequestRecord) -> bool:
    return record.status == RequestStatus.CLAIMED_REMOVED and record.claimed_removal_at is not None


def is_false_claim(
    record: RequestRecord,
    obs: Optional[ObservationRecord],
    grace: timedelta = DEFAULT_FALSE_CLAIM_GRACE,
    as_of: Optional[datetime] = None,
) -> bool:
    """True if the verifier saw the content up after the platform said it was removed.

    Catches both cases:
    - claimed at 1h, content stayed up until 20h, then removed (last_seen_up_at = ~20h)
    - claimed at 1h, content never removed (still up at the last check)

    With `as_of`, only what was known by then counts: content last seen up after
    `as_of` was up continuously from its first check, so it was up at `as_of` too.
    """
    if obs is None or not has_claim(record):
        return False
    assert record.claimed_removal_at is not None
    threshold = record.claimed_removal_at + grace

    def known_by(ts: datetime) -> datetime:
        return ts if as_of is None else min(ts, as_of)

    if obs.last_seen_up_at is not None:
        return known_by(obs.last_seen_up_at) > threshold

    # Older observations without last_seen_up_at: fall back to "still up at last check".
    return (
        obs.observation_status == ObservationStatus.CONFIRMED_AVAILABLE
        and known_by(obs.last_checked_at) > threshold
    )


def effective_removal_time(
    record: RequestRecord,
    obs: Optional[ObservationRecord],
    as_of: datetime,
    grace: timedelta = DEFAULT_FALSE_CLAIM_GRACE,
) -> Optional[datetime]:
    """When the content should be treated as removed, or None if not removed by `as_of`.

    Verified evidence beats the platform's claim:
    1. Verifier confirmed removal -> verified_removed_at (even if the platform never reported it).
    2. Verifier saw it up after the claim -> not removed, whatever the platform says.
    3. No conclusive observation (none, uncertain, failed) -> fall back to the platform's claim.
    """
    if obs is not None and obs.observation_status == ObservationStatus.CONFIRMED_REMOVED:
        if obs.verified_removed_at is not None and obs.verified_removed_at <= as_of:
            return obs.verified_removed_at
        return None

    claim = record.claimed_removal_at if has_claim(record) else None
    if claim is None or claim > as_of:
        return None

    if obs is not None and obs.observation_status == ObservationStatus.CONFIRMED_AVAILABLE:
        # Still up at the last check. Honour the claim only if monitoring stopped before it.
        return None if is_false_claim(record, obs, grace, as_of) else claim

    return claim


def is_verified_removal(obs: Optional[ObservationRecord], as_of: datetime) -> bool:
    return (
        obs is not None
        and obs.observation_status == ObservationStatus.CONFIRMED_REMOVED
        and obs.verified_removed_at is not None
        and obs.verified_removed_at <= as_of
    )


def reupload_window_closed(
    obs: ObservationRecord, as_of: datetime, window: timedelta = DEFAULT_REUPLOAD_WINDOW
) -> bool:
    """True once the whole re-upload window after verified removal lies before `as_of`."""
    return obs.verified_removed_at is not None and obs.verified_removed_at + window <= as_of


def is_reupload_within_window(
    obs: Optional[ObservationRecord],
    as_of: datetime,
    window: timedelta = DEFAULT_REUPLOAD_WINDOW,
) -> bool:
    return (
        obs is not None
        and obs.verified_removed_at is not None
        and obs.reupload_detected_at is not None
        and obs.reupload_detected_at <= as_of
        and obs.reupload_detected_at - obs.verified_removed_at <= window
    )
