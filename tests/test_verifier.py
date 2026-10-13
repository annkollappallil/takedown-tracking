"""Tests for the pluggable verifier, the simulator and the scheduled runner (Half 2)."""

from datetime import datetime, timedelta, timezone
import json
import re
from typing import List, Optional

import pytest

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.integrity.rules import is_false_claim
from takedown_tracking.models.observation import CheckError, ObservationStatus
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.verifier import (
    CheckResult,
    CheckState,
    RunnerConfig,
    SimulatedChecker,
    SimulatedReuploadDetector,
    VerificationRunner,
    simulate_observations,
)

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=40)


def make_request(
    rid: str = "SYN-T-0001",
    status: RequestStatus = RequestStatus.CLAIMED_REMOVED,
    claim_h: Optional[float] = 2.0,
    rtype: RequestType = RequestType.TAKEDOWN,
    complete: bool = True,
    sent: datetime = T0,
) -> RequestRecord:
    return RequestRecord(
        request_id=rid,
        is_synthetic=True,
        request_type=rtype,
        platform=Platform.PLATFORM_A,
        category=Category.CSAM,
        priority=Priority.ROUTINE,
        sent_at=sent,
        police_unit_id="SYN-UNIT",
        is_complete_request=complete,
        status=status,
        claimed_removal_at=sent + timedelta(hours=claim_h) if claim_h is not None else None,
    )


class ScriptedChecker:
    """Checker whose answers are scripted by time: removed_at, glitches and errors."""

    agent_id = "scripted-checker"

    def __init__(
        self,
        removed_at: Optional[datetime] = None,
        glitch_down_at: Optional[datetime] = None,
        always_error: Optional[CheckError] = None,
    ) -> None:
        self.removed_at = removed_at
        self.glitch_down_at = glitch_down_at
        self.always_error = always_error
        self.calls: List[datetime] = []

    def check(self, record: RequestRecord, at: datetime) -> CheckResult:
        self.calls.append(at)
        if self.always_error:
            return CheckResult(CheckState.UNKNOWN, self.always_error)
        if self.glitch_down_at is not None and at == self.glitch_down_at:
            return CheckResult(CheckState.DOWN)
        if self.removed_at is not None and at >= self.removed_at:
            return CheckResult(CheckState.DOWN)
        return CheckResult(CheckState.UP)


class TestCheckResult:
    def test_unknown_requires_error(self) -> None:
        with pytest.raises(ValueError):
            CheckResult(CheckState.UNKNOWN)

    def test_conclusive_results_carry_no_error(self) -> None:
        with pytest.raises(ValueError):
            CheckResult(CheckState.UP, CheckError.TIMEOUT)


