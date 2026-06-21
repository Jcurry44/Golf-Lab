from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app_common import DEFAULT_DB, WEB_ROOT, connect, int_param, json_bytes, str_param
from golf_lab_analytics import (
    course_card,
    course_cards,
    database_summary,
    event_board,
    model_board,
    player_card,
    player_cards,
    warehouse_health,
)


class GolfLabHandler(BaseHTTPRequestHandler):
    server_version = "GolfLab/0.1"

    def send_json(self, payload, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path: str) -> None:
        if path in ("", "/"):
            file_path = WEB_ROOT / "index.html"
        else:
            safe = Path(path.lstrip("/"))
            file_path = WEB_ROOT / safe
        if not file_path.exists() or not file_path.is_file() or WEB_ROOT not in file_path.resolve().parents:
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8"
        if file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_static(parsed.path)
            return
        params = parse_qs(parsed.query)
        db_path: Path = self.server.db_path  # type: ignore[attr-defined]
        try:
            with connect(db_path, readonly=True) as conn:
                if parsed.path == "/api/summary":
                    self.send_json(database_summary(conn))
                    return
                if parsed.path == "/api/event":
                    self.send_json(event_board(conn, str_param(params, "event_id")))
                    return
                if parsed.path == "/api/player-cards":
                    self.send_json(player_cards(conn, str_param(params, "event_id"), int_param(params, "limit", 250, 1, 5000)))
                    return
                if parsed.path == "/api/player":
                    self.send_json(player_card(conn, str_param(params, "id"), str_param(params, "event_id")))
                    return
                if parsed.path == "/api/course-cards":
                    self.send_json(course_cards(conn, int_param(params, "limit", 250, 1, 500)))
                    return
                if parsed.path == "/api/course":
                    self.send_json(course_card(conn, str_param(params, "id")))
                    return
                if parsed.path == "/api/model-board":
                    self.send_json(model_board(conn, str_param(params, "event_id"), int_param(params, "limit", 100, 1, 500)))
                    return
                if parsed.path == "/api/warehouse-health":
                    self.send_json(warehouse_health(conn))
                    return
            self.send_error(404)
        except (sqlite3.Error, ValueError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except FileNotFoundError:
            self.send_json({"error": f"Database not found at {db_path}. Run golf_lab_import.py first."}, status=503)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Golf Lab local API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), GolfLabHandler)
    server.db_path = args.db  # type: ignore[attr-defined]
    print(f"Golf Lab running at http://{args.host}:{args.port}/")
    print(f"Database: {args.db}")
    server.serve_forever()


if __name__ == "__main__":
    main()
