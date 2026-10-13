"""Unit tests for the CLI commands."""

import json
from pathlib import Path
import tempfile
import pytest

from takedown_tracking.cli import main


class TestCLI:
    """Test suite for CLI subcommands."""

    def test_cli_generate_json(self) -> None:
        """CLI generate should output valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_out.json"
            exit_code = main(["generate", "--count", "25", "--seed", "42", "--output", str(out_file)])
            assert exit_code == 0
            assert out_file.exists()

            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert len(data) == 25
            assert data[0]["is_synthetic"] is True

    def test_cli_generate_csv(self) -> None:
        """CLI generate should output valid CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_out.csv"
            exit_code = main(["generate", "--count", "15", "--seed", "42", "--output", str(out_file)])
            assert exit_code == 0
            assert out_file.exists()

    def test_cli_import_and_metrics(self) -> None:
        """CLI import and metrics on valid data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = Path(tmpdir) / "data.json"
            main(["generate", "--count", "20", "--output", str(data_file)])

            # Import
            imp_code = main(["import", "--input", str(data_file)])
            assert imp_code == 0

            # Metrics
            met_code = main(["metrics", "--input", str(data_file)])
            assert met_code == 0

    def test_cli_export_scoreboard(self) -> None:
        """CLI export-scoreboard should write valid scoreboard JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = Path(tmpdir) / "data.json"
            sb_file = Path(tmpdir) / "scoreboard.json"
            main(["generate", "--count", "30", "--output", str(data_file)])

            exit_code = main([
                "export-scoreboard",
                "--input", str(data_file),
                "--output", str(sb_file),
                "--k-threshold", "10",
            ])
            assert exit_code == 0
            assert sb_file.exists()

            with open(sb_file, "r", encoding="utf-8") as f:
                sb_data = json.load(f)

            assert sb_data["schema_version"] in ("1.0.0", "2.0.0")
            assert "metadata" in sb_data
            assert "overall_performance" in sb_data
            assert "platform_breakdown" in sb_data

    def test_cli_import_quarantine_failures(self) -> None:
        """CLI import should write failures to quarantine file when bad records present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_data_file = Path(tmpdir) / "bad.json"
            quarantine_file = Path(tmpdir) / "quarantined.json"

            # Create bad record
            with open(bad_data_file, "w", encoding="utf-8") as f:
                json.dump([{"invalid_record": True}], f)

            exit_code = main([
                "import",
                "--input", str(bad_data_file),
                "--quarantine", str(quarantine_file),
            ])
            # Exits with 1 due to invalid row quarantined
            assert exit_code == 1
            assert quarantine_file.exists()

            with open(quarantine_file, "r", encoding="utf-8") as f:
                q_data = json.load(f)
            assert len(q_data) > 0
            assert q_data[0]["error_code"] == "MISSING_REQUIRED_FIELD"
