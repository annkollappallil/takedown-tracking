"""Victim status page: access codes, what is shown, rate limiting, HTTP endpoint and CLI."""

from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import re
import threading
from http.server import HTTPServer

import pytest

from takedown_tracking.models.observation import CheckError, ObservationRecord, ObservationStatus
from takedown_tracking.models.request import (
    Category,
    LegalRoute,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.victim_status import (
    NOT_FOUND_MESSAGE,
    RATE_LIMITED_MESSAGE,
    AccessStore,
    LookupResult,
    RateLimiter,
    StatusService,
    build_status,
    generate_access_code,
    issue_codes,
)
from takedown_tracking.web.app import TakedownRequestHandler

T0 = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
H = lambda h: T0 + timedelta(hours=h)  # noqa: E731
FAST = 1000  # PBKDF2 iterations for tests


def req(rid="SYN-REQ-2026-00001", status=RequestStatus.ACKNOWLEDGED, claim_h=None, complete=True, ack_h=0.5):
    return RequestRecord(
        request_id=rid,
        is_synthetic=True,
        request_type=RequestType.TAKEDOWN,
        platform=Platform.PLATFORM_C,
        category=Category.NON_CONSENSUAL_IMAGERY,
        priority=Priority.ROUTINE,
        sent_at=T0,
        police_unit_id="SYN-UNIT-KL-CYBER-03",
        is_complete_request=complete,
        status=status,
        acknowledged_at=H(ack_h) if ack_h is not None else None,
        claimed_removal_at=H(claim_h) if claim_h is not None else None,
        content_url_hash="a" * 64,
        legal_route=LegalRoute.GRIEVANCE_COMPLAINT_3_2_B,  # victim complaint: 2-hour deadline
    )


def obs_removed(rid, last_up_h, removed_h, reupload_h=None):
    return ObservationRecord(rid, ObservationStatus.CONFIRMED_REMOVED, H(removed_h + 0.25), "test",
                             first_checked_at=H(0.25), last_seen_up_at=H(last_up_h) if last_up_h else None,
                             verified_removed_at=H(removed_h),
                             reupload_detected_at=H(reupload_h) if reupload_h else None)


def obs_up(rid, last_up_h):
    return ObservationRecord(rid, ObservationStatus.CONFIRMED_AVAILABLE, H(last_up_h), "test",
                             first_checked_at=H(0.25), last_seen_up_at=H(last_up_h))


class TestAccessCodes:
    def test_format_and_uniqueness(self):
        codes = {generate_access_code() for _ in range(500)}
        assert len(codes) == 500
        assert all(re.fullmatch(r"[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}", c) for c in codes)

    def test_verify_and_normalisation(self):
        store = AccessStore(iterations=FAST)
        code = store.issue("syn-req-2026-00001")
        assert store.verify("SYN-REQ-2026-00001", code)
        assert store.verify(" syn-req-2026-00001 ", code.lower().replace("-", " "))
        assert not store.verify("SYN-REQ-2026-00001", generate_access_code())
        assert not store.verify("SYN-REQ-2026-99999", code)

    def test_unknown_reference_costs_the_same_work(self, monkeypatch):
        store = AccessStore(iterations=FAST)
        code = store.issue("SYN-REQ-2026-00001")
        calls = []
        real = hashlib.pbkdf2_hmac
        monkeypatch.setattr(hashlib, "pbkdf2_hmac", lambda *a: calls.append(1) or real(*a))
        store.verify("SYN-REQ-2026-00001", code)
        store.verify("SYN-REQ-2026-99999", code)
        assert len(calls) == 2

    def test_saved_file_never_contains_codes(self, tmp_path):
        store = AccessStore(iterations=FAST)
        issued = issue_codes(store, ["R1", "R2", "R3"])
        store.save(tmp_path / "access.json")
        text = (tmp_path / "access.json").read_text()
        for _, code in issued:
            assert code not in text and code.replace("-", "") not in text
        loaded = AccessStore.load(tmp_path / "access.json")
        assert all(loaded.verify(ref, code) for ref, code in issued)
        assert issue_codes(loaded, ["R1", "R2", "R3", "R4"]) and len(loaded) == 4
        assert issue_codes(loaded, ["R1"]) == []


class TestWhatTheVictimSees:
    @pytest.mark.parametrize(
        "record, obs, now_h, stage, within",
        [
            (req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.5), obs_removed("SYN-REQ-2026-00001", 1.25, 1.5), 10, "removal_confirmed", True),
            (req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.5), None, 10, "platform_says_removed", True),
            (req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.0), obs_up("SYN-REQ-2026-00001", 9.0), 10, "still_online_despite_claim", False),
            (req(), obs_up("SYN-REQ-2026-00001", 1.0), 1.0, "platform_acknowledged", None),
            (req(ack_h=None), None, 1.0, "sent_to_platform", None),
            (req(status=RequestStatus.REJECTED), None, 10, "platform_declined", False),
            (req(complete=False), None, 10, "needs_police_action", False),
            (req(), obs_removed("SYN-REQ-2026-00001", 4.75, 5.0), 10, "removal_confirmed", False),  # silent removal, late
        ],
    )
    def test_stages(self, record, obs, now_h, stage, within):
        s = build_status(record, obs, H(now_h))
        assert s["stage"] == stage
        assert s["within_deadline"] is within

    def test_deadline_passed_and_copy_found_are_explained(self):
        s = build_status(req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.0),
                         obs_removed("SYN-REQ-2026-00001", 0.75, 1.0, reupload_h=30), H(48))
        assert "copy was later found" in s["message"]
        late = build_status(req(), obs_up("SYN-REQ-2026-00001", 40), H(48))
        assert "deadline has passed" in late["message"]
        assert late["hours_elapsed"] == 48.0 and late["hours_taken"] is None

    def test_future_evidence_is_not_shown(self):
        s = build_status(req(), obs_removed("SYN-REQ-2026-00001", 49.75, 50), H(10))
        assert s["stage"] == "platform_acknowledged"
        assert s["independent_check"]["result"] == "online"

    def test_could_not_check(self):
        o = ObservationRecord("SYN-REQ-2026-00001", ObservationStatus.UNCERTAIN, H(5), "test",
                              first_checked_at=H(0.25), check_error=CheckError.LOGIN_WALL)
        assert build_status(req(), o, H(10))["independent_check"]["result"] == "could_not_check"

    def test_reveals_nothing_sensitive(self):
        s = build_status(req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.0), obs_removed("SYN-REQ-2026-00001", 0.75, 1.0), H(10))
        text = json.dumps(s)
        for secret in ("NonConsensual", "Platform C", "SYN-UNIT", "a" * 64, "category", "platform\"", "police_unit"):
            assert secret not in text


