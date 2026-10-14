"""Wappalyzer 指纹适配器单测（T17）。

规格依据：
- SDD-04 T11 工具注册表契约（categories 含 `FINGERPRINT`）
- SRS-02 F3.1 常见漏洞检测：指纹识别（Wappalyzer）→ 组件/版本比对漏洞库
- SDD-01 §5.1 检测流水线「指纹识别」阶段、§5.4
- 工具清单 §2.1：Wappalyzer **只读分析**、「仅解析响应，无写操作」

实现变更（2026-10-14）：由「调用 wappalyzer CLI」改为**进程内调用 python-Wappalyzer**
（Wappalyzer 规则的 Python 实现）。原因：npm 上的 wappalyzer CLI 已废弃，且进程内调用
可测、无外部二进制依赖。该库自带抓取（`WebPage.new_from_url`），只读 GET。

⚠️ 待决：`FINGERPRINT` 不在 SDD-02 的 `finding_type` 枚举内。指纹本身不是风险项，
本适配器统一以 `severity=INFO` 输出，是否落库为 Finding 需口径确认。

运行：python tests/unit/test_wappalyzer_adapter.py -v
"""

import http.server
import pathlib
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.wappalyzer import WappalyzerAdapter  # noqa: E402
from src.tools.contract import ToolRegistry  # noqa: E402

TARGET = "http://127.0.0.1:8080/"

# 真实形态的 python-Wappalyzer 输出（对 DVWA 实测所得）
DETECTED = {
    "Debian": {"versions": [], "categories": ["Operating systems"]},
    "Apache": {"versions": ["2.4.25"], "categories": ["Web servers"]},
}

FAKE_HTML = b"<html><head><title>fixture</title></head><body>ok</body></html>"


def _analyzer_returning(detected):
    def _analyze(url):
        return detected

    return _analyze


def _analyzer_raising(exc):
    def _analyze(url):
        raise exc

    return _analyze


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    """本地夹具：带 nginx 标识的响应，用于验证真实库调用链。"""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Server", "nginx/1.24.0")
        self.send_header("Content-Length", str(len(FAKE_HTML)))
        self.end_headers()
        self.wfile.write(FAKE_HTML)

    def log_message(self, *args):
        pass


class WappalyzerManifestTest(unittest.TestCase):
    def test_manifest_registers_into_contract_registry(self):
        adapter = WappalyzerAdapter()
        registry = ToolRegistry()
        registry.register(adapter)
        self.assertIs(registry.get("wappalyzer"), adapter)

    def test_manifest_declares_fingerprint_category(self):
        self.assertEqual(WappalyzerAdapter().manifest.categories, ["FINGERPRINT"])

    def test_manifest_is_read_only_local(self):
        manifest = WappalyzerAdapter().manifest
        self.assertTrue(manifest.read_only)
        self.assertEqual(manifest.execution_mode, "local")


class WappalyzerNonDestructiveTest(unittest.TestCase):
    def setUp(self):
        self.adapter = WappalyzerAdapter()

    def test_accepts_plain_http_target(self):
        self.assertTrue(self.adapter.non_destructive_check(TARGET, {}))

    def test_rejects_non_http_scheme(self):
        self.assertFalse(self.adapter.non_destructive_check("file:///etc/passwd", {}))

    def test_rejects_network_range_targets(self):
        self.assertFalse(self.adapter.non_destructive_check("http://192.168.0.0/24", {}))

    def test_rejects_data_sending_params(self):
        # 只读分析：不接受任何主动发送数据的参数
        for key in ("post_data", "payload", "fuzz", "brute", "upload"):
            with self.subTest(param=key):
                self.assertFalse(self.adapter.non_destructive_check(TARGET, {key: "x"}))


