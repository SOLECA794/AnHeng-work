"""Independent checks for B's ORM/schema and T11 runtime controls.

Run from repository root after installing requirements:
    python scripts/verify_b_models.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import Base
from src.tools.contract import RateLimit, ToolManifest, ToolRegistry, ToolResult


class QuickAdapter:
    manifest = ToolManifest(
        id="verify-quick",
        name="verification tool",
        description="read-only verification adapter",
        categories=["VULN"],
        rate_limit=RateLimit(requests_per_minute=1, burst=1),
        timeout_seconds=1,
    )

    def non_destructive_check(self, target: str, params: dict) -> bool:
        return target.startswith("http://127.0.0.1")

    def run(self, target: str, params: dict) -> ToolResult:
        return ToolResult(tool_id=self.manifest.id, status="SUCCESS")


class SlowAdapter:
    manifest = ToolManifest(
        id="verify-slow",
        name="slow verification tool",
        description="read-only verification adapter",
        categories=["VULN"],
        timeout_seconds=1,
    )

    def non_destructive_check(self, target: str, params: dict) -> bool:
        return True

    def run(self, target: str, params: dict) -> ToolResult:
        time.sleep(1.2)
        return ToolResult(tool_id=self.manifest.id, status="SUCCESS")


def main() -> None:
    expected_tables = {
        "sys_user", "auth_scope", "site_page", "schedule", "detection_task",
        "finding", "alert", "baseline", "sensitive_rule", "report", "agent_log",
    }
    actual_tables = set(Base.metadata.tables)
    assert actual_tables == expected_tables, sorted(actual_tables)

    registry = ToolRegistry()
    registry.register(QuickAdapter())
    assert registry.execute("verify-quick", "http://127.0.0.1").status == "SUCCESS"
    assert registry.execute("verify-quick", "http://127.0.0.1").status == "REJECTED"
    assert registry.audit()[-1]["status"] == "RATE_LIMITED"

    registry.register(SlowAdapter())
    started = time.monotonic()
    result = registry.execute("verify-slow", "http://127.0.0.1")
    elapsed = time.monotonic() - started
    assert result.status == "TIMEOUT"
    assert elapsed < 1.1, elapsed
    print("B models/schema and T11 rate-limit/timeout checks: OK")


if __name__ == "__main__":
    main()
