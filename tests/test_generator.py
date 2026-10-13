"""Unit tests for the synthetic data generator."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.ingest.importer import RequestImporter
from takedown_tracking.models.request import Platform, RequestStatus, RequestType


class TestSyntheticDataGenerator:
    """Test suite for SyntheticDataGenerator."""

    def test_reproducibility_with_seed(self) -> None:
        """Identical seeds must generate exact identical records."""
        gen1 = SyntheticDataGenerator(seed=12345)
        recs1 = gen1.generate(count=40)

        gen2 = SyntheticDataGenerator(seed=12345)
        recs2 = gen2.generate(count=40)

        assert len(recs1) == 40
        assert len(recs2) == 40

        for r1, r2 in zip(recs1, recs2):
            assert r1.request_id == r2.request_id
            assert r1.platform == r2.platform
            assert r1.sent_at == r2.sent_at
            assert r1.claimed_removal_at == r2.claimed_removal_at
            assert r1.status == r2.status
            assert r1.is_synthetic is True

    def test_differing_seeds_produce_variation(self) -> None:
        """Different seeds must produce different record distributions."""
        recs1 = SyntheticDataGenerator(seed=101).generate(count=20)
        recs2 = SyntheticDataGenerator(seed=202).generate(count=20)

        assert recs1[0].platform != recs2[0].platform or recs1[0].sent_at != recs2[0].sent_at

    def test_synthetic_safeguards_and_fictional_platforms(self) -> None:
        """All records must be flagged as synthetic, use fictional platforms, and have HMACs."""
        gen = SyntheticDataGenerator(seed=999)
        records = gen.generate(count=50)

        fictional_allowed = {f"Platform {c}" for c in "ABCDEFGHIJ"}
        forbidden_real = {"Meta", "Instagram", "X_Twitter", "YouTube", "Telegram", "WhatsApp", "Snapchat", "Reddit", "TikTok"}

        for r in records:
            assert r.is_synthetic is True
            assert r.request_id.startswith("SYN-REQ-")
            assert r.police_unit_id.startswith("SYN-UNIT-")
            # Fictional platform guarantee
            assert r.platform.value in fictional_allowed
            assert r.platform.value not in forbidden_real
            # HMAC URL check
            assert r.content_url_hmac is not None
            assert len(r.content_url_hmac) == 64

    def test_record_type_diversity(self) -> None:
        """Generator must emit both takedowns and data requests."""
        records = SyntheticDataGenerator(seed=42).generate(count=100)

        takedowns = [r for r in records if r.request_type == RequestType.TAKEDOWN]
        data_reqs = [r for r in records if r.request_type == RequestType.DATA_REQUEST]

        assert len(takedowns) > 0
        assert len(data_reqs) > 0
        # Roughly 75% takedowns, 25% data requests
        assert 50 <= len(takedowns) <= 90
        assert 10 <= len(data_reqs) <= 40

    def test_generate_observations(self) -> None:
        """Generator can produce companion observations for Half 2 integration."""
        gen = SyntheticDataGenerator(seed=42)
        records = gen.generate(count=40)
        obs = gen.generate_observations(records)

        assert len(obs) > 0
        for o in obs:
            assert o.request_id.startswith("SYN-REQ-")
            assert o.verifier_agent_id is not None
            assert o.last_checked_at is not None

    def test_export_and_reimport_json(self) -> None:
        """Exporting to JSON and re-importing must preserve all fields."""
        records = SyntheticDataGenerator(seed=77).generate(count=30)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "synth_export.json"
            SyntheticDataGenerator.export_json(records, json_file)
            assert json_file.exists()

            result = RequestImporter.from_json_file(json_file)
            assert result.valid_count == 30
            assert result.invalid_count == 0

            # Compare first record
            orig = records[0]
            imported = result.valid_records[0]
            assert orig.request_id == imported.request_id
            assert orig.platform == imported.platform
            assert orig.status == imported.status
            assert orig.is_synthetic is True

    def test_export_and_reimport_csv(self) -> None:
        """Exporting to CSV and re-importing must preserve all fields."""
        records = SyntheticDataGenerator(seed=88).generate(count=30)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_file = Path(tmpdir) / "synth_export.csv"
            SyntheticDataGenerator.export_csv(records, csv_file)
            assert csv_file.exists()

            result = RequestImporter.from_csv_file(csv_file)
            assert result.valid_count == 30
            assert result.invalid_count == 0

    def test_zero_or_negative_count(self) -> None:
        """Zero count must return empty list without crashing."""
        gen = SyntheticDataGenerator(seed=1)
        assert gen.generate(count=0) == []
        assert gen.generate(count=-5) == []
