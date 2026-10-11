"""Run five isolated, loopback-only sites for the T40 evaluation dataset."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent / "sites"
PORTS = {"a": 18081, "b": 18082, "c": 18083, "d": 18084, "e": 18085}


class FixtureHandler(SimpleHTTPRequestHandler):
    server_version = "FixtureHTTP"
    sys_version = ""

    def __init__(self, *args, site: str, **kwargs):
        self.site = site
        super().__init__(*args, directory=str(ROOT / site), **kwargs)

    def end_headers(self):
        if self.site == "e":
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path)
        if self.site == "a" and path.path == "/search":
            # Deliberately unsafe reflection, confined to a local test server.
            query = parse_qs(path.query).get("q", [""])[0]
            body = f"<!doctype html><title>Search fixture</title><p>Result: {query}</p>"
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.site == "d" and path.path == "/.env":
            self.path = "/synthetic-env.txt"
        elif self.site == "d" and path.path == "/backup.bak":
            self.path = "/synthetic-backup.txt"
        return super().do_GET()


def serve(host: str) -> None:
    servers = []
    try:
        for site, port in PORTS.items():
            handler = partial(FixtureHandler, site=site)
            server = ThreadingHTTPServer((host, port), handler)
            servers.append(server)
            Thread(target=server.serve_forever, daemon=True, name=f"site-{site}").start()
            print(f"{site.upper()}: http://{host}:{port}/", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        while True:
            sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    args = parser.parse_args()
    serve(args.host)
