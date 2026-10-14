"""Stable T11 contract shared by the tool adapters.

Adapters should depend on these models and Registry methods, rather than on
another adapter's implementation details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Lock
from time import monotonic
from typing import Any, Protocol
from pydantic import BaseModel, Field


class RateLimit(BaseModel):
    requests_per_minute: int = Field(default=30, ge=1)
    burst: int = Field(default=1, ge=1)


class ToolManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    name: str
    description: str
    version: str = "0.1.0"
    categories: list[str] = Field(min_length=1)
    execution_mode: str = "local"  # local | http | container
    read_only: bool = True
    enabled: bool = True
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class ToolResult(BaseModel):
    tool_id: str
    status: str  # SUCCESS | REJECTED | FAILED | TIMEOUT
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: str | None = None
    error: str | None = None


class ToolAdapter(Protocol):
    manifest: ToolManifest

    def non_destructive_check(self, target: str, params: dict[str, Any]) -> bool: ...
    def run(self, target: str, params: dict[str, Any]) -> ToolResult: ...


@dataclass
class _TokenBucket:
    capacity: int
    refill_per_second: float
    tokens: float
    updated_at: float = field(default_factory=monotonic)
    lock: Lock = field(default_factory=Lock)

    def take(self) -> bool:
        now = monotonic()
        with self.lock:
            elapsed = max(0.0, now - self.updated_at)
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.updated_at = now
            if self.tokens < 1.0:
                return False
            self.tokens -= 1.0
            return True


@dataclass
class ToolRegistry:
    """In-memory registry with per-tool token-bucket limiting and hard timeouts."""

    _entries: dict[str, ToolAdapter] = field(default_factory=dict)
    _audit: list[dict[str, Any]] = field(default_factory=list)
    _buckets: dict[str, _TokenBucket] = field(default_factory=dict)

    def register(self, adapter: ToolAdapter) -> None:
        manifest = adapter.manifest
        if not manifest.read_only:
            raise ValueError("only read-only tools may be registered")
        if manifest.id in self._entries:
            raise ValueError(f"tool already registered: {manifest.id}")
        self._entries[manifest.id] = adapter
        self._buckets[manifest.id] = _TokenBucket(
            capacity=manifest.rate_limit.burst,
            refill_per_second=manifest.rate_limit.requests_per_minute / 60.0,
            tokens=float(manifest.rate_limit.burst),
        )

    def get(self, tool_id: str) -> ToolAdapter:
        try:
            return self._entries[tool_id]
        except KeyError as exc:
            raise KeyError(f"tool not registered: {tool_id}") from exc

    def manifests(self) -> list[ToolManifest]:
        return [adapter.manifest for adapter in self._entries.values()]

    def execute(self, tool_id: str, target: str, params: dict[str, Any] | None = None) -> ToolResult:
        adapter = self.get(tool_id)
        params = params or {}
        manifest = adapter.manifest
        if not manifest.enabled:
            return self._reject(tool_id, target, "tool disabled")
        if not adapter.non_destructive_check(target, params):
            return self._reject(tool_id, target, "non-destructive check failed")
        if not self._buckets[tool_id].take():
            return self._reject(tool_id, target, "rate limit exceeded", audit_status="RATE_LIMITED")

        # Run the adapter in a worker so a hung tool cannot block the API caller.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool_id}")
        future = executor.submit(adapter.run, target, params)
        try:
            result = future.result(timeout=manifest.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            self._audit.append({"tool_id": tool_id, "target": target, "status": "TIMEOUT", "timeout_seconds": manifest.timeout_seconds})
            return ToolResult(tool_id=tool_id, status="TIMEOUT", error=f"execution exceeded {manifest.timeout_seconds}s")
        except Exception as exc:
            self._audit.append({"tool_id": tool_id, "target": target, "status": "FAILED", "error": str(exc)})
            return ToolResult(tool_id=tool_id, status="FAILED", error=str(exc))
        finally:
            # Do not wait for a timed-out worker; cancellation is best effort.
            executor.shutdown(wait=False, cancel_futures=True)

        self._audit.append({"tool_id": tool_id, "target": target, "status": result.status})
        return result

    def _reject(self, tool_id: str, target: str, reason: str, audit_status: str = "REJECTED") -> ToolResult:
        self._audit.append({"tool_id": tool_id, "target": target, "status": audit_status, "reason": reason})
        return ToolResult(tool_id=tool_id, status="REJECTED", error=reason)

    def audit(self) -> list[dict[str, Any]]:
        return list(self._audit)
