"""恒脑 v2 adapter, aligned with A's recorded wire responses."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
from threading import BoundedSemaphore, Lock
import time
from typing import Any, BinaryIO

import httpx

from src.agent.errors import map_error
from src.agent.provider import AgentError, AgentProvider, parse_sse_line


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
        return {"appKey": self.app_key, "sign": timestamp + base64.b64encode(digest).decode()}


class HenganAgentProvider(AgentProvider):
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
    map_error = staticmethod(map_error)

    def __init__(self, base_url: str, app_key: str, app_secret: str, *,
                 timeout_seconds: float = 90, client: httpx.Client | None = None,
                 max_concurrency: int = 2, retry_attempts: int = 3,
                 sleep=time.sleep, audit=None) -> None:
        if not 1 <= max_concurrency <= 2 or not 0 <= retry_attempts <= 5:
            raise ValueError("invalid concurrency/retry limit")
        self._base_url = base_url.rstrip("/")
        self._signer = HenganSigner(app_key, app_secret)
        self._timeout = timeout_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None
        self._slots = BoundedSemaphore(max_concurrency)
        self._controls = BoundedSemaphore(max_concurrency)
        self._session_guard = Lock()
        self._active_sessions: set[str] = set()
        self._retry_attempts = retry_attempts
        self._sleep = sleep
        self._audit = audit

    def close(self):
        if self._owns_client:
            self._client.close()

    def _url(self, name):
        return self._base_url + self._paths[name]

    def _headers(self, *, stream=False):
        return {**self._signer.headers(), "Accept": "text/event-stream" if stream else "application/json"}

    @contextmanager
    def _operation(self, action, sid=None):
        started = time.monotonic()
        status, code = "FAILED", None
        claimed = acquired = False
        slots = self._controls if action in {"resume", "stop"} else self._slots
        try:
            if sid:
                with self._session_guard:
                    if sid in self._active_sessions:
                        raise map_error(-301)
                    self._active_sessions.add(sid)
                    claimed = True
            # Control requests must work while execute streams await answers.
            acquired = slots.acquire(timeout=self._timeout)
            if not acquired:
                raise map_error(-301)
            yield
            status = "SUCCESS"
        except GeneratorExit:
            status = "CANCELLED"
            raise
        except AgentError as exc:
            code = exc.provider_code
            raise
        finally:
            if acquired:
                slots.release()
            if claimed:
                with self._session_guard:
                    self._active_sessions.discard(sid)
            if self._audit:
                self._audit({"action": action, "status": status, "provider_code": code,
                             "latency_ms": int((time.monotonic() - started) * 1000)})

    def _unwrap(self, body):
        if not isinstance(body, dict) or type(body.get("code")) not in {str, int}:
            raise map_error(None)
        try:
            code = int(body["code"])
        except ValueError as exc:
            raise map_error(None) from exc
        if code != 0 or body.get("flag") == 2:
            error = map_error(code)
            if body.get("flag") == 2:
                error.retryable = False
            raise error
        return body.get("data")

    @staticmethod
    def _check_http(response):
        if response.status_code in {401, 403}:
            raise map_error(-403)
        if response.status_code == 429:
            raise map_error(-301)
        response.raise_for_status()

    def _post(self, name, payload):
        for attempt in range(self._retry_attempts + 1):
            try:
                response = self._client.post(self._url(name), headers=self._headers(),
                                             json=payload, timeout=self._timeout)
                self._check_http(response)
                return self._unwrap(response.json())
            except AgentError as exc:
                if not exc.retryable or exc.provider_code not in {-300, -301} or attempt == self._retry_attempts:
                    raise
                self._sleep(min(2 ** attempt, 8))
            except (httpx.HTTPError, ValueError) as exc:
                raise map_error(None) from exc

    def execute(self, sid: str, agent_id: str, inputs: dict[str, Any], stream: bool = False):
        payload = {"sid": sid, "id": agent_id, "inputs": inputs, "stream": stream}
        if stream:
            return self.execute_stream(payload)
        with self._operation("execute", sid):
            data = self._post("execute", payload)
            if not isinstance(data, dict):
                raise map_error(None)
            return data

    def execute_stream(self, payload):
        with self._operation("execute", payload.get("sid")):
            emitted = False
            for attempt in range(self._retry_attempts + 1):
                try:
                    with self._client.stream("POST", self._url("execute"),
                                             headers=self._headers(stream=True), json=payload,
                                             timeout=self._timeout) as response:
                        self._check_http(response)
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            response.read()
                            self._unwrap(response.json())
                            raise map_error(None)
                        data_lines = []
                        data_size = 0
                        for line in response.iter_lines():
                            if line.startswith("data:"):
                                data_size += len(line)
                                if data_size > 1_000_000:
                                    raise map_error(None)
                                data_lines.append(line[5:].lstrip(" "))
                                # A's captures contain complete JSON per data line.
                                # Also accept standard multiline JSON SSE frames.
                                try:
                                    json.loads("\n".join(data_lines))
                                except ValueError:
                                    continue
                                event = parse_sse_line("data:" + "\n".join(data_lines))
                                data_lines.clear()
                                data_size = 0
                                if event:
                                    emitted = True
                                    yield event
                            elif not line and data_lines:
                                event = parse_sse_line("data:" + "\n".join(data_lines))
                                data_lines.clear()
                                data_size = 0
                                if event:
                                    emitted = True
                                    yield event
                        if data_lines:
                            event = parse_sse_line("data:" + "\n".join(data_lines))
                            if event:
                                emitted = True
                                yield event
                        if not emitted:
                            raise map_error(None)
                        return
                except AgentError as exc:
                    if emitted or not exc.retryable or exc.provider_code not in {-300, -301} or attempt == self._retry_attempts:
                        raise
                    self._sleep(min(2 ** attempt, 8))
                except (httpx.HTTPError, ValueError) as exc:
                    raise map_error(None) from exc

    def stop(self, sid: str):
        with self._operation("stop"):
            return self._post("stop", {"sid": sid})

    def async_submit(self, agent_id: str, inputs: dict[str, Any], sid: str | None = None) -> str:
        with self._operation("submit", sid):
            data = self._post("submit", {"sid": sid, "id": agent_id, "inputs": inputs})
            if isinstance(data, dict):
                data = data.get("taskId") or data.get("task_id") or data.get("id")
            if not isinstance(data, str) or not data:
                raise map_error(None)
            return data

    def async_status(self, task_id: str) -> int:
        with self._operation("status"):
            data = self._post("status", {"taskId": task_id})
            if isinstance(data, dict):
                data = data.get("status")
            if type(data) not in {str, int} or str(data) not in {"1", "2", "3", "4"}:
                raise map_error(None)
            return int(data)

    def async_messages(self, task_id: str, full: bool = True):
        with self._operation("messages"):
            data = self._post("messages", {"taskId": task_id, "full": full})
            if not isinstance(data, dict):
                raise map_error(None)
            return data

    def resume(self, message_id: str, inputs: dict[str, Any]):
        with self._operation("resume"):
            return self._post("resume", {"messageId": message_id, "inputs": inputs})

    def wait_for_task(self, task_id: str, *, timeout_seconds=300, poll_interval=5):
        """Bounded polling; never automatically submits a replacement task."""
        if timeout_seconds <= 0 or poll_interval <= 0:
            raise ValueError("poll timeout and interval must be positive")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.async_status(task_id)
            if status == 2:
                data = self.async_messages(task_id, full=True)
                if data.get("errors") or data.get("status") in {3, 4, "3", "4"}:
                    raise map_error(None)
                return data
            if status == 3:
                raise AgentError(None, 40902, "恒脑异步任务已取消")
            if status == 4:
                raise map_error(None)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self._sleep(min(poll_interval, remaining))
        raise AgentError(None, 50001, "恒脑异步任务等待超时，请查询原任务状态", True)

    def upload(self, file: BinaryIO, filename: str) -> str:
        with self._operation("upload"):
            try:
                response = self._client.post(self._url("upload"), headers=self._signer.headers(),
                                             files={"file": (filename, file)}, timeout=self._timeout)
                self._check_http(response)
                data = self._unwrap(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                raise map_error(None) from exc
            if isinstance(data, dict):
                data = data.get("file") or data.get("fileId") or data.get("file_id")
            if not isinstance(data, str) or not data:
                raise map_error(None)
            return data

    def download(self, file_id: str) -> bytes:
        with self._operation("download"):
            try:
                response = self._client.post(self._url("download"), headers=self._headers(),
                                             json={"fileId": file_id}, timeout=self._timeout)
                self._check_http(response)
                if "application/json" in response.headers.get("content-type", ""):
                    self._unwrap(response.json())
                    raise map_error(None)
                return response.content
            except (httpx.HTTPError, ValueError) as exc:
                raise map_error(None) from exc
