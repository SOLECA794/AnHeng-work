"""Stable T11 contract shared by the tool adapters.

Adapters should depend on these models and Registry methods, rather than on
another adapter's implementation details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Callable, Protocol
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
class ToolRegistry:
    """In-memory reference registry; persistence can be added behind this API."""

    _entries: dict[str, ToolAdapter] = field(default_factory=dict)
    _audit: list[dict[str, Any]] = field(default_factory=list)

    def register(self, adapter: ToolAdapter) -> None:
        manifest = adapter.manifest
        if not manifest.read_only:
            raise ValueError("only read-only tools may be registered")
        if manifest.id in self._entries:
            raise ValueError(f"tool already registered: {manifest.id}")
        self._entries[manifest.id] = adapter

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
        result = adapter.run(target, params)
        self._audit.append({"tool_id": tool_id, "target": self._audit_target(target), "status": result.status})
        return result

    def _reject(self, tool_id: str, target: str, reason: str) -> ToolResult:
        self._audit.append({"tool_id": tool_id, "target": self._audit_target(target), "status": "REJECTED", "reason": reason})
        return ToolResult(tool_id=tool_id, status="REJECTED", error=reason)

    @staticmethod
    def _audit_target(target: str) -> str:
        # URLs may embed credentials, query tokens or sensitive path segments.
        # A fingerprint preserves correlation without copying them into logs.
        if not isinstance(target, str):
            return "[INVALID_TARGET]"
        return "sha256:" + hashlib.sha256(target.encode("utf-8")).hexdigest()

    def audit(self) -> list[dict[str, Any]]:
        return list(self._audit)
