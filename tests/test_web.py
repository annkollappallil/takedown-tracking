"""Unit and integration tests for the web server and API endpoints."""

from http.server import HTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error
import pytest

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.privacy.filter import PrivacyFilter
from takedown_tracking.web.app import TakedownRequestHandler


@pytest.fixture(scope="module")
def web_server():
    """Spin up a test HTTP server on localhost with an ephemeral port."""
    # Seed test scoreboard
    gen = SyntheticDataGenerator(seed=42)
    records = gen.generate(count=60)
    pfilter = PrivacyFilter(k_threshold=5, is_demonstration_mode=True)
    TakedownRequestHandler.scoreboard = pfilter.filter_and_publish(records)

    server = HTTPServer(("127.0.0.1", 0), TakedownRequestHandler)
    host, port = server.server_address

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    yield base_url

    server.shutdown()
    server.server_close()


class TestWebServer:
    """Test suite for HTTP endpoints."""

    def test_serve_index_html(self, web_server: str) -> None:
        """GET / must serve HTML with status 200."""
        req = urllib.request.Request(f"{web_server}/")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "Takedown Accountability" in body
            assert "DEMONSTRATION MODE" in body

    def test_serve_css(self, web_server: str) -> None:
        """GET /styles.css must serve CSS with status 200."""
        req = urllib.request.Request(f"{web_server}/styles.css")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert "text/css" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "--accent-indigo" in body

    def test_serve_javascript(self, web_server: str) -> None:
        """GET /app.js must serve JS with status 200."""
        req = urllib.request.Request(f"{web_server}/app.js")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert "application/javascript" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "fetchAndRenderScoreboard" in body

    def test_api_health(self, web_server: str) -> None:
        """GET /api/health must return healthy status."""
        req = urllib.request.Request(f"{web_server}/api/health")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "healthy"
            assert data["service"] == "takedown-tracking"

    def test_api_scoreboard(self, web_server: str) -> None:
        """GET /api/scoreboard must return full privacy-filtered scoreboard."""
        req = urllib.request.Request(f"{web_server}/api/scoreboard")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert "application/json" in resp.headers.get("Content-Type", "")
            data = json.loads(resp.read().decode("utf-8"))
            assert data["schema_version"] in ("1.0.0", "2.0.0")
            assert "metadata" in data
            assert data["metadata"]["is_demonstration_mode"] is True
            assert "overall_performance" in data
            assert "platform_breakdown" in data
            assert "category_breakdown" in data

    def test_not_found(self, web_server: str) -> None:
        """Nonexistent paths must return 404."""
        req = urllib.request.Request(f"{web_server}/nonexistent_endpoint")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404
