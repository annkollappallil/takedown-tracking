"""Tests for integrity rules, integrity metrics and how they feed the speed metrics."""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pytest

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.integrity import (
    build_integrity_summary,
    compute_integrity,
    effective_removal_time,
    is_false_claim,
)
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.observation import (
    CheckError,
    ObservationRecord,
    ObservationStatus,
    ObservationValidationError,
)
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=30)
H = lambda h: T0 + timedelta(hours=h)  # noqa: E731


def req(rid: str, status=RequestStatus.CLAIMED_REMOVED, claim_h: Optional[float] = 1.0, platform=Platform.PLATFORM_A):
    return RequestRecord(
        request_id=rid,
        is_synthetic=True,
        request_type=RequestType.TAKEDOWN,
        platform=platform,
        category=Category.CSAM,
        priority=Priority.ROUTINE,
        sent_at=T0,
        police_unit_id="SYN-UNIT",
        is_complete_request=True,
        status=status,
        claimed_removal_at=H(claim_h) if claim_h is not None else None,
    )


def removed(rid: str, last_up_h: Optional[float], removed_h: float, reupload_h: Optional[float] = None):
    return ObservationRecord(
        request_id=rid,
        observation_status=ObservationStatus.CONFIRMED_REMOVED,
        first_checked_at=H(0.25),
        last_seen_up_at=H(last_up_h) if last_up_h is not None else None,
        verified_removed_at=H(removed_h),
        reupload_detected_at=H(reupload_h) if reupload_h is not None else None,
        last_checked_at=H(removed_h + 0.25),
        verifier_agent_id="test",
    )


def still_up(rid: str, last_up_h: float):
    return ObservationRecord(
        request_id=rid,
        observation_status=ObservationStatus.CONFIRMED_AVAILABLE,
        first_checked_at=H(0.25),
        last_seen_up_at=H(last_up_h),
        last_checked_at=H(last_up_h),
        verifier_agent_id="test",
    )


class TestRules:
    def test_late_removal_after_early_claim_is_a_false_claim(self) -> None:
        """Claim at 1h, content seen up until 20h, then removed: the claim was false."""
        r, o = req("R1", claim_h=1.0), removed("R1", last_up_h=20.0, removed_h=20.25)
        assert is_false_claim(r, o)
        assert effective_removal_time(r, o, AS_OF) == H(20.25)

    def test_honest_claim_is_not_false(self) -> None:
        r, o = req("R2", claim_h=1.0), removed("R2", last_up_h=0.75, removed_h=1.0)
        assert not is_false_claim(r, o)

    def test_cache_grace_period(self) -> None:
        r = req("R3", claim_h=1.0)
        assert not is_false_claim(r, removed("R3", last_up_h=1.25, removed_h=1.5))  # within 15 min
        assert is_false_claim(r, removed("R3", last_up_h=1.5, removed_h=1.75))

    def test_never_removed_after_claim(self) -> None:
        r, o = req("R4", claim_h=1.0), still_up("R4", last_up_h=700.0)
        assert is_false_claim(r, o)
        assert effective_removal_time(r, o, AS_OF) is None

    def test_silent_removal_counts_as_removed(self) -> None:
        r = req("R5", status=RequestStatus.ACKNOWLEDGED, claim_h=None)
        assert effective_removal_time(r, removed("R5", 4.75, 5.0), AS_OF) == H(5.0)

    def test_inconclusive_observation_falls_back_to_claim(self) -> None:
        r = req("R6", claim_h=3.0)
        o = ObservationRecord(
            request_id="R6",
            observation_status=ObservationStatus.UNCERTAIN,
            first_checked_at=H(0.25),
            last_checked_at=H(10),
            check_error=CheckError.LOGIN_WALL,
            verifier_agent_id="test",
        )
        assert not is_false_claim(r, o)
        assert effective_removal_time(r, o, AS_OF) == H(3.0)

    def test_monitoring_ended_before_claim_honours_claim(self) -> None:
        r = req("R7", claim_h=50.0)
        assert effective_removal_time(r, still_up("R7", last_up_h=24.0), AS_OF) == H(50.0)

    def test_evidence_after_as_of_is_ignored(self) -> None:
        r = req("R8", status=RequestStatus.PENDING, claim_h=None)
        assert effective_removal_time(r, removed("R8", 99.75, 100.0), H(50)) is None


