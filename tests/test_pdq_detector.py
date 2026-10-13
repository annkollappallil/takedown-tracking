"""PDQ re-upload detector: matching rules, simulated feed, and agreement with ground truth."""

from datetime import datetime, timedelta, timezone
import json
import re

import pytest

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.hashing.images import rescale, synthetic_image
from takedown_tracking.hashing.pdq import PDQHash
from takedown_tracking.models.request import (
    Category,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
)
from takedown_tracking.verifier import (
    DEFAULT_PROFILES,
    IntegrityProfile,
    SimulatedChecker,
    SimulatedReuploadDetector,
    VerificationRunner,
    simulate_observations,
)
from takedown_tracking.verifier.pdq_detector import (
    HashRegistry,
    PDQReuploadDetector,
    candidate_from_image,
    hash_image,
    simulated_pdq_detector,
)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def record(rid="SYN-REQ-2026-00042", platform=Platform.PLATFORM_A) -> RequestRecord:
    return RequestRecord(
        request_id=rid,
        is_synthetic=True,
        request_type=RequestType.TAKEDOWN,
        platform=platform,
        category=Category.NON_CONSENSUAL_IMAGERY,
        priority=Priority.ROUTINE,
        sent_at=T0,
        police_unit_id="SYN-UNIT",
        is_complete_request=True,
        status=RequestStatus.CLAIMED_REMOVED,
        claimed_removal_at=T0 + timedelta(hours=1),
    )


class ListFeed:
    def __init__(self, candidates):
        self.items = candidates

    def candidates(self, platform, until):
        return [c for c in self.items if c.platform == platform and c.seen_at <= until]


@pytest.fixture(scope="module")
def original():
    return synthetic_image("SYN-REQ-2026-00042")


class TestDetector:
    def make(self, original, candidates):
        registry = HashRegistry()
        registry.register("SYN-REQ-2026-00042", hash_image(original))
        return PDQReuploadDetector(registry, ListFeed(candidates))

    def test_finds_resized_copy_only_while_it_is_live(self, original):
        copy = candidate_from_image("Platform A", T0 + timedelta(days=2), rescale(original, 0.7),
                                    taken_down_at=T0 + timedelta(days=4))
        detector = self.make(original, [copy])
        assert not detector.scan(record(), T0 + timedelta(days=1))
        assert detector.scan(record(), T0 + timedelta(days=3))
        assert not detector.scan(record(), T0 + timedelta(days=5))

    def test_ignores_unrelated_posts_and_other_platforms(self, original):
        unrelated = candidate_from_image("Platform A", T0, synthetic_image("something-else"))
        elsewhere = candidate_from_image("Platform B", T0, original)
        detector = self.make(original, [unrelated, elsewhere])
        assert not detector.scan(record(), T0 + timedelta(days=1))

    def test_featureless_content_is_never_matched(self):
        registry = HashRegistry()
        registry.register("SYN-REQ-2026-00042", PDQHash(0, quality=10))
        detector = PDQReuploadDetector(registry, ListFeed([]))
        assert not detector.scan(record(), T0 + timedelta(days=1))

    def test_unregistered_content_is_never_matched(self, original):
        detector = PDQReuploadDetector(HashRegistry(), ListFeed([candidate_from_image("Platform A", T0, original)]))
        assert not detector.scan(record(), T0 + timedelta(days=1))


class TestAgainstGroundTruth:
    @pytest.fixture(scope="class")
    def runs(self):
        records = SyntheticDataGenerator(seed=21).generate(count=160)
        as_of = max(r.sent_at for r in records) + timedelta(days=14)
        # Plenty of re-uploads so the hit rate is meaningful.
        profiles = {p: IntegrityProfile(reupload_rate=0.5) for p in DEFAULT_PROFILES}
        checker = SimulatedChecker(seed=21, profiles=profiles)
        truth = {o.request_id: o for o in VerificationRunner(checker, SimulatedReuploadDetector(checker)).run(records, as_of)}
        detector, feed = simulated_pdq_detector(checker, records)
        found = {o.request_id: o for o in VerificationRunner(checker, detector).run(records, as_of)}
        return truth, found, feed

    def test_no_false_matches(self, runs):
        truth, found, _ = runs
        for rid, obs in found.items():
            if obs.reupload_detected_at is not None:
                assert truth[rid].reupload_detected_at is not None, rid

    def test_finds_most_reuploads_at_the_same_scan(self, runs):
        truth, found, _ = runs
        planted = [rid for rid, o in truth.items() if o.reupload_detected_at is not None]
        assert len(planted) >= 15
        hits = [rid for rid in planted if found[rid].reupload_detected_at is not None]
        assert len(hits) / len(planted) >= 0.75
        for rid in hits:
            assert found[rid].reupload_detected_at == truth[rid].reupload_detected_at

    def test_decoys_were_present(self, runs):
        _, _, feed = runs
        assert any(feed._cache.values())

    def test_observations_carry_no_hashes(self):
        records = SyntheticDataGenerator(seed=3).generate(count=60)
        as_of = max(r.sent_at for r in records) + timedelta(days=14)
        payload = json.dumps([o.to_dict() for o in simulate_observations(records, as_of, reupload_detector="pdq")])
        assert not re.search(r"\b[0-9a-f]{40,}\b", payload)

    def test_unknown_detector_name_rejected(self):
        with pytest.raises(ValueError):
            simulate_observations([], T0, reupload_detector="magic")


class TestPdqCommand:
    def test_compares_two_images(self, tmp_path, capsys, original):
        pytest.importorskip("PIL")
        from takedown_tracking.cli import main
        from takedown_tracking.hashing.images import save_image

        a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
        save_image(original, str(a))
        save_image(rescale(original, 0.7), str(b))
        save_image(synthetic_image("unrelated"), str(c))

        assert main(["pdq", str(a), str(b)]) == 0
        assert "MATCH" in capsys.readouterr().out
        assert main(["pdq", str(a), str(c)]) == 0
        assert "NO MATCH" in capsys.readouterr().out