class TestRateLimiting:
    def make(self, clock):
        store = AccessStore(iterations=FAST)
        code = store.issue("SYN-REQ-2026-00001")
        service = StatusService(store, {"SYN-REQ-2026-00001": req()}, clock=clock)
        return service, code

    def test_client_blocked_after_five_failures_then_released(self):
        now = [H(10)]
        service, code = self.make(lambda: now[0])
        for _ in range(5):
            assert service.lookup("SYN-REQ-2026-00001", "WRONG", "1.2.3.4")[0] == LookupResult.NOT_FOUND
        assert service.lookup("SYN-REQ-2026-00001", code, "1.2.3.4")[0] == LookupResult.RATE_LIMITED
        assert service.lookup("SYN-REQ-2026-00001", code, "5.6.7.8")[0] == LookupResult.OK
        now[0] = H(10.5)
        assert service.lookup("SYN-REQ-2026-00001", code, "1.2.3.4")[0] == LookupResult.OK

    def test_reference_protected_against_many_clients(self):
        service, code = self.make(lambda: H(10))
        for i in range(10):
            service.lookup("SYN-REQ-2026-00001", "WRONG", f"10.0.0.{i}")
        assert service.lookup("SYN-REQ-2026-00001", code, "10.0.1.1")[0] == LookupResult.RATE_LIMITED

    def test_limiter_window(self):
        limiter = RateLimiter(per_client=2, per_reference=99, window=timedelta(minutes=1))
        limiter.record_failure("c", "r", H(0))
        limiter.record_failure("c", "r", H(0))
        assert limiter.blocked("c", "r", H(0))
        assert not limiter.blocked("c", "r", H(0) + timedelta(minutes=2))


