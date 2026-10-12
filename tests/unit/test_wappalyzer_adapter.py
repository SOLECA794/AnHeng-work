"""Wappalyzer 指纹适配器单测（T17）。

规格依据：
- SDD-04 T11 工具注册表契约（categories 含 `FINGERPRINT`）
- SRS-02 F3.1 常见漏洞检测：指纹识别（Wappalyzer）→ 组件/版本比对漏洞库
- SDD-01 §5.1 检测流水线「指纹识别」阶段、§5.4
- 工具清单 §2.1：Wappalyzer **只读分析**、「仅解析响应，无写操作」

⚠️ 待决事项（详见 docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md）：
  1. `FINGERPRINT` 不在 SDD-02 的 `finding_type` 枚举内——指纹不是风险项，本适配器
     以 `severity=INFO` 输出，是否入库为 Finding 需口径确认；
  2. npm 上的 `wappalyzer` CLI 已废弃，命令行调用方式需在装好后实测确认。

运行：python tests/unit/test_wappalyzer_adapter.py -v
"""

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.wappalyzer import WappalyzerAdapter  # noqa: E402
from src.tools.contract import ToolRegistry  # noqa: E402

TARGET = "http://127.0.0.1:8080/"

# 真实形态的 Wappalyzer CLI JSON 输出
WAPPALYZER_JSON = json.dumps(
    {
        "urls": {
            TARGET: {
                "applications": [
                    {
                        "name": "Nginx",
                        "version": "1.18.0",
                        "confidence": 100,
                        "categories": [{"name": "Web servers"}],
                    },
                    {
                        "name": "PHP",
                        "version": "",
                        "confidence": 90,
                        "categories": [{"name": "Programming languages"}],
                    },
                ]
            }
        }
    }
)


def _executor_returning(raw):
    def _run(command, timeout_seconds):
        return raw

    return _run


def _executor_raising(exc):
    def _run(command, timeout_seconds):
        raise exc

    return _run


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


class WappalyzerCommandTest(unittest.TestCase):
    def test_command_targets_the_url(self):
        command = WappalyzerAdapter().build_command(TARGET, {})
        self.assertEqual(command[0], "wappalyzer")
        self.assertIn(TARGET, command)


class WappalyzerParseTest(unittest.TestCase):
    def setUp(self):
        self.findings = WappalyzerAdapter().parse_output(WAPPALYZER_JSON)

    def test_maps_applications_to_fingerprint_findings(self):
        self.assertEqual(len(self.findings), 2)
        finding = self.findings[0]
        self.assertEqual(finding["type"], "FINGERPRINT")
        self.assertEqual(finding["severity"], "INFO")  # 指纹不是风险项
        self.assertEqual(finding["title"], "Nginx 1.18.0")
        self.assertEqual(finding["url"], TARGET)
        self.assertEqual(finding["tool_name"], "wappalyzer")

    def test_handles_application_without_version(self):
        self.assertEqual(self.findings[1]["title"], "PHP")
        self.assertIn("未知", self.findings[1]["evidence"])

    def test_records_category_and_confidence_in_evidence(self):
        evidence = self.findings[0]["evidence"]
        self.assertIn("Web servers", evidence)
        self.assertIn("100", evidence)

    def test_returns_empty_for_empty_output(self):
        self.assertEqual(WappalyzerAdapter().parse_output(""), [])

    def test_returns_empty_for_malformed_json(self):
        self.assertEqual(WappalyzerAdapter().parse_output("{not json}"), [])

    def test_skips_url_entries_without_applications(self):
        payload = json.dumps({"urls": {TARGET: {}, "http://x.test/": "junk"}})
        self.assertEqual(WappalyzerAdapter().parse_output(payload), [])


class WappalyzerRunTest(unittest.TestCase):
    def test_returns_success_with_fingerprints(self):
        result = WappalyzerAdapter(executor=_executor_returning(WAPPALYZER_JSON)).run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tool_id, "wappalyzer")
        self.assertEqual(len(result.findings), 2)

    def test_reports_failed_when_binary_missing(self):
        adapter = WappalyzerAdapter(executor=_executor_raising(FileNotFoundError("wappalyzer")))
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertIn("wappalyzer", result.error)

    def test_reports_timeout_status(self):
        adapter = WappalyzerAdapter(
            executor=_executor_raising(subprocess.TimeoutExpired(cmd="wappalyzer", timeout=30))
        )
        self.assertEqual(adapter.run(TARGET, {}).status, "TIMEOUT")

    def test_returns_rejected_on_unsafe_params(self):
        adapter = WappalyzerAdapter(executor=_executor_returning(WAPPALYZER_JSON))
        self.assertEqual(adapter.run(TARGET, {"post_data": "x"}).status, "REJECTED")

    def test_registry_execute_path_returns_result(self):
        registry = ToolRegistry()
        registry.register(WappalyzerAdapter(executor=_executor_returning(WAPPALYZER_JSON)))
        result = registry.execute("wappalyzer", TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(registry.audit()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
