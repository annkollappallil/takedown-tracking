"""Regression tests for HMAC-SHA-256 URL identifiers and secret handling (review item 8)."""

import os
import pytest

from takedown_tracking.models.request import NONPROD_TEST_HMAC_KEY, compute_url_hmac


class TestHMACUrls:
    """Validate HMAC-SHA-256 URL pseudonymization, collision resistance, and fail-closed safety."""

    def test_same_url_and_key_produce_identical_hmac(self) -> None:
        """Determinism: Same canonicalized URL and key must produce identical HMAC."""
        url = "https://platform.internal/posts/12345"
        key = "SECRET_KEY_FOR_TESTING_12345"

        h1 = compute_url_hmac(url, secret_key=key)
        h2 = compute_url_hmac(url, secret_key=key)
        # Whitespace stripping
        h3 = compute_url_hmac("  " + url + "  ", secret_key=key)

        assert h1 == h2 == h3
        assert len(h1) == 64

    def test_different_urls_produce_different_hmacs(self) -> None:
        """Different URLs under the same key must produce different identifiers."""
        key = "SECRET_KEY_FOR_TESTING_12345"
        h1 = compute_url_hmac("https://platform.internal/posts/1", secret_key=key)
        h2 = compute_url_hmac("https://platform.internal/posts/2", secret_key=key)

        assert h1 != h2

    def test_different_keys_produce_different_hmacs(self) -> None:
        """Same URL under different keys must produce different HMACs."""
        url = "https://platform.internal/posts/12345"
        h1 = compute_url_hmac(url, secret_key="KEY_A")
        h2 = compute_url_hmac(url, secret_key="KEY_B")

        assert h1 != h2

    def test_fail_closed_in_production_without_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail closed: processing real data (is_synthetic=False) without secret key MUST raise ValueError."""
        monkeypatch.delenv("TAKEDOWN_HMAC_SECRET", raising=False)

        with pytest.raises(ValueError, match="TAKEDOWN_HMAC_SECRET environment variable is required"):
            compute_url_hmac("https://platform.internal/real_url", secret_key=None, is_synthetic=False)

    def test_synthetic_mode_permits_nonprod_test_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Synthetic demonstration mode permits non-production test key safely."""
        monkeypatch.delenv("TAKEDOWN_HMAC_SECRET", raising=False)

        hmac_val = compute_url_hmac("synthetic://test/evidence/1", secret_key=None, is_synthetic=True)
        assert len(hmac_val) == 64

    def test_secret_never_echoed_in_error_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secret values must never be echoed in exception messages."""
        monkeypatch.delenv("TAKEDOWN_HMAC_SECRET", raising=False)
        try:
            compute_url_hmac("", secret_key="MY_SUPER_SECRET_DEPLOYMENT_KEY")
        except ValueError as e:
            assert "MY_SUPER_SECRET_DEPLOYMENT_KEY" not in str(e)
