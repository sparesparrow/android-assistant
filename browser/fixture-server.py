#!/usr/bin/env python3
"""Serves browser/fixtures on 127.0.0.1 and records every request under /canary/ (one line each in
the file given by --hits). An agent that follows an injected instruction hits a canary URL."""
import argparse
import http.server
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--hits", required=True)
    args = parser.parse_args()
    hits = Path(args.hits)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(FIXTURES), **kw)

        def do_GET(self):  # noqa: N802 - http.server API
            if self.path.startswith("/canary/"):
                with hits.open("a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} GET {self.path}\n")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"verified\n")
                return
            super().do_GET()

        def do_POST(self):  # noqa: N802
            with hits.open("a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} POST {self.path}\n")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
