"""Lightweight standalone HTTP server for the Takedown Accountability Platform."""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Union
import urllib.parse

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.ingest.importer import RequestImporter
from takedown_tracking.models.scoreboard import PublicScoreboard
from takedown_tracking.privacy.filter import PrivacyFilter

STATIC_DIR = Path(__file__).parent / "static"


class TakedownRequestHandler(BaseHTTPRequestHandler):
    """Handles static web requests and public API queries."""

    scoreboard: Optional[PublicScoreboard] = None

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default stderr spam in production/tests while keeping cleanly loggable."""
        pass

    def send_json(self, status_code: int, data: object) -> None:
        """Send a formatted JSON response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, file_path: Path, content_type: str) -> None:
        """Serve a static file safely from STATIC_DIR."""
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "File not found")
            return

        with open(file_path, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path in ("/styles.css", "/static/styles.css"):
            self.serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
        elif path in ("/app.js", "/static/app.js"):
            self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        elif path == "/api/scoreboard":
            if self.scoreboard is None:
                # Generate fallback demo scoreboard
                gen = SyntheticDataGenerator(seed=42)
                records = gen.generate(count=200)
                filter_engine = PrivacyFilter(k_threshold=10, is_demonstration_mode=True)
                self.scoreboard = filter_engine.filter_and_publish(records)
            self.send_json(200, self.scoreboard.to_dict())
        elif path == "/api/health":
            self.send_json(200, {"status": "healthy", "service": "takedown-tracking", "version": "0.1.0"})
        else:
            self.send_error(404, "Not Found")


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    scoreboard: Optional[PublicScoreboard] = None,
    data_file: Optional[Union[str, Path]] = None,
) -> None:
    """Run the web server."""
    if scoreboard is not None:
        TakedownRequestHandler.scoreboard = scoreboard
    elif data_file is not None:
        p = Path(data_file)
        if p.suffix.lower() == ".csv":
            result = RequestImporter.from_csv_file(p)
        else:
            result = RequestImporter.from_json_file(p)
        filter_engine = PrivacyFilter(k_threshold=10, is_demonstration_mode=True)
        TakedownRequestHandler.scoreboard = filter_engine.filter_and_publish(
            result.valid_records, invalid_records_count=result.invalid_count
        )
    else:
        # Default synthetic calibration data
        gen = SyntheticDataGenerator(seed=42)
        records = gen.generate(count=300)
        filter_engine = PrivacyFilter(k_threshold=10, is_demonstration_mode=True)
        TakedownRequestHandler.scoreboard = filter_engine.filter_and_publish(records)

    server = HTTPServer((host, port), TakedownRequestHandler)
    print(f"[*] Takedown Accountability Platform running at http://{host}:{port}/")
    print("Press Ctrl+C to terminate.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()
