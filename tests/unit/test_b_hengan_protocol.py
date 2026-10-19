"""T10 wire-level checks via httpx MockTransport; no platform/network calls."""
import base64
import hashlib
import hmac
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from src.agent.hengan import HenganAgentProvider, HenganSigner
from src.agent.provider import AgentError, aggregate_events, parse_sse_line

FIXTURES = Path(__file__).resolve().parents[2] / "deploy" / "恒脑平台文档"


class HenganProtocolTests(unittest.TestCase):
    def provider(self, handler, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return HenganAgentProvider("https://fixture.invalid", "fixture-key", "fixture-secret",
                                   client=client, **kwargs)

    def test_signature_matches_independent_hmac_vector(self):
        with patch("src.agent.hengan.time.time", return_value=1792368000):
            headers = HenganSigner("fixture-key", "fixture-secret").headers()
        ts = "1792368000000"
        digest = hmac.new(b"fixture-secret", (ts + "\nfixture-secret\nfixture-key").encode(), hashlib.sha256).digest()
        self.assertEqual(headers, {"appKey": "fixture-key", "sign": ts + base64.b64encode(digest).decode()})

    def test_recorded_submit_upload_and_messages(self):
        def handler(request):
            names = {"submit": "09-task-submit.json", "upload": "09-file-upload.json",
                     "messages": "09-task-messages.json"}
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=(FIXTURES / names[name]).read_bytes(),
                                  headers={"content-type": "application/json"})
        provider = self.provider(handler)
        self.assertEqual(provider.async_submit("agent", {}), "2109235891519254529")
        self.assertTrue(provider.upload(io.BytesIO(b"synthetic"), "fixture.txt").startswith("file:"))
        self.assertIsInstance(provider.async_messages("task")["messages"], list)

    def test_recorded_nonstream_execute(self):
        provider = self.provider(lambda _: httpx.Response(200, content=(FIXTURES / "09-execute-非流式-恶意IoC.json").read_bytes()))
        data = provider.execute("sid", "agent", {"input": "fixture"})
        self.assertIn("messages", data["session"])
        self.assertIn("token_usage", data)

    def test_recorded_stream_aggregation(self):
        raw = (FIXTURES / "09-execute-流式SSE.txt").read_text(encoding="utf-8")
        provider = self.provider(lambda _: httpx.Response(200, text=raw, headers={"content-type": "text/event-stream"}))
        events = list(provider.execute("sid", "agent", {}, stream=True))
        expected = [parse_sse_line(line) for line in raw.splitlines() if line.startswith("data:")]
        self.assertEqual(len(events), len(expected))
        self.assertEqual(aggregate_events(events), aggregate_events(expected))
        self.assertGreater(len(events), 700)

    def test_question_options_and_resume_completion_time(self):
        question = {"mode": "question", "message_id": "question-id", "name": "confirm",
                    "options": {"A": "确认授权"}, "qid": "qid", "message": "授权确认"}
        event = parse_sse_line("data:" + json.dumps(question))
        self.assertEqual(event.content, "授权确认")
        self.assertEqual(event.raw["options"]["A"], "确认授权")
        def handler(request):
            self.assertEqual(json.loads(request.content), {"messageId": "question-id", "inputs": {"confirm": {"A": "确认授权"}}})
            return httpx.Response(200, json={"code": 0, "data": "2026-10-19 12:00:00"})
        self.assertEqual(self.provider(handler).resume("question-id", {"confirm": {"A": "确认授权"}}), "2026-10-19 12:00:00")

    def test_multiline_sse_with_comments_and_eof(self):
        raw = ': heartbeat\nevent: message\ndata: {"code": 0,\ndata: "data": {"content":"片段", "message_id":"m"}}'
        provider = self.provider(lambda _: httpx.Response(200, text=raw, headers={"content-type": "text/event-stream"}))
        self.assertEqual(list(provider.execute("s", "a", {}, True))[0].content, "片段")

    def test_download_is_post_json_and_preserves_binary(self):
        def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(json.loads(request.content), {"fileId": "file:fixture"})
            return httpx.Response(200, content=b"\x00\xffbytes", headers={"content-type": "application/octet-stream"})
        self.assertEqual(self.provider(handler).download("file:fixture"), b"\x00\xffbytes")

    def test_error_mapping_and_daily_quota_is_not_retryable(self):
        for upstream, project in ((-402, 40300), (-403, 40300), (-405, 40300),
                                  (-409, 40001), (-504, 40001), (-304, 50002),
                                  (-300, 50002), (-301, 50002), (-200, 50001), (-500, 50001)):
            with self.subTest(upstream=upstream):
                self.assertEqual(HenganAgentProvider.map_error(upstream).project_code, project)
        self.assertFalse(HenganAgentProvider.map_error(-304).retryable)

    def test_concurrency_retry_resigns_and_backs_off(self):
        calls, delays = [], []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"code": -301} if len(calls) < 3 else {"code": "0", "data": "task-id"})
        provider = self.provider(handler, sleep=delays.append)
        self.assertEqual(provider.async_submit("agent", {}), "task-id")
        self.assertEqual(delays, [1, 2])
        self.assertTrue(all("sign" in request.headers for request in calls))

    def test_quota_terminal_and_timeout_are_not_replayed(self):
        for body in ({"code": -304}, {"code": -301, "flag": 2}):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=body)
            with self.assertRaises(AgentError):
                self.provider(handler, sleep=lambda _: self.fail("must not sleep")).async_submit("a", {})
            self.assertEqual(len(calls), 1)
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("fixture-secret must not escape", request=request)
        with self.assertRaises(AgentError) as caught:
            self.provider(timeout).execute("s", "a", {})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_json_error_in_stream_and_no_retry_after_output(self):
        for text, content_type in ((json.dumps({"code": -504}), "application/json"),
                                   ('data:{"code":0,"data":{"content":"a"}}\n\ndata:{"code":-301}\n\n', "text/event-stream")):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, text=text, headers={"content-type": content_type})
            with self.assertRaises(AgentError):
                list(self.provider(handler, sleep=lambda _: self.fail("must not retry")).execute("s", "a", {}, True))
            self.assertEqual(len(calls), 1)

    def test_protocol_malformed_payloads_become_agent_errors(self):
        for body in ([], {}, {"code": "wrong"}, {"code": 0, "data": []}, {"code": 0, "data": None}):
            with self.subTest(body=body), self.assertRaises(AgentError):
                self.provider(lambda _: httpx.Response(200, json=body)).execute("s", "a", {})
        for data in (None, {}, True, "bad", 5):
            with self.subTest(data=data), self.assertRaises(AgentError):
                self.provider(lambda _: httpx.Response(200, json={"code": 0, "data": data})).async_status("t")

    def test_stream_close_releases_slot_and_same_sid(self):
        logs = []
        raw = 'data:{"code":0,"data":{"content":"first"}}\n\ndata:{"code":0,"data":{"content":"second"}}\n\n'
        provider = self.provider(lambda _: httpx.Response(200, text=raw, headers={"content-type": "text/event-stream"}),
                                 max_concurrency=1, audit=logs.append)
        stream = provider.execute("sid", "agent", {}, True)
        self.assertEqual(next(stream).content, "first")
        with self.assertRaises(AgentError):
            next(provider.execute("sid", "agent", {}, True))
        stream.close()
        self.assertEqual(len(list(provider.execute("sid", "agent", {}, True))), 2)
        self.assertIn("CANCELLED", [row["status"] for row in logs])
        self.assertEqual(logs[-1]["status"], "SUCCESS")

    def test_resume_available_while_only_execution_slot_is_occupied(self):
        def handler(request):
            if request.url.path.endswith("resume"):
                return httpx.Response(200, json={"code": 0, "data": "completed"})
            return httpx.Response(200, text='data:{"code":0,"data":{"mode":"question","message_id":"m"}}\n\n',
                                  headers={"content-type": "text/event-stream"})
        provider = self.provider(handler, max_concurrency=1)
        stream = provider.execute("s", "a", {}, True)
        self.assertEqual(next(stream).mode, "question")
        self.assertEqual(provider.resume("m", {"confirm": "yes"}), "completed")
        stream.close()

    def test_bounded_polling_completion_failure_and_cancellation(self):
        provider = self.provider(lambda _: self.fail("unexpected network"), sleep=lambda _: None)
        states = iter([1, 2])
        provider.async_status = lambda _: next(states)
        provider.async_messages = lambda *args, **kwargs: {"messages": [], "status": 2}
        self.assertEqual(provider.wait_for_task("t")["status"], 2)
        for state, code in ((3, 40902), (4, 50001)):
            provider.async_status = lambda _: state
            with self.assertRaises(AgentError) as caught:
                provider.wait_for_task("t")
            self.assertEqual(caught.exception.project_code, code)
        provider.async_status = lambda _: 2
        provider.async_messages = lambda *args, **kwargs: {"errors": ["private upstream content"]}
        with self.assertRaises(AgentError) as caught:
            provider.wait_for_task("t")
        self.assertNotIn("private", str(caught.exception))

    def test_poll_timeout_does_not_resubmit(self):
        provider = self.provider(lambda _: self.fail("unexpected network"), sleep=lambda _: None)
        provider.async_status = lambda _: 1
        with patch("src.agent.hengan.time.monotonic", side_effect=[0, 0, 1, 3]):
            with self.assertRaises(AgentError) as caught:
                provider.wait_for_task("t", timeout_seconds=2)
        self.assertEqual(caught.exception.project_code, 50001)

    def test_stop_and_all_async_statuses(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": "stopped"})
        provider = self.provider(handler)
        self.assertEqual(provider.stop("sid"), "stopped")
        self.assertEqual(calls, [{"sid": "sid"}])
        for status in (1, 2, 3, 4, "1", "2", "3", "4"):
            provider._post = lambda *args: status
            self.assertEqual(provider.async_status("t"), int(status))

    def test_execution_concurrency_bound_and_release_on_error(self):
        raw = 'data:{"code":0,"data":{"content":"a"}}\n\n'
        provider = self.provider(lambda _: httpx.Response(200, text=raw, headers={"content-type": "text/event-stream"}),
                                 max_concurrency=1, timeout_seconds=0.01)
        stream = provider.execute("one", "a", {}, True)
        next(stream)
        with self.assertRaises(AgentError):
            next(provider.execute("two", "a", {}, True))
        stream.close()
        self.assertEqual(len(list(provider.execute("two", "a", {}, True))), 1)


if __name__ == "__main__":
    unittest.main()
