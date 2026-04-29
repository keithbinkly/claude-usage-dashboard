#!/usr/bin/env python3
"""Tiny dev server for the Claude Usage Monitor dashboard.

Serves the dashboard directory (same as `python3 -m http.server`) AND
exposes live API endpoints so page refreshes show fresh data without
regenerating the whole dashboard HTML:

  /api/rate-limits      — primary account OAuth usage (pacing gauges)
  /api/rate-limits-max  — second account OAuth usage (if configured)
  /api/pacing           — combined pacing computation

Usage:
    python3 serve.py [--port 8922] [--root .]
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Import fetch_rate_limits_live from the sibling module
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from claude_usage import fetch_rate_limits_live, fetch_rate_limits_for_max, compute_pacing_live


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/rate-limits-max" or self.path.startswith("/api/rate-limits-max?"):
            self.handle_rate_limits_max()
            return
        if self.path == "/api/rate-limits" or self.path.startswith("/api/rate-limits?"):
            self.handle_rate_limits()
            return
        if self.path == "/api/pacing" or self.path.startswith("/api/pacing?"):
            self.handle_pacing()
            return
        super().do_GET()

    def _json_response(self, data: dict) -> None:
        body = json.dumps(data or {"error": "no data"}).encode("utf-8")
        self.send_response(200 if data and "error" not in data else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_rate_limits(self):
        try:
            data = fetch_rate_limits_live()
        except Exception as e:
            data = {"error": str(e)}
        self._json_response(data or {"error": "fetch returned None"})

    def handle_rate_limits_max(self):
        try:
            data = fetch_rate_limits_for_max()
        except Exception as e:
            data = {"error": str(e)}
        self._json_response(data or {"error": "max snapshot missing or fetch failed"})

    def handle_pacing(self):
        try:
            data = compute_pacing_live()
        except Exception as e:
            data = {"error": str(e)}
        self._json_response(data or {"error": "pacing compute returned None"})

    def log_message(self, fmt, *args):
        # Quieter than the default — one line per request, no timestamp noise
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8922)
    ap.add_argument("--root", default=".", help="directory to serve")
    args = ap.parse_args()

    import os
    os.chdir(args.root)
    server = ThreadingHTTPServer(("", args.port), DashboardHandler)
    print(f"Serving {args.root} on http://localhost:{args.port}")
    print(f"Rate-limit API: http://localhost:{args.port}/api/rate-limits")
    print(f"Max account API: http://localhost:{args.port}/api/rate-limits-max")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
