"""ZAP 被动扫描适配器单测（T16）。

规格依据：
- SDD-04 T11 工具注册表契约
- SRS-02 F3.2 弱配置检测（缺失安全响应头、错误信息泄露、目录列举等）
- 工具清单 §2.1：OWASP ZAP **被动扫描**、被动代理模式、「仅被动观察请求/响应」
- SDD-01 §5.3/§5.5、SDD-02 §4.6 finding 字段

说明：本机未安装 ZAP（依赖 Java），`run()` 的编排用**注入式 HTTP 客户端**验证（依赖注入，非 mock 被测代码）。

运行：python tests/unit/test_zap_adapter.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.zap import ZapAdapter  # noqa: E402
from src.tools.contract import ToolRegistry  # noqa: E402

TARGET = "http://127.0.0.1:8080/"

# 真实形态的 ZAP /JSON/core/view/alerts 响应（弱配置 1 条 + 漏洞 1 条）
ALERTS_PAYLOAD = {
    "alerts": [
        {
            "pluginId": "10021",
            "alert": "X-Content-Type-Options Header Missing",
            "risk": "Low",
            "confidence": "Medium",
            "url": "http://127.0.0.1:8080/",
            "param": "",
            "evidence": "X-Content-Type-Options",
            "description": "The Anti-MIME-Sniffing header is not set.",
            "solution": "Ensure the application sets the X-Content-Type-Options header.",
            "cweid": "693",
        },
        {
            "pluginId": "40018",
            "alert": "SQL Injection",
            "risk": "High",
            "confidence": "High",
            "url": "http://127.0.0.1:8080/login.php",
            "param": "username",
            "evidence": "1' OR '1'='1",
            "description": "SQL injection may be possible.",
            "solution": "Use parameterized queries.",
            "cweid": "89",
        },
    ]
}


class _FakeZap:
    """记录调用并按端点返回的假 ZAP REST 客户端。"""

    def __init__(self, alerts=None, error=None):
        self.calls: list[tuple[str, dict]] = []
        self._alerts = ALERTS_PAYLOAD if alerts is None else alerts
        self._error = error

    def __call__(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, dict(params or {})))
        if self._error is not None:
            raise self._error
        if path.endswith("/alerts/"):
            return self._alerts
        return {"Result": "OK"}


class ZapManifestTest(unittest.TestCase):
    def test_manifest_registers_into_contract_registry(self):
        adapter = ZapAdapter()
        registry = ToolRegistry()
        registry.register(adapter)
        self.assertIs(registry.get("zap"), adapter)

    def test_manifest_is_read_only(self):
        self.assertTrue(ZapAdapter().manifest.read_only)

    def test_manifest_declares_vuln_and_weak_config(self):
        self.assertEqual(set(ZapAdapter().manifest.categories), {"VULN", "WEAK_CONFIG"})

    def test_manifest_uses_http_execution_mode(self):
        # ZAP 以守护进程 + REST API 方式调用
        self.assertEqual(ZapAdapter().manifest.execution_mode, "http")


class ZapNonDestructiveTest(unittest.TestCase):
    def setUp(self):
        self.adapter = ZapAdapter()

    def test_accepts_plain_http_target(self):
        self.assertTrue(self.adapter.non_destructive_check(TARGET, {}))

    def test_rejects_non_http_scheme(self):
        self.assertFalse(self.adapter.non_destructive_check("file:///etc/passwd", {}))

    def test_rejects_network_range_targets(self):
        self.assertFalse(self.adapter.non_destructive_check("http://192.168.0.0/24", {}))

    def test_rejects_active_scan_params(self):
        # 工具清单 §2.1 明确只允许被动模式，主动扫描/攻击类参数一律拒绝
        for key in ("active_scan", "ascan", "attack", "ajax_spider", "spider", "fuzz"):
            with self.subTest(param=key):
                self.assertFalse(self.adapter.non_destructive_check(TARGET, {key: True}))


class ZapRunTest(unittest.TestCase):
    def test_asks_zap_to_access_the_target_through_its_proxy(self):
        fake = _FakeZap()
        ZapAdapter(http=fake).run(TARGET, {})
        path, params = fake.calls[0]
        self.assertTrue(path.endswith("/accessUrl/"))
        self.assertEqual(params["url"], TARGET)

    def test_fetches_alerts_for_the_target(self):
        fake = _FakeZap()
        ZapAdapter(http=fake).run(TARGET, {})
        path, params = fake.calls[1]
        self.assertTrue(path.endswith("/alerts/"))
        self.assertEqual(params["baseurl"], TARGET)

    def test_includes_api_key_when_configured(self):
        fake = _FakeZap()
        ZapAdapter(http=fake, api_key="secret-key").run(TARGET, {})
        self.assertTrue(all(params.get("apikey") == "secret-key" for _, params in fake.calls))

    def test_returns_success_with_findings(self):
        result = ZapAdapter(http=_FakeZap()).run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tool_id, "zap")
        self.assertEqual(len(result.findings), 2)

    def test_maps_zap_risk_to_project_severity(self):
        findings = ZapAdapter(http=_FakeZap()).run(TARGET, {}).findings
        by_title = {f["title"]: f["severity"] for f in findings}
        self.assertEqual(by_title["SQL Injection"], "HIGH")
        self.assertEqual(by_title["X-Content-Type-Options Header Missing"], "LOW")

    def test_categorizes_missing_header_alerts_as_weak_config(self):
        findings = ZapAdapter(http=_FakeZap()).run(TARGET, {}).findings
        by_title = {f["title"]: f["type"] for f in findings}
        self.assertEqual(by_title["X-Content-Type-Options Header Missing"], "WEAK_CONFIG")

    def test_categorizes_injection_alerts_as_vuln(self):
        findings = ZapAdapter(http=_FakeZap()).run(TARGET, {}).findings
        by_title = {f["title"]: f["type"] for f in findings}
        self.assertEqual(by_title["SQL Injection"], "VULN")

    def test_evidence_is_desensitized(self):
        # 证据不得包含 ZAP 的原始 evidence（可能含响应片段/注入载荷）
        findings = ZapAdapter(http=_FakeZap()).run(TARGET, {}).findings
        for finding in findings:
            self.assertNotIn("1' OR '1'='1", finding["evidence"])
            self.assertIn("40018" if "SQL" in finding["title"] else "10021", finding["evidence"])

    def test_returns_rejected_when_check_fails(self):
        result = ZapAdapter(http=_FakeZap()).run(TARGET, {"active_scan": True})
        self.assertEqual(result.status, "REJECTED")

    def test_returns_failed_when_zap_unreachable(self):
        result = ZapAdapter(http=_FakeZap(error=ConnectionError("refused"))).run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertIn("ZAP", result.error)

    def test_returns_timeout_status(self):
        result = ZapAdapter(http=_FakeZap(error=TimeoutError("slow"))).run(TARGET, {})
        self.assertEqual(result.status, "TIMEOUT")

    def test_tolerates_empty_alert_set(self):
        result = ZapAdapter(http=_FakeZap(alerts={"alerts": []})).run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.findings, [])

    def test_skips_malformed_alert_entries(self):
        payload = {"alerts": [ALERTS_PAYLOAD["alerts"][0], "not-a-dict", {}]}
        result = ZapAdapter(http=_FakeZap(alerts=payload)).run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(result.findings), 1)


class ZapRegistryTest(unittest.TestCase):
    def test_registry_execute_path_returns_result_and_audits(self):
        registry = ToolRegistry()
        registry.register(ZapAdapter(http=_FakeZap()))
        result = registry.execute("zap", TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(registry.audit()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