@pytest.fixture
def server():
    store = AccessStore(iterations=FAST)
    code = store.issue("SYN-REQ-2026-00001")
    records = {"SYN-REQ-2026-00001": req(status=RequestStatus.CLAIMED_REMOVED, claim_h=1.5)}
    observations = {"SYN-REQ-2026-00001": obs_removed("SYN-REQ-2026-00001", 1.25, 1.5)}
    TakedownRequestHandler.status_service = StatusService(store, records, observations, clock=lambda: H(10))
    httpd = HTTPServer(("127.0.0.1", 0), TakedownRequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], code
    httpd.shutdown()
    httpd.server_close()
    TakedownRequestHandler.status_service = None


def call(port, method, path, body=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    out = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return out


class TestHttp:
    def test_lookup_ok_is_private_and_uncached(self, server):
        port, code = server
        status, headers, body = call(port, "POST", "/api/status", {"reference": "SYN-REQ-2026-00001", "code": code})
        assert status == 200
        assert json.loads(body)["stage"] == "removal_confirmed"
        assert headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in headers

    def test_wrong_code_and_unknown_reference_look_identical(self, server):
        port, code = server
        a = call(port, "POST", "/api/status", {"reference": "SYN-REQ-2026-00001", "code": "WRONG"})
        b = call(port, "POST", "/api/status", {"reference": "SYN-REQ-2026-77777", "code": code})
        assert a[0] == b[0] == 404 and a[2] == b[2]
        assert json.loads(a[2])["error"] == NOT_FOUND_MESSAGE

    def test_bad_requests(self, server):
        port, _ = server
        assert call(port, "POST", "/api/status", raw=b"not json")[0] == 400
        assert call(port, "POST", "/api/status", {"reference": 5, "code": []})[0] == 400
        assert call(port, "POST", "/api/status", raw=b"x" * 5000)[0] == 413
        assert call(port, "POST", "/api/other", {})[0] == 404

    def test_rate_limited_over_http(self, server):
        port, code = server
        for _ in range(5):
            call(port, "POST", "/api/status", {"reference": "SYN-REQ-2026-00001", "code": "WRONG"})
        status, _, body = call(port, "POST", "/api/status", {"reference": "SYN-REQ-2026-00001", "code": code})
        assert status == 429 and json.loads(body)["error"] == RATE_LIMITED_MESSAGE

    def test_page_is_self_contained(self, server):
        port, _ = server
        status, _, html = call(port, "GET", "/status")
        assert status == 200 and b'id="lookup"' in html
        assert b"https://" not in html and b"http://" not in html  # no third-party fonts or scripts
        status, _, js = call(port, "GET", "/status.js")
        assert status == 200 and b".innerHTML" not in js

    def test_disabled_without_access_file(self):
        TakedownRequestHandler.status_service = None
        httpd = HTTPServer(("127.0.0.1", 0), TakedownRequestHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            status, _, body = call(httpd.server_address[1], "POST", "/api/status", {"reference": "x", "code": "y"})
            assert status == 404 and b"not enabled" in body
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestCli:
    def test_issue_and_check(self, tmp_path, capsys):
        from takedown_tracking.cli import main
        from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator

        records = SyntheticDataGenerator(seed=4).generate(count=40)
        requests = tmp_path / "requests.json"
        SyntheticDataGenerator.export_json(records, requests)
        access, codes = tmp_path / "victim_access.json", tmp_path / "local_codes.csv"
        assert main(["victim-access", "issue", "--input", str(requests), "--access", str(access),
                     "--codes-out", str(codes), "--iterations", str(FAST)]) == 0
        rows = codes.read_text().strip().splitlines()[1:]
        takedowns = [r for r in records if r.request_type == RequestType.TAKEDOWN]
        assert len(rows) == len(takedowns)
        ref, code = rows[0].split(",")
        assert code not in access.read_text()
        capsys.readouterr()
        assert main(["victim-access", "check", "--input", str(requests), "--access", str(access),
                     "--reference", ref, "--code", code, "--at", "2026-10-08T00:00:00Z"]) == 0
        assert json.loads(capsys.readouterr().out)["reference"] == ref
        assert main(["victim-access", "check", "--input", str(requests), "--access", str(access),
                     "--reference", ref, "--code", "WRONG"]) == 1
