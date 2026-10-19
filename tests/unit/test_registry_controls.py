import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from src.tools.contract import ToolRegistry, ToolManifest, ToolResult, _TokenBucket


class Adapter:
    def __init__(self):
        self.manifest = ToolManifest(id="demo", name="demo", description="test",
                                    categories=["VULN"], timeout_seconds=1,
                                    rate_limit={"requests_per_minute": 600, "burst": 10})

    def non_destructive_check(self, target, params):
        return True

    def run(self, target, params):
        return ToolResult(tool_id="demo", status="SUCCESS")


class RegistryTests(unittest.TestCase):
    def test_unknown_and_changed_read_only(self):
        r = ToolRegistry()
        self.assertEqual(r.execute("missing", "test").status, "REJECTED")
        a = Adapter()
        r.register(a)
        a.manifest.read_only = False
        self.assertEqual(r.execute("demo", "test").status, "REJECTED")

    def test_concurrent_bucket_and_refill(self):
        bucket = _TokenBucket(3, 1, 3, updated_at=100)
        with patch("src.tools.contract.monotonic", return_value=100):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(lambda _: bucket.take(), range(20))), 3)
        with patch("src.tools.contract.monotonic", return_value=101):
            self.assertTrue(bucket.take())
            self.assertFalse(bucket.take())

    def test_timeout_keeps_busy_until_worker_finishes(self):
        release = threading.Event()
        done = threading.Event()
        a = Adapter()
        def run(target, params):
            try:
                release.wait(5)
                return ToolResult(tool_id="demo", status="SUCCESS")
            finally:
                done.set()
        a.run = run
        r = ToolRegistry()
        r.register(a)
        target = "https://fixture.invalid/private-secret?token=query-secret"
        try:
            self.assertEqual(r.execute("demo", target).status, "TIMEOUT")
            self.assertTrue(r.audit()[-1]["target"].startswith("sha256:"))
            self.assertNotIn("secret", str(r.audit()))
            self.assertEqual(r.execute("demo", "test").error, "tool still running")
        finally:
            release.set()
            self.assertTrue(done.wait(2))

    def test_exceptions_and_invalid_results(self):
        for behavior in ("exception", "invalid"):
            a = Adapter()
            def run(target, params):
                if behavior == "exception":
                    raise ValueError("secret-token")
                return None
            a.run = run
            r = ToolRegistry()
            r.register(a)
            result = r.execute("demo", "https://fixture.invalid/private-secret?token=query-secret")
            self.assertEqual(result.status, "FAILED")
            self.assertNotIn("secret-token", str(r.audit()) + str(result))
            self.assertTrue(r.audit()[-1]["target"].startswith("sha256:"))
            self.assertNotIn("query-secret", str(r.audit()))


if __name__ == "__main__":
    unittest.main()
