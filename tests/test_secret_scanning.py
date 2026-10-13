"""Secret scanner audit regression test ensuring zero credentials in tests or repository."""

from pathlib import Path
import re
import pytest

# High-entropy credential patterns (GitHub PAT, OAuth tokens, AWS access keys, private keys)
TOKEN_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    re.compile(r"gho_[A-Za-z0-9_]{20,255}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


class TestSecretScanning:
    """Verify that test source files, configs, and repository files contain zero accidental credentials."""

    def test_no_credentials_in_tests_directory(self) -> None:
        """All files in tests/ must be completely clean of candidate credentials."""
        repo_root = Path(__file__).resolve().parent.parent
        tests_dir = repo_root / "tests"

        for test_file in tests_dir.glob("**/*.py"):
            content = test_file.read_text(encoding="utf-8")
            for pattern in TOKEN_PATTERNS:
                matches = pattern.findall(content)
                assert len(matches) == 0, f"Potential credential pattern found in {test_file.name}: {matches}"

    def test_no_credentials_in_source_directory(self) -> None:
        """All files in src/ must be clean of candidate credentials."""
        repo_root = Path(__file__).resolve().parent.parent
        src_dir = repo_root / "src"

        for src_file in src_dir.glob("**/*.py"):
            content = src_file.read_text(encoding="utf-8")
            for pattern in TOKEN_PATTERNS:
                matches = pattern.findall(content)
                assert len(matches) == 0, f"Potential credential pattern found in {src_file.name}: {matches}"

    def test_gitleaks_configuration_has_no_broad_test_allowlist(self) -> None:
        """The .gitleaks.toml configuration must not contain broad tests/ exclusions."""
        repo_root = Path(__file__).resolve().parent.parent
        gitleaks_cfg = repo_root / ".gitleaks.toml"
        assert gitleaks_cfg.exists()

        content = gitleaks_cfg.read_text(encoding="utf-8")
        assert "^tests/.*" not in content
        assert "'''^tests/" not in content