class TestRunner:
    def test_removal_confirmed_on_first_down_of_run(self) -> None:
        removed = T0 + timedelta(hours=1, minutes=40)
        obs = VerificationRunner(ScriptedChecker(removed_at=removed)).observe(make_request(), AS_OF)
        assert obs.observation_status == ObservationStatus.CONFIRMED_REMOVED
        # 15-minute grid: last UP at 1:30, first DOWN at 1:45, confirmed at 2:00
        assert obs.last_seen_up_at == T0 + timedelta(hours=1, minutes=30)
        assert obs.verified_removed_at == T0 + timedelta(hours=1, minutes=45)
        assert obs.last_checked_at == T0 + timedelta(hours=2)
        assert obs.first_checked_at == T0 + timedelta(minutes=15)
        obs.validate()

    def test_single_glitchy_down_is_not_a_removal(self) -> None:
        checker = ScriptedChecker(glitch_down_at=T0 + timedelta(hours=1))
        obs = VerificationRunner(checker).observe(make_request(), T0 + timedelta(hours=5))
        assert obs.observation_status == ObservationStatus.CONFIRMED_AVAILABLE
        assert obs.verified_removed_at is None
        assert obs.last_seen_up_at == T0 + timedelta(hours=5)

    def test_visibility_errors_are_uncertain(self) -> None:
        obs = VerificationRunner(ScriptedChecker(always_error=CheckError.LOGIN_WALL)).observe(
            make_request(), T0 + timedelta(hours=3)
        )
        assert obs.observation_status == ObservationStatus.UNCERTAIN
        assert obs.check_error == CheckError.LOGIN_WALL
        obs.validate()

    def test_technical_errors_are_failed_checks(self) -> None:
        obs = VerificationRunner(ScriptedChecker(always_error=CheckError.TIMEOUT)).observe(
            make_request(), T0 + timedelta(hours=3)
        )
        assert obs.observation_status == ObservationStatus.FAILED_CHECK
        assert obs.check_error == CheckError.TIMEOUT

    def test_never_checks_after_as_of_or_after_monitoring_window(self) -> None:
        checker = ScriptedChecker()
        as_of = T0 + timedelta(hours=6)
        VerificationRunner(checker).observe(make_request(), as_of)
        assert max(checker.calls) <= as_of

        checker = ScriptedChecker()
        VerificationRunner(checker, config=RunnerConfig(max_monitoring_days=1)).observe(make_request(), AS_OF)
        assert max(checker.calls) <= T0 + timedelta(days=1)

    def test_not_yet_checked_requests_have_no_observation(self) -> None:
        late = make_request(sent=AS_OF - timedelta(minutes=5))
        assert VerificationRunner(ScriptedChecker()).observe(late, AS_OF) is None

    def test_data_requests_and_incomplete_requests_are_not_monitored(self) -> None:
        runner = VerificationRunner(ScriptedChecker())
        assert runner.observe(make_request(rtype=RequestType.DATA_REQUEST), AS_OF) is None
        assert runner.observe(make_request(complete=False), AS_OF) is None


class TestSimulator:
    @pytest.fixture(scope="class")
    def records(self) -> List[RequestRecord]:
        return SyntheticDataGenerator(seed=42).generate(count=300)

    def test_ground_truth_is_order_independent(self, records: List[RequestRecord]) -> None:
        a, b = SimulatedChecker(seed=7), SimulatedChecker(seed=7)
        forward = [a.ground_truth(r) for r in records]
        backward = [b.ground_truth(r) for r in reversed(records)][::-1]
        assert forward == backward

    def test_same_seed_same_observations(self, records: List[RequestRecord]) -> None:
        as_of = max(r.sent_at for r in records) + timedelta(days=7)
        first = [o.to_dict() for o in simulate_observations(records, as_of, seed=3)]
        second = [o.to_dict() for o in simulate_observations(records, as_of, seed=3)]
        assert first == second

    def test_simulator_produces_every_scenario(self, records: List[RequestRecord]) -> None:
        checker = SimulatedChecker(seed=42)
        scenarios = {checker.ground_truth(r).scenario for r in records}
        assert {"honest_claim", "silent_removal", "never_removed"} <= scenarios
        assert scenarios & {"false_claim_removed_late", "false_claim_never_removed"}

    def test_false_claim_detection_matches_ground_truth(self, records: List[RequestRecord]) -> None:
        """Every testable claim is classified exactly as the hidden truth says."""
        as_of = max(r.sent_at for r in records) + timedelta(days=7)
        checker = SimulatedChecker(seed=42)
        runner = VerificationRunner(checker, SimulatedReuploadDetector(checker))
        checked = 0
        for r in records:
            truth = checker.ground_truth(r)
            if not truth.scenario.startswith(("honest_claim", "false_claim")):
                continue
            obs = runner.observe(r, as_of)
            if obs is None or obs.observation_status not in (
                ObservationStatus.CONFIRMED_REMOVED,
                ObservationStatus.CONFIRMED_AVAILABLE,
            ):
                continue
            checked += 1
            assert is_false_claim(r, obs) == truth.scenario.startswith("false_claim"), r.request_id
        assert checked > 100

    def test_verified_removal_within_one_interval_of_truth(self, records: List[RequestRecord]) -> None:
        as_of = max(r.sent_at for r in records) + timedelta(days=7)
        checker = SimulatedChecker(seed=42)
        runner = VerificationRunner(checker)
        interval = timedelta(minutes=runner.config.check_interval_minutes)
        for r in records:
            obs = runner.observe(r, as_of)
            if obs is None or obs.observation_status != ObservationStatus.CONFIRMED_REMOVED:
                continue
            truth = checker.ground_truth(r).removed_at
            assert truth is not None
            # Removal is seen at the first check on or after it (flaky checks can delay it).
            assert obs.verified_removed_at >= truth
            assert obs.last_seen_up_at is None or obs.last_seen_up_at < truth
            if obs.last_seen_up_at is not None:
                assert obs.verified_removed_at - obs.last_seen_up_at >= interval

    def test_reuploads_only_reported_inside_window(self, records: List[RequestRecord]) -> None:
        as_of = max(r.sent_at for r in records) + timedelta(days=30)
        config = RunnerConfig(reupload_window_days=7)
        found = 0
        for obs in simulate_observations(records, as_of, seed=42, config=config):
            obs.validate()
            if obs.reupload_detected_at is not None:
                found += 1
                assert obs.reupload_detected_at - obs.verified_removed_at <= timedelta(days=7)
        assert found > 0