class TestEngineUsesRules:
    def test_late_false_claim_is_counted_and_timed_by_verifier(self) -> None:
        records = [req(f"R{i}", claim_h=1.0) for i in range(10)]
        obs = {f"R{i}": removed(f"R{i}", 0.75, 1.0) for i in range(9)}
        obs["R9"] = removed("R9", last_up_h=20.0, removed_h=20.25)
        m = MetricsEngine().compute_performance_metrics(records, as_of=AS_OF, observations=obs)
        assert m.false_removal_claims_count == 1
        assert m.false_removal_claim_rate_pct == 10.0
        assert m.completed_takedowns == 10       # it was removed in the end
        assert m.p90_removal_hours > 1.0         # but timed at 20.25h, not the 1h claim

    def test_verifier_beats_platform_status_for_silent_removals(self) -> None:
        records = [req(f"S{i}", status=RequestStatus.ACKNOWLEDGED, claim_h=None) for i in range(10)]
        obs = {f"S{i}": removed(f"S{i}", 0.75, 1.0) for i in range(10)}
        m = MetricsEngine().compute_performance_metrics(records, as_of=AS_OF, observations=obs)
        assert m.still_available_rate_pct == 0.0
        assert m.verified_takedowns_count == 10
        assert m.median_removal_hours == 1.0


class TestIntegrityMetrics:
    def test_counts_and_rates(self) -> None:
        records = [req(f"A{i}") for i in range(20)] + [
            req(f"P{i}", status=RequestStatus.PENDING, claim_h=None) for i in range(5)
        ]
        obs: Dict[str, ObservationRecord] = {}
        for i in range(16):
            # Removals on day 1; re-uploads for 4 of them within 7 days
            obs[f"A{i}"] = removed(f"A{i}", 0.75, 1.0, reupload_h=48.0 if i < 4 else None)
        for i in range(16, 18):
            obs[f"A{i}"] = removed(f"A{i}", 30.0, 30.25)  # false claims, removed late
        obs["A18"] = still_up("A18", 600.0)               # false claim, never removed
        # A19 not monitored
        obs["P0"] = removed("P0", 5.0, 5.25)              # silent removal
        for i in range(1, 5):
            obs[f"P{i}"] = ObservationRecord(
                request_id=f"P{i}",
                observation_status=ObservationStatus.UNCERTAIN,
                first_checked_at=H(0.25),
                last_checked_at=H(10),
                check_error=CheckError.PRIVATE_OR_RESTRICTED,
                verifier_agent_id="test",
            )

        m = compute_integrity(records, obs, AS_OF)
        assert m.eligible_takedowns == 25
        assert m.monitored_takedowns == 24
        assert m.verification_coverage_pct == 96.0
        assert m.unverifiable_count == 4
        assert m.claims_checked == 19
        assert m.false_claims_count == 3
        assert m.verified_removals == 19
        assert m.silent_removals_count == 1
        assert m.reupload_cohort == 19
        assert m.reuploads_within_window == 4

    def test_reupload_rate_excludes_removals_with_open_window(self) -> None:
        records = [req(f"W{i}") for i in range(10)]
        as_of = H(24 * 10)
        obs = {f"W{i}": removed(f"W{i}", 0.75, 1.0) for i in range(5)}  # window closed by day 10
        obs.update({f"W{i}": removed(f"W{i}", 0.75, 24 * 9.0) for i in range(5, 10)})  # still open
        m = compute_integrity(records, obs, as_of)
        assert m.verified_removals == 10
        assert m.reupload_cohort == 5

    def test_reuploads_outside_window_not_counted(self) -> None:
        records = [req("X1")]
        obs = {"X1": removed("X1", 0.75, 1.0, reupload_h=1.0 + 24 * 8)}
        assert compute_integrity(records, obs, AS_OF).reuploads_within_window == 0

    def test_publish_gates_each_figure_on_its_group_size(self) -> None:
        records = [req(f"K{i}") for i in range(12)]
        obs = {f"K{i}": removed(f"K{i}", 0.75, 1.0) for i in range(12)}
        obs["K0"] = removed("K0", 30.0, 30.25)
        public = compute_integrity(records, obs, AS_OF).publish(k=10)
        assert public["false_claim_rate_pct"] == 8.33   # 1 of 12 claims checked
        assert public["false_claims_count"] == 1        # same group as the rate
        assert public["reupload_rate_pct"] is not None

        tiny = compute_integrity(records[:5], obs, AS_OF).publish(k=10)
        assert all(v is None for v in tiny.values())

    def test_hidden_count_never_paired_with_visible_rate(self) -> None:
        gen = SyntheticDataGenerator(seed=5)
        records = gen.generate(count=400)
        as_of = max(r.sent_at for r in records) + timedelta(days=10)
        obs = {o.request_id: o for o in gen.generate_observations(records, as_of=as_of)}
        summary = build_integrity_summary(records, obs, as_of, k=10)
        pairs = [
            ("false_claims_count", "false_claim_rate_pct"),
            ("reuploads_within_window", "reupload_rate_pct"),
        ]
        for row in [summary["overall"], *summary["by_platform"]]:
            for count_key, rate_key in pairs:
                assert (row[count_key] is None) == (row[rate_key] is None)


