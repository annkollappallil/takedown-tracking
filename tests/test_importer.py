"""Unit tests for the validator and importer."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import pytest

from takedown_tracking.ingest.importer import RequestImporter
from takedown_tracking.ingest.validator import RequestValidator
from takedown_tracking.models.request import Platform, RequestStatus, RequestType


class TestRequestValidatorAndImporter:
    """Test suite for RequestValidator and RequestImporter."""

    def test_validate_valid_row(self) -> None:
        """A complete valid row should produce a RequestRecord with no failures."""
        row = {
            "request_id": "SYN-REQ-2026-00001",
            "is_synthetic": True,
            "request_type": "takedown",
            "platform": "Meta",
            "category": "CSAM",
            "priority": "emergency_life_threat",
            "sent_at": "2026-09-01T10:00:00+00:00",
            "acknowledged_at": "2026-09-01T10:15:00+00:00",
            "claimed_removal_at": "2026-09-01T11:30:00+00:00",
            "data_provided_at": None,
            "police_unit_id": "SYN-UNIT-KL-01",
            "is_complete_request": True,
            "incompletion_reason": None,
            "status": "claimed_removed",
            "rejection_reason": None,
            "content_url_hash": "a" * 64,
        }

        record, failures = RequestValidator.validate_row(row, row_index=1)
        assert failures == []
        assert record is not None
        assert record.request_id == "SYN-REQ-2026-00001"
        assert record.platform == Platform.META
        assert record.status == RequestStatus.CLAIMED_REMOVED

    def test_missing_mandatory_field(self) -> None:
        """Missing required fields must fail gracefully with safe error codes."""
        row = {
            "request_id": "SYN-REQ-001",
            # missing is_synthetic, platform, etc.
        }

        record, failures = RequestValidator.validate_row(row, row_index=5)
        assert record is None
        assert len(failures) > 0
        error_codes = [f.error_code for f in failures]
        assert "MISSING_REQUIRED_FIELD" in error_codes
        # Guarantee no sensitive data is printed
        for f in failures:
            assert "MISSING_REQUIRED_FIELD" in f.error_code

    def test_temporal_inversion(self) -> None:
        """Removal timestamp prior to sent timestamp must be flagged."""
        row = {
            "request_id": "SYN-REQ-2026-00002",
            "is_synthetic": True,
            "request_type": "takedown",
            "platform": "YouTube",
            "category": "HateSpeech",
            "priority": "routine",
            "sent_at": "2026-09-02T12:00:00+00:00",
            "claimed_removal_at": "2026-09-01T12:00:00+00:00",  # Prior to sent_at!
            "police_unit_id": "SYN-UNIT-02",
            "is_complete_request": True,
            "status": "claimed_removed",
        }

        record, failures = RequestValidator.validate_row(row, row_index=2)
        assert record is None
        error_codes = [f.error_code for f in failures]
        assert "TEMPORAL_INVERSION" in error_codes

    def test_inconsistent_status_missing_removal_time(self) -> None:
        """Status claimed_removed without a removal timestamp must be flagged."""
        row = {
            "request_id": "SYN-REQ-2026-00003",
            "is_synthetic": True,
            "request_type": "takedown",
            "platform": "Meta",
            "category": "Defamation",
            "priority": "routine",
            "sent_at": "2026-09-02T12:00:00+00:00",
            "claimed_removal_at": None,
            "police_unit_id": "SYN-UNIT-02",
            "is_complete_request": True,
            "status": "claimed_removed",  # Claims removed but no timestamp
        }

        record, failures = RequestValidator.validate_row(row, row_index=3)
        assert record is None
        error_codes = [f.error_code for f in failures]
        assert "INCONSISTENT_STATUS" in error_codes

    def test_invalid_enum_values(self) -> None:
        """Unrecognized platforms or categories must be rejected with errors."""
        row = {
            "request_id": "SYN-REQ-2026-00004",
            "is_synthetic": True,
            "request_type": "invalid_type",
            "platform": "FakeIntermediary999",
            "category": "UnrecognizedCategory",
            "priority": "urgent",
            "sent_at": "2026-09-02T12:00:00+00:00",
            "police_unit_id": "SYN-UNIT-02",
            "is_complete_request": True,
            "status": "pending",
        }

        record, failures = RequestValidator.validate_row(row, row_index=4)
        assert record is None
        error_codes = [f.error_code for f in failures]
        assert "INVALID_ENUM_VALUE" in error_codes

    def test_invalid_hash_format(self) -> None:
        """Non-SHA256 content hashes must be rejected."""
        row = {
            "request_id": "SYN-REQ-2026-00005",
            "is_synthetic": True,
            "request_type": "takedown",
            "platform": "Meta",
            "category": "Terrorism",
            "priority": "urgent",
            "sent_at": "2026-09-02T12:00:00+00:00",
            "police_unit_id": "SYN-UNIT-02",
            "is_complete_request": True,
            "status": "pending",
            "content_url_hash": "not-a-valid-sha256-hash",
        }

        record, failures = RequestValidator.validate_row(row, row_index=5)
        assert record is None
        error_codes = [f.error_code for f in failures]
        assert "INVALID_HASH_FORMAT" in error_codes

    def test_importer_file_not_found(self) -> None:
        """Attempting to import nonexistent file must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            RequestImporter.from_json_file("nonexistent_path_123.json")

    def test_importer_invalid_json_root(self) -> None:
        """Importing JSON that is not an array must raise ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"not_a_list": True}, f)
            temp_path = f.name

        try:
            with pytest.raises(ValueError):
                RequestImporter.from_json_file(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)
