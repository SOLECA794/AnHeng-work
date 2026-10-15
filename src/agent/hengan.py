"""恒脑 OpenAPI implementation of :class:`AgentProvider` (T10)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any, BinaryIO

import httpx

from src.agent.provider import AgentError, AgentEvent, AgentProvider, parse_sse_line
from src.core.errors import ErrorCode


class HenganSigner:
    def __init__(self, app_key: str, app_secret: str) -> None:
        if not app_key or not app_secret:
            raise ValueError("Hengan app key and app secret are required")
        self.app_key = app_key
        self.app_secret = app_secret

    def headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        data = f"{timestamp}\n{self.app_secret}\n{self.app_key}"
        digest = hmac.new(self.app_secret.encode(), data.encode(), hashlib.sha256).digest()
        sign = timestamp + base64.b64encode(digest).decode()
        return {"appKey": self.app_key, "sign": sign}


class HenganAgentProvider(AgentProvider):
    """Synchronous adapter; callers can run it in their task worker/SSE route."""

    _paths = {
        "execute": "/open/api/v2/agent/execute",
        "stop": "/open/api/v2/agent/execute/stop",
        "submit": "/open/api/v2/agent/task/submit",
        "status": "/open/api/v2/agent/task/status",
        "messages": "/open/api/v2/agent/task/messages",
        "resume": "/open/api/v2/agent/execute/resume",
        "upload": "/open/api/v2/agent/file/upload",
        "download": "/open/api/v2/agent/file/download",
    }

    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        *,
        timeout_seconds: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signer = HenganSigner(app_key, app_secret)
        self._timeout = timeout_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @staticmethod
    def map_error(code: int | None, message: str = "agent request failed") -> AgentError:
        if code in (-402, -403, -405):
            return AgentError(code, int(ErrorCode.FORBIDDEN), message, False)
        if code in (-409, -504):
            return AgentError(code, int(ErrorCode.INVALID_PARAMETER), message, False)
        if code in (-304, -301, -300):
            return AgentError(code, int(ErrorCode.TOOL_FAILED), message, True)
        if code == -200:
            return AgentError(code, int(ErrorCode.AGENT_UNAVAILABLE), message, True)
        return AgentError(code, int(ErrorCode.AGENT_UNAVAILABLE), message, True)

    def _url(self, name: str) -> str:
        return f"{self._base_url}{self._paths[name]}"

    def _headers(self) -> dict[str, str]:
        return {**self._signer.headers(), "Accept": "application/json"}

    def _post(self, name: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(self._url(name), headers=self._headers(), json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise self.map_error(-500, f"Hengan request timed out: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise self.map_error(-500, f"Hengan request failed: {exc}") from exc
        return self._unwrap(body)

    def _unwrap(self, body: dict[str, Any]) -> dict[str, Any]:
        code = body.get("code", 0)
        if code != 0 or body.get("flag") == 2:
            raise self.map_error(code, body.get("msg") or "Hengan request failed")
        data = body.get("data")
        return {} if data is None else data

    def execute(self, sid: str, agent_id: str, inputs: dict[str, Any], stream: bool = False) -> Any:
        payload = {"sid": sid, "id": agent_id, "inputs": inputs, "stream": stream}
        if stream:
            return self.execute_stream(payload)
        return self._post("execute", payload)

    def execute_stream(self, payload: dict[str, Any]) -> Iterator[AgentEvent]:
        try:
            with self._client.stream("POST", self._url("execute"), headers=self._headers(), json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        event = parse_sse_line(line)
                        if event is not None:
                            yield event
        except AgentError:
            raise
        except httpx.TimeoutException as exc:
            raise self.map_error(-500, f"Hengan stream timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise self.map_error(-500, f"Hengan stream failed: {exc}") from exc

    def stop(self, sid: str) -> dict[str, Any]:
        return self._post("stop", {"sid": sid})

    def async_submit(self, agent_id: str, inputs: dict[str, Any], sid: str | None = None) -> str:
        data = self._post("submit", {"sid": sid, "id": agent_id, "inputs": inputs})
        task_id = data.get("taskId") or data.get("task_id") or data.get("id")
        if not task_id:
            raise self.map_error(-500, "Hengan submit response has no taskId")
        return str(task_id)

    def async_status(self, task_id: str) -> int:
        data = self._post("status", {"taskId": task_id})
        if isinstance(data, int):
            return data
        if isinstance(data, str) and data.isdigit():
            return int(data)
        if isinstance(data, dict):
            return int(data.get("status", data.get("value", 0)))
        raise self.map_error(-500, "Hengan status response is invalid")

    def async_messages(self, task_id: str, full: bool = True) -> dict[str, Any]:
        return self._post("messages", {"taskId": task_id, "full": full})

    def resume(self, message_id: str, inputs: dict[str, Any]) -> Any:
        return self._post("resume", {"messageId": message_id, "inputs": inputs})

    def upload(self, file: BinaryIO, filename: str) -> str:
        try:
            response = self._client.post(
                self._url("upload"), headers=self._signer.headers(),
                files={"file": (filename, file)}, timeout=self._timeout,
            )
            response.raise_for_status()
            data = self._unwrap(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise self.map_error(-500, f"Hengan upload failed: {exc}") from exc
        file_id = data.get("file") or data.get("fileId") or data.get("file_id")
        if not file_id:
            raise self.map_error(-500, "Hengan upload response has no file id")
        return str(file_id)

    def download(self, file_id: str) -> bytes:
        try:
            response = self._client.get(
                self._url("download"), headers=self._signer.headers(), params={"fileId": file_id},
            )
            response.raise_for_status()
            return response.content
        except httpx.TimeoutException as exc:
            raise self.map_error(-500, f"Hengan download timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise self.map_error(-500, f"Hengan download failed: {exc}") from exc
