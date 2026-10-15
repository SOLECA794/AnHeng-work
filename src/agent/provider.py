"""Platform-neutral interface for agent execution (T10)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Iterable, Protocol


@dataclass(frozen=True)
class AgentError(Exception):
    provider_code: int | None
    project_code: int
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class AgentEvent:
    message_id: str | None
    source: str | None
    content: str
    done: bool = False
    mode: str | None = None
    node_id: str | None = None
    raw: dict[str, Any] | None = None


class AgentProvider(Protocol):
    def execute(self, sid: str, agent_id: str, inputs: dict[str, Any], stream: bool = False) -> Any: ...
    def stop(self, sid: str) -> dict[str, Any]: ...
    def async_submit(self, agent_id: str, inputs: dict[str, Any], sid: str | None = None) -> str: ...
    def async_status(self, task_id: str) -> int: ...
    def async_messages(self, task_id: str, full: bool = True) -> dict[str, Any]: ...
    def resume(self, message_id: str, inputs: dict[str, Any]) -> Any: ...
    def upload(self, file: BinaryIO, filename: str) -> str: ...
    def download(self, file_id: str) -> bytes: ...


def parse_sse_line(line: str) -> AgentEvent | None:
    """Parse one ``data:`` line from 恒脑 execute stream."""
    import json

    if not line.startswith("data:"):
        return None
    payload = json.loads(line[5:].strip())
    if payload.get("code", 0) != 0:
        raise AgentError(payload.get("code"), 50001, payload.get("msg", "agent request failed"), True)
    data = payload.get("data") or {}
    return AgentEvent(
        message_id=data.get("message_id"),
        source=data.get("from"),
        content=data.get("content", ""),
        done=bool(data.get("done", False)),
        mode=data.get("mode"),
        node_id=data.get("node_id"),
        raw=data,
    )


def aggregate_events(events: Iterable[AgentEvent]) -> list[dict[str, Any]]:
    grouped: dict[str, list[AgentEvent]] = {}
    for event in events:
        key = event.message_id or f"event-{len(grouped)}"
        grouped.setdefault(key, []).append(event)
    return [
        {
            "message_id": message_id,
            "source": chunks[0].source,
            "text": "".join(chunk.content for chunk in chunks),
            "done": chunks[-1].done,
            "mode": chunks[0].mode,
        }
        for message_id, chunks in grouped.items()
    ]