class TestSummary:
    def test_summary_shape_and_fictional_platforms(self) -> None:
        gen = SyntheticDataGenerator(seed=42)
        records = gen.generate(count=300)
        as_of = max(r.sent_at for r in records) + timedelta(days=7)
        obs = {o.request_id: o for o in gen.generate_observations(records, as_of=as_of)}
        summary = build_integrity_summary(records, obs, as_of, k=10)
        assert summary["method"]["reupload_window_days"] == 7.0
        assert summary["by_platform"]
        for row in summary["by_platform"]:
            assert row["platform"].startswith("Platform ")
        assert summary["overall"]["verification_coverage_pct"] is not None


class TestContractValidation:
    def test_rejects_free_text_notes(self) -> None:
        with pytest.raises(ObservationValidationError):
            ObservationRecord.from_dict(
                {
                    "request_id": "N1",
                    "observation_status": "confirmed_available",
                    "first_checked_at": "2026-09-01T00:15:00Z",
                    "last_seen_up_at": "2026-09-01T01:00:00Z",
                    "last_checked_at": "2026-09-01T01:00:00Z",
                    "verifier_agent_id": "test",
                    "notes": "https://example.invalid/post/123",
                }
            )

    @pytest.mark.parametrize(
        "bad",
        [
            {"observation_status": "confirmed_removed"},  # no verified_removed_at
            {"observation_status": "uncertain"},  # no check_error
            {"observation_status": "confirmed_available", "verified_removed_at": "2026-09-01T00:30:00Z"},
            {
                "observation_status": "confirmed_removed",
                "last_seen_up_at": "2026-09-01T00:45:00Z",
                "verified_removed_at": "2026-09-01T00:30:00Z",
            },
            {
                "observation_status": "confirmed_removed",
                "verified_removed_at": "2026-09-01T00:30:00Z",
                "reupload_detected_at": "2026-09-01T00:20:00Z",
            },
        ],
    )
    def test_rejects_inconsistent_observations(self, bad: dict) -> None:
        base = {
            "request_id": "V1",
            "first_checked_at": "2026-09-01T00:15:00Z",
            "last_checked_at": "2026-09-01T01:00:00Z",
            "verifier_agent_id": "test",
        }
        with pytest.raises(ObservationValidationError):
            ObservationRecord.from_dict({**base, **bad})

    def test_model_fields_match_schema(self) -> None:
        import json
        from pathlib import Path

        schema = json.loads(
            (Path(__file__).parent.parent / "schemas" / "observation.schema.json").read_text()
        )
        obs = removed("M1", 0.75, 1.0)
        assert set(obs.to_dict()) == set(schema["properties"])
        assert set(schema["required"]) <= set(obs.to_dict())