class TestObservationPrivacy:
    def test_observations_contain_only_ids_timestamps_and_codes(self) -> None:
        records = SyntheticDataGenerator(seed=11).generate(count=120)
        as_of = max(r.sent_at for r in records) + timedelta(days=7)
        payload = json.dumps([o.to_dict() for o in simulate_observations(records, as_of)])
        assert "http" not in payload.lower()
        assert "synthetic://" not in payload
        assert not re.search(r"\b[0-9a-f]{40,}\b", payload)  # no hashes or HMACs
        for r in records:
            if r.content_url_hash:
                assert r.content_url_hash not in payload


class TestVerifyCommand:
    def test_verify_writes_valid_observations(self, tmp_path) -> None:
        from takedown_tracking.cli import load_observations_file, main

        requests = tmp_path / "requests.json"
        observations = tmp_path / "observations.json"
        records = SyntheticDataGenerator(seed=9).generate(count=60)
        SyntheticDataGenerator.export_json(records, requests)

        code = main(
            ["verify", "--input", str(requests), "--output", str(observations),
             "--as-of", "2026-10-08T00:00:00Z"]
        )
        assert code == 0
        loaded = load_observations_file(str(observations))
        monitored = [r for r in records if VerificationRunner.should_monitor(r)]
        assert set(loaded) == {r.request_id for r in monitored}

    def test_verify_refuses_real_records(self, tmp_path) -> None:
        from dataclasses import replace

        from takedown_tracking.cli import main

        records = [replace(r, is_synthetic=False) for r in SyntheticDataGenerator(seed=9).generate(count=20)]
        requests = tmp_path / "real.json"
        SyntheticDataGenerator.export_json(records, requests)
        assert main(["verify", "--input", str(requests), "--output", str(tmp_path / "o.json")]) == 2
        assert not (tmp_path / "o.json").exists()

    def test_loader_rejects_bad_rows_without_echoing_them(self, tmp_path) -> None:
        from takedown_tracking.cli import load_observations_file

        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([{
            "request_id": "SYN-REQ-1", "observation_status": "confirmed_available",
            "first_checked_at": "2026-09-01T00:15:00Z", "last_checked_at": "2026-09-01T01:00:00Z",
            "last_seen_up_at": "2026-09-01T01:00:00Z", "verifier_agent_id": "x-1",
            "notes": "https://example.invalid/secret-post",
        }]))
        with pytest.raises(ValueError) as exc:
            load_observations_file(str(bad))
        assert "example.invalid" not in str(exc.value)
        assert "index 0" in str(exc.value)