class WappalyzerParseTest(unittest.TestCase):
    def setUp(self):
        self.findings = WappalyzerAdapter().parse_detected(DETECTED, TARGET)

    def test_maps_each_technology_to_a_fingerprint_finding(self):
        self.assertEqual(len(self.findings), 2)
        by_title = {f["title"]: f for f in self.findings}
        self.assertIn("Apache 2.4.25", by_title)
        self.assertIn("Debian", by_title)

    def test_uses_fingerprint_type_and_info_severity(self):
        # 指纹不是风险项：统一 INFO，避免进入告警链路
        for finding in self.findings:
            self.assertEqual(finding["type"], "FINGERPRINT")
            self.assertEqual(finding["severity"], "INFO")
            self.assertEqual(finding["tool_name"], "wappalyzer")
            self.assertEqual(finding["url"], TARGET)

    def test_omits_version_when_unknown(self):
        debian = next(f for f in self.findings if f["title"] == "Debian")
        self.assertIn("未知", debian["evidence"])
        self.assertIn("Operating systems", debian["evidence"])

    def test_records_version_and_category_in_evidence(self):
        apache = next(f for f in self.findings if f["title"].startswith("Apache"))
        self.assertIn("2.4.25", apache["evidence"])
        self.assertIn("Web servers", apache["evidence"])

    def test_returns_empty_for_no_detection(self):
        self.assertEqual(WappalyzerAdapter().parse_detected({}, TARGET), [])

    def test_tolerates_missing_inner_fields(self):
        findings = WappalyzerAdapter().parse_detected({"Nginx": {}}, TARGET)
        self.assertEqual(findings[0]["title"], "Nginx")


class WappalyzerRunTest(unittest.TestCase):
    def test_returns_success_with_fingerprints(self):
        result = WappalyzerAdapter(analyzer=_analyzer_returning(DETECTED)).run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tool_id, "wappalyzer")
        self.assertEqual(len(result.findings), 2)

    def test_returns_rejected_on_unsafe_params(self):
        adapter = WappalyzerAdapter(analyzer=_analyzer_returning(DETECTED))
        self.assertEqual(adapter.run(TARGET, {"post_data": "x"}).status, "REJECTED")

    def test_reports_timeout_status(self):
        adapter = WappalyzerAdapter(analyzer=_analyzer_raising(TimeoutError("slow")))
        self.assertEqual(adapter.run(TARGET, {}).status, "TIMEOUT")

    def test_reports_failed_on_connection_error(self):
        adapter = WappalyzerAdapter(analyzer=_analyzer_raising(ConnectionError("refused")))
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertTrue(result.error)

    def test_reports_missing_library_clearly(self):
        # 库未安装时应给出可操作的提示，而不是堆栈
        adapter = WappalyzerAdapter(
            analyzer=_analyzer_raising(ImportError("No module named 'Wappalyzer'"))
        )
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertIn("python-Wappalyzer", result.error)

    def test_registry_execute_path_returns_result(self):
        registry = ToolRegistry()
        registry.register(WappalyzerAdapter(analyzer=_analyzer_returning(DETECTED)))
        result = registry.execute("wappalyzer", TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(registry.audit()), 1)


class WappalyzerRealLibraryTest(unittest.TestCase):
    """真实库调用链验证：不注入 analyzer，走 python-Wappalyzer 抓取 + 分析。

    夹具是进程内真实 HTTP 服务（非 mock），因此不需要 Docker 或外部靶场。
    """

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.url = f"http://{host}:{port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_detects_technology_from_a_real_response(self):
        result = WappalyzerAdapter().run(self.url, {})
        self.assertEqual(result.status, "SUCCESS", msg=result.error)
        titles = [f["title"] for f in result.findings]
        self.assertTrue(
            any("nginx" in t.lower() for t in titles),
            f"响应带 Server: nginx/1.24.0，应检出 nginx；实际检出：{titles}",
        )

    def test_real_detection_carries_version_when_available(self):
        result = WappalyzerAdapter().run(self.url, {})
        nginx = next(f for f in result.findings if "nginx" in f["title"].lower())
        self.assertIn("1.24.0", nginx["evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
