"""Dispute flow: cycle rules, effects of accepted disputes, privacy of the public summary, CLI."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from takedown_tracking.disputes import (
    DisputableMetric,
    DisputeError,
    DisputeReason,
    DisputeStatus,
    ReportCycle,
    apply_resolutions,
    check_request_ids,
    load_cycle,
    platform_statement,
    public_dispute_summary,
    save_cycle,
)
from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.models.observation import ObservationRecord, ObservationStatus
from takedown_tracking.models.request import RequestStatus, RequestType

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, tzinfo=timezone.utc)
SHARED = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
CLOSED = SHARED + timedelta(days=8)

M, R = DisputableMetric, DisputeReason


def new_cycle() -> ReportCycle:
    return ReportCycle("2026-09", START, END, END, SHARED)


@pytest.fixture(scope="module")
def records():
    return SyntheticDataGenerator(seed=8).generate(count=120)


def takedowns_of(records, platform):
    return [r for r in records if r.platform.value == platform and r.request_type == RequestType.TAKEDOWN]


class TestCycleRules:
    def test_window_and_publish_gate(self):
        c = new_cycle()
        assert c.window_open(SHARED + timedelta(days=1))
        assert not c.window_open(CLOSED)
        d = c.raise_dispute("Platform A", M.OTHER, R.OTHER, "We disagree.", SHARED + timedelta(hours=1))
        assert c.publish_blockers(CLOSED) == ["1 dispute(s) still open"]
        with pytest.raises(DisputeError):
            c.mark_published(CLOSED)
        c.resolve(d.dispute_id, accept=False, note="No evidence given.", now=CLOSED)
        c.mark_published(CLOSED)
        assert c.published_at == CLOSED
        with pytest.raises(DisputeError):
            c.resolve(d.dispute_id, accept=True, note="Late change.", now=CLOSED)

    def test_cannot_publish_while_window_open(self):
        c = new_cycle()
        assert any("window open" in b for b in c.publish_blockers(SHARED + timedelta(days=1)))

    def test_no_disputes_outside_window(self):
        c = new_cycle()
        with pytest.raises(DisputeError):
            c.raise_dispute("Platform A", M.OTHER, R.OTHER, "Too late.", CLOSED)
        with pytest.raises(DisputeError):
            c.raise_dispute("Platform A", M.OTHER, R.OTHER, "Too early.", SHARED - timedelta(hours=1))

    def test_reasons_with_effects_need_request_ids(self):
        with pytest.raises(DisputeError):
            new_cycle().raise_dispute("Platform A", M.MEDIAN_REMOVAL_HOURS, R.DUPLICATE_REQUEST, "Dup.", SHARED)

    @pytest.mark.parametrize("text", ["see https://x.example/post/1", "www.example.org/abc", "post at example.com", "", "x" * 1001])
    def test_statements_must_be_short_plain_text(self, text):
        with pytest.raises(DisputeError):
            new_cycle().raise_dispute("Platform A", M.OTHER, R.OTHER, text, SHARED)

    def test_wrong_timestamp_needs_corrections_for_every_request(self):
        c = new_cycle()
        d = c.raise_dispute("Platform A", M.MEDIAN_REMOVAL_HOURS, R.WRONG_TIMESTAMP, "Removed earlier.", SHARED,
                            request_ids=("A1", "A2"))
        with pytest.raises(DisputeError):
            c.resolve(d.dispute_id, True, "ok", CLOSED, corrections={"A1": START})
        with pytest.raises(DisputeError):
            c.resolve(new_cycle().raise_dispute("Platform A", M.OTHER, R.OTHER, "x", SHARED).dispute_id,
                      True, "ok", CLOSED, corrections={"A1": START})

    def test_withdraw(self):
        c = new_cycle()
        d = c.raise_dispute("Platform A", M.OTHER, R.OTHER, "Never mind.", SHARED)
        assert c.withdraw(d.dispute_id, SHARED + timedelta(hours=2)).status == DisputeStatus.WITHDRAWN
        assert c.publish_blockers(CLOSED) == []

    def test_cycle_dates_validated(self):
        with pytest.raises(DisputeError):
            ReportCycle("x", END, START, END, SHARED)
        with pytest.raises(DisputeError):
            ReportCycle("x", START, END, END, END - timedelta(days=1))

    def test_round_trip(self, tmp_path):
        c = new_cycle()
        d = c.raise_dispute("Platform B", M.MEDIAN_REMOVAL_HOURS, R.WRONG_TIMESTAMP, "Earlier.", SHARED, ("B1",))
        c.resolve(d.dispute_id, True, "Checked.", CLOSED, corrections={"B1": START + timedelta(hours=1)})
        save_cycle(c, tmp_path / "c.json")
        assert load_cycle(tmp_path / "c.json").to_dict() == c.to_dict()


class TestEffects:
    def test_request_ids_must_belong_to_platform_and_cycle(self, records):
        c = new_cycle()
        a = takedowns_of(records, "Platform A")[0]
        check_request_ids(c, records, "Platform A", [a.request_id])
        with pytest.raises(DisputeError):
            check_request_ids(c, records, "Platform B", [a.request_id])
        with pytest.raises(DisputeError):
            check_request_ids(c, records, "Platform A", ["NOT-A-REQUEST"])

    def test_accepted_disputes_change_data_rejected_do_not(self, records):
        c = new_cycle()
        a = [r for r in takedowns_of(records, "Platform A") if r.is_complete_request]
        dup, incomplete, late, seen = a[0], a[1], a[2], a[3]
        obs = {seen.request_id: ObservationRecord(seen.request_id, ObservationStatus.CONFIRMED_AVAILABLE,
                                                  END, "test", first_checked_at=seen.sent_at,
                                                  last_seen_up_at=END)}
        fixed_time = late.sent_at + timedelta(hours=1)
        plan = [
            (R.DUPLICATE_REQUEST, (dup.request_id,), True, None),
            (R.REQUEST_INCOMPLETE, (incomplete.request_id,), True, None),
            (R.WRONG_TIMESTAMP, (late.request_id,), True, {late.request_id: fixed_time}),
            (R.VERIFICATION_ERROR, (seen.request_id,), True, None),
            (R.NOT_OUR_PLATFORM, (a[4].request_id,), False, None),
        ]
        for reason, ids, accept, corrections in plan:
            d = c.raise_dispute("Platform A", M.OTHER, reason, "Checked our logs.", SHARED, ids)
            c.resolve(d.dispute_id, accept, "Reviewed.", CLOSED, corrections=corrections)

        out, out_obs, adj = apply_resolutions(c, records, obs)
        by_id = {r.request_id: r for r in out}
        assert dup.request_id not in by_id
        assert a[4].request_id in by_id                     # rejected: unchanged
        assert by_id[incomplete.request_id].is_complete_request is False
        assert by_id[late.request_id].claimed_removal_at == fixed_time
        assert by_id[late.request_id].status == RequestStatus.CLAIMED_REMOVED
        assert seen.request_id not in out_obs
        assert adj.to_dict() == {"requests_excluded": 1, "requests_marked_incomplete": 1,
                                 "removal_times_corrected": 1, "observations_set_aside": 1}

    def test_only_cycle_period_is_used(self, records):
        moved = [replace(records[0], sent_at=END + timedelta(days=3))] + list(records[1:])
        out, _, _ = apply_resolutions(new_cycle(), moved)
        assert records[0].request_id not in {r.request_id for r in out}


class TestPrivacy:
    def test_public_summary_has_counts_only(self, records):
        c = new_cycle()
        rid = takedowns_of(records, "Platform C")[0].request_id
        d = c.raise_dispute("Platform C", M.FALSE_CLAIM_RATE, R.VERIFICATION_ERROR,
                            "Victim-sensitive explanation here.", SHARED, (rid,))
        c.resolve(d.dispute_id, False, "Police note that must stay internal.", CLOSED)
        _, _, adj = apply_resolutions(c, records)
        text = json.dumps(public_dispute_summary(c, adj, draft=False))
        assert rid not in text
        assert "Victim-sensitive" not in text and "must stay internal" not in text
        assert '"rejected": 1' in text

    def test_platform_statement_has_no_urls_or_hashes(self, records):
        text = json.dumps(platform_statement(new_cycle(), records, None, "Platform A"))
        assert "content_url" not in text and "police_unit" not in text
        for r in records:
            if r.content_url_hash:
                assert r.content_url_hash not in text
        others = {r.request_id for r in records if r.platform.value != "Platform A"}
        assert not any(o in text for o in others)


class TestCli:
    def run(self, *argv):
        from takedown_tracking.cli import main
        return main(list(argv))

    def test_full_cycle(self, tmp_path, records):
        req = tmp_path / "requests.json"
        SyntheticDataGenerator.export_json(records, req)
        cyc, out = str(tmp_path / "cycle.json"), str(tmp_path / "board.json")
        assert self.run("dispute", "open-cycle", "--cycle", cyc, "--id", "2026-09",
                        "--period-start", "2026-09-01T00:00:00Z", "--period-end", "2026-10-01T00:00:00Z",
                        "--as-of", "2026-10-01T00:00:00Z", "--at", "2026-10-02T09:00:00Z") == 0
        rid = takedowns_of(records, "Platform B")[0].request_id
        assert self.run("dispute", "raise", "--cycle", cyc, "--input", str(req), "--platform", "Platform B",
                        "--metric", "median_removal_hours", "--reason", "duplicate_request",
                        "--statement", "Reported twice.", "--request-ids", rid, "--at", "2026-10-03T00:00:00Z") == 0
        # Publishing is refused while the window is open and the dispute unresolved
        assert self.run("export-scoreboard", "--input", str(req), "--cycle", cyc, "--output", out,
                        "--at", "2026-10-04T00:00:00Z") == 3
        assert self.run("export-scoreboard", "--input", str(req), "--cycle", cyc, "--output", out, "--draft",
                        "--at", "2026-10-04T00:00:00Z") == 0
        assert json.load(open(out))["integrity_summary"]["disputes"]["report_status"] == "draft"
        assert self.run("dispute", "resolve", "--cycle", cyc, "--id", "DSP-2026-09-001", "accept",
                        "--note", "Confirmed.", "--at", "2026-10-05T00:00:00Z") == 0
        assert self.run("export-scoreboard", "--input", str(req), "--cycle", cyc, "--output", out,
                        "--at", "2026-10-10T00:00:00Z") == 0
        board = json.load(open(out))
        assert board["integrity_summary"]["disputes"]["report_status"] == "published"
        assert board["integrity_summary"]["disputes_resolved"] == 1
        assert board["metadata"]["total_requests_analyzed"] == len(records) - 1
        assert load_cycle(cyc).published_at is not None
        # Can't publish the same cycle twice
        assert self.run("export-scoreboard", "--input", str(req), "--cycle", cyc, "--output", out,
                        "--at", "2026-10-11T00:00:00Z") == 3

    def test_raise_rejects_other_platforms_requests(self, tmp_path, records):
        req = tmp_path / "requests.json"
        SyntheticDataGenerator.export_json(records, req)
        cyc = str(tmp_path / "cycle.json")
        self.run("dispute", "open-cycle", "--cycle", cyc, "--id", "2026-09", "--period-start", "2026-09-01T00:00:00Z",
                 "--period-end", "2026-10-01T00:00:00Z", "--as-of", "2026-10-01T00:00:00Z", "--at", "2026-10-02T09:00:00Z")
        rid = takedowns_of(records, "Platform B")[0].request_id
        assert self.run("dispute", "raise", "--cycle", cyc, "--input", str(req), "--platform", "Platform C",
                        "--metric", "other", "--reason", "duplicate_request", "--statement", "x",
                        "--request-ids", rid, "--at", "2026-10-03T00:00:00Z") == 2
