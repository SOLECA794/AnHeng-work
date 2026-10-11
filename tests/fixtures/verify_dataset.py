"""Check that the versioned T40 labels still match the served fixtures."""

from __future__ import annotations

import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from site_server import FixtureHandler, PORTS


DATASET = Path(__file__).resolve().parents[1] / "dataset" / "v1.0.0.json"


def main() -> None:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    assert data["version"] == "1.0.0"
    assert set(data["sites"]) == set("ABCDE")
    cases = data["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(isinstance(case["positive"], bool) for case in cases)
    assert all(case["category"] and case["evidence"] for case in cases)
    assert all(case["site"] in data["sites"] for case in cases)
    assert all({True, False} <= {case["positive"] for case in cases if case["category"] == category}
               for category in {case["category"] for case in cases})

    servers = {}
    try:
        for site in "abcde":
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(FixtureHandler, site=site))
            servers[site.upper()] = server
            Thread(target=server.serve_forever, daemon=True).start()

        def fetch(site: str, path: str):
            server = servers[site]
            with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=3) as response:
                return response.read(), response.headers

        for case in cases:
            fetch(case["site"], case["path"])

        a_xss, _ = fetch("A", "/search?q=%3Cb%3Efixture-probe%3C%2Fb%3E")
        assert b"<b>fixture-probe</b>" in a_xss
        a_listing, _ = fetch("A", "/listing/")
        assert b"probe.txt" in a_listing
        b_risk, _ = fetch("B", "/")
        assert b"collector.example.invalid" in b_risk
        b_clean, _ = fetch("B", "/about.html")
        assert b"collector.example.invalid" not in b_clean
        baseline = (DATASET.parent / data["baseline"]).read_bytes()
        c_current, _ = fetch("C", "/")
        c_baseline, _ = fetch("C", "/baseline.html")
        assert c_current != baseline and c_baseline == baseline
        d_env, _ = fetch("D", "/.env")
        assert b"fixture-only-token" in d_env
        d_backup, _ = fetch("D", "/backup.bak")
        assert b"synthetic backup" in d_backup
        e_page, e_headers = fetch("E", "/")
        assert b"collector.example.invalid" not in e_page
        assert b"fixture-only-token" not in e_page
        assert e_headers["Content-Security-Policy"]
        assert e_headers["X-Content-Type-Options"] == "nosniff"
        print(f"OK: {len(cases)} labeled cases, five isolated sites, baseline and markers verified")
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
