"""CSV and JSON importer with privacy-safe error isolation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Union

from takedown_tracking.ingest.validator import RequestValidator
from takedown_tracking.models.metrics import ValidationFailure
from takedown_tracking.models.request import RequestRecord


@dataclass
class ImportResult:
    """Encapsulates the outcome of an import job."""

    valid_records: List[RequestRecord]
    failures: List[ValidationFailure]
    total_rows: int

    @property
    def valid_count(self) -> int:
        return len(self.valid_records)

    @property
    def invalid_count(self) -> int:
        # Number of unique rows that experienced validation failure
        return len({f.row_index for f in self.failures})

    def summary(self) -> Dict[str, Any]:
        return {
            "total_rows_processed": self.total_rows,
            "valid_records_imported": self.valid_count,
            "invalid_rows_quarantined": self.invalid_count,
            "total_validation_errors": len(self.failures),
            "error_breakdown_by_code": self._error_code_distribution(),
        }

    def _error_code_distribution(self) -> Dict[str, int]:
        dist: Dict[str, int] = {}
        for f in self.failures:
            dist[f.error_code] = dist.get(f.error_code, 0) + 1
        return dist


class RequestImporter:
    """Parses and validates requests from CSV and JSON sources."""

    @classmethod
    def from_json_file(cls, path: Union[str, Path]) -> ImportResult:
        """Import records from a JSON file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Root JSON element must be an array of request records.")

        valid_records: List[RequestRecord] = []
        failures: List[ValidationFailure] = []

        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                failures.append(
                    ValidationFailure(
                        row_index=idx,
                        field="root",
                        error_code="INVALID_RECORD_FORMAT",
                        message="Record must be a JSON object.",
                    )
                )
                continue

            record, row_failures = RequestValidator.validate_row(item, row_index=idx)
            if record:
                valid_records.append(record)
            if row_failures:
                failures.extend(row_failures)

        return ImportResult(
            valid_records=valid_records,
            failures=failures,
            total_rows=len(data),
        )

    @classmethod
    def from_csv_file(cls, path: Union[str, Path]) -> ImportResult:
        """Import records from a CSV file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        valid_records: List[RequestRecord] = []
        failures: List[ValidationFailure] = []
        total_rows = 0

        with open(file_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):  # Header is row 1
                total_rows += 1
                record, row_failures = RequestValidator.validate_row(row, row_index=idx)
                if record:
                    valid_records.append(record)
                if row_failures:
                    failures.extend(row_failures)

        return ImportResult(
            valid_records=valid_records,
            failures=failures,
            total_rows=total_rows,
        )
