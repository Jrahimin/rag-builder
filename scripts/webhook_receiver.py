"""Tiny local receiver for exercising the Phase 6 webhook contract without Docker."""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def build_handler(secret: str) -> type[BaseHTTPRequestHandler]:
    seen: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            event_id = self.headers.get("X-APE-Event-ID", "")
            timestamp = self.headers.get("X-APE-Timestamp", "")
            signature = self.headers.get("X-APE-Signature", "").removeprefix("v1=")
            try:
                uuid.UUID(event_id)
                if abs(time.time() - int(timestamp)) > 300:
                    raise ValueError("stale timestamp")
            except ValueError:
                self.send_error(401, "invalid event identity/timestamp")
                return
            signed = timestamp.encode() + b"." + event_id.encode() + b"." + body
            expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                self.send_error(401, "invalid signature")
                return
            duplicate = event_id in seen
            seen.add(event_id)
            payload = json.loads(body)
            print(
                json.dumps(
                    {
                        "duplicate": duplicate,
                        "event_id": event_id,
                        "type": payload.get("type"),
                        "data": payload.get("data"),
                    },
                    ensure_ascii=False,
                )
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secret",
        required=True,
        help="signing_secret returned by endpoint create",
    )
    parser.add_argument("--port", type=int, default=9010)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(args.secret))
    print(f"Listening on http://127.0.0.1:{args.port}/webhooks/ape")
    server.serve_forever()


if __name__ == "__main__":
    main()
