"""Nuclei 适配器单测（T15）。

规格依据：
- SDD-04 T11 工具注册表契约（ToolManifest / ToolAdapter / ToolResult）
- SRS-02 F3.1 常见漏洞检测（组件/版本比对 → 非破坏验证）
- SDD-01 §5.3 工具层设计、§5.5 结果归一化；工具清单 §2.1 非破坏声明
- SDD-02 §4.6 finding 字段

说明：本机未安装 nuclei，因此 `run()` 的编排用**注入式执行器**验证（依赖注入，非 mock 被测代码），
命令构造与输出解析为纯函数，用真实形态的 nuclei JSONL 样例验证。

运行：python tests/unit/test_nuclei_adapter.py -v
"""

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.nuclei import NucleiAdapter  # noqa: E402
from src.tools.contract import ToolRegistry  # noqa: E402

TARGET = "http://127.0.0.1:8080/"

# 真实形态的 nuclei -jsonl 输出（2 条：一条 CVE、一条弱配置）
NUCLEI_JSONL = "\n".join(
    [
        json.dumps(
            {
                "template-id": "CVE-2021-41773",
                "info": {
                    "name": "Apache Path Traversal",
                    "severity": "critical",
                    "tags": ["cve", "apache"],
                    "remediation": "升级 Apache 至 2.4.51 及以上",
                },
                "matched-at": "http://127.0.0.1:8080/cgi-bin/.%2e/",
                "type": "http",
            }
        ),
        json.dumps(
            {
                "template-id": "http-missing-security-headers",
                "info": {
                    "name": "Missing Security Headers",
                    "severity": "info",
                    "tags": ["misconfig", "headers"],
                },
                "matched-at": "http://127.0.0.1:8080/",
            }
        ),
    ]
)


def _executor_returning(raw):
    def _run(command, timeout_seconds):
        return raw

    return _run


def _executor_raising(exc):
    def _run(command, timeout_seconds):
        raise exc

    return _run


class NucleiManifestTest(unittest.TestCase):
    def test_manifest_registers_into_contract_registry(self):
        adapter = NucleiAdapter()
        registry = ToolRegistry()
        registry.register(adapter)
        self.assertIs(registry.get("nuclei"), adapter)

    def test_manifest_is_read_only(self):
        self.assertTrue(NucleiAdapter().manifest.read_only)

    def test_manifest_declares_vuln_and_weak_config(self):
        # category 口径按 SDD-02（待 SDD-04 §1 词表确认，见 docs/01-项目治理/04-…核对反馈.md）
        self.assertEqual(set(NucleiAdapter().manifest.categories), {"VULN", "WEAK_CONFIG"})

    def test_manifest_timeout_within_contract_limit(self):
        timeout = NucleiAdapter().manifest.timeout_seconds
        self.assertGreaterEqual(timeout, 1)
        self.assertLessEqual(timeout, 300)

    def test_manifest_rate_limit_is_positive(self):
        rate_limit = NucleiAdapter().manifest.rate_limit
        self.assertGreaterEqual(rate_limit.requests_per_minute, 1)
        self.assertGreaterEqual(rate_limit.burst, 1)


class NucleiNonDestructiveTest(unittest.TestCase):
    def setUp(self):
        self.adapter = NucleiAdapter()

    def test_accepts_plain_http_target(self):
        self.assertTrue(self.adapter.non_destructive_check(TARGET, {}))
        self.assertTrue(self.adapter.non_destructive_check("https://site.test/", {}))

    def test_rejects_non_http_scheme(self):
        for target in ("file:///etc/passwd", "ftp://site.test/", "gopher://site.test/"):
            with self.subTest(target=target):
                self.assertFalse(self.adapter.non_destructive_check(target, {}))

    def test_rejects_network_range_targets(self):
        # 只允许单个 URL，禁止 CIDR/网段（范围扩大即越权）
        for target in ("http://192.168.0.0/24", "http://10.0.0.1-10.0.0.9"):
            with self.subTest(target=target):
                self.assertFalse(self.adapter.non_destructive_check(target, {}))

    def test_rejects_destructive_params(self):
        for key in ("interactsh_url", "fuzz", "headless", "payload", "exploit"):
            with self.subTest(param=key):
                self.assertFalse(self.adapter.non_destructive_check(TARGET, {key: "x"}))

    def test_rejects_unsafe_template_tags(self):
        self.assertFalse(self.adapter.non_destructive_check(TARGET, {"templates": ["http/fuzzing/x.yaml"]}))
        self.assertFalse(self.adapter.non_destructive_check(TARGET, {"tags": ["dos"]}))


class NucleiCommandTest(unittest.TestCase):
    def setUp(self):
        self.adapter = NucleiAdapter()
        self.command = self.adapter.build_command(TARGET, {})

    def test_targets_single_url_with_jsonl_output(self):
        self.assertIn("-u", self.command)
        self.assertEqual(self.command[self.command.index("-u") + 1], TARGET)
        self.assertIn("-jsonl", self.command)

    def test_disables_out_of_band_interaction(self):
        self.assertIn("-no-interactsh", self.command)

    def test_silent_is_enabled_by_default(self):
        self.assertIn("-silent", self.command)

    def test_silent_can_be_disabled_for_diagnostics(self):
        # 排查扫描异常时需要看到 nuclei 的 INFO/WRN（走 stderr，不影响 stdout 的 JSONL）
        command = self.adapter.build_command(TARGET, {"silent": False})
        self.assertNotIn("-silent", command)
        self.assertIn("-jsonl", command)

    def test_excludes_intrusive_template_tags(self):
        self.assertIn("-etags", self.command)
        excluded = self.command[self.command.index("-etags") + 1]
        for tag in ("fuzz", "intrusive", "dos"):
            self.assertIn(tag, excluded)

    def test_applies_manifest_rate_limit(self):
        # manifest 声明的是「每分钟」，而 nuclei 的 -rate-limit 单位是「每秒」，
        # 直接透传会放大 60 倍（实测 DVWA 被打到无响应）。必须换算。
        self.assertIn("-rate-limit", self.command)
        per_second = int(self.command[self.command.index("-rate-limit") + 1])
        duration = self.command[self.command.index("-rate-limit-duration") + 1]
        effective_per_minute = per_second * 60 / float(duration.rstrip("s"))
        self.assertAlmostEqual(
            effective_per_minute,
            self.adapter.manifest.rate_limit.requests_per_minute,
            places=6,
        )

    def test_rate_limit_never_exceeds_declared_per_minute(self):
        declared = self.adapter.manifest.rate_limit.requests_per_minute
        command = self.adapter.build_command(TARGET, {})
        per_second = int(command[command.index("-rate-limit") + 1])
        duration = float(command[command.index("-rate-limit-duration") + 1].rstrip("s"))
        self.assertLessEqual(per_second * 60 / duration, declared)

    def test_rate_limit_converts_explicit_per_minute_param(self):
        # 断言需求本身（实际速率 == 声明速率），而非某个具体参数编码
        for declared in (6, 30, 60, 120, 300):
            with self.subTest(requests_per_minute=declared):
                command = self.adapter.build_command(TARGET, {"rate_limit": declared})
                per_second = int(command[command.index("-rate-limit") + 1])
                duration = float(
                    command[command.index("-rate-limit-duration") + 1].rstrip("s")
                )
                self.assertAlmostEqual(per_second * 60 / duration, declared, places=6)

    def test_honours_explicit_template_list(self):
        command = self.adapter.build_command(TARGET, {"templates": ["http/cves/2021/x.yaml"]})
        self.assertIn("http/cves/2021/x.yaml", command)


class NucleiParseTest(unittest.TestCase):
    def setUp(self):
        self.findings = NucleiAdapter().parse_jsonl(NUCLEI_JSONL)

    def test_maps_to_sdd02_finding_fields(self):
        finding = self.findings[0]
        self.assertEqual(finding["type"], "VULN")
        self.assertEqual(finding["severity"], "CRITICAL")
        self.assertEqual(finding["title"], "Apache Path Traversal")
        self.assertEqual(finding["url"], "http://127.0.0.1:8080/cgi-bin/.%2e/")
        self.assertEqual(finding["tool_name"], "nuclei")

    def test_categorizes_misconfig_templates_as_weak_config(self):
        self.assertEqual(self.findings[1]["type"], "WEAK_CONFIG")

    def test_categorizes_exploit_templates_as_vuln(self):
        self.assertEqual(self.findings[0]["type"], "VULN")

    def test_maps_nuclei_severity_to_project_severity(self):
        self.assertEqual(self.findings[0]["severity"], "CRITICAL")
        self.assertEqual(self.findings[1]["severity"], "INFO")

    def test_uses_template_remediation_as_suggestion(self):
        self.assertEqual(self.findings[0]["suggestion"], "升级 Apache 至 2.4.51 及以上")

    def test_evidence_is_desensitized(self):
        # 证据不得包含响应正文，只保留模板标识与命中位置等可核查信息
        evidence = self.findings[0]["evidence"]
        self.assertIn("CVE-2021-41773", evidence)
        self.assertNotIn("raw", evidence)
        self.assertLessEqual(len(evidence), 300)

    def test_skips_malformed_lines(self):
        raw = NUCLEI_JSONL + "\n{not json}\n\n"
        self.assertEqual(len(NucleiAdapter().parse_jsonl(raw)), 2)

    def test_returns_empty_for_empty_output(self):
        self.assertEqual(NucleiAdapter().parse_jsonl(""), [])


class NucleiRunTest(unittest.TestCase):
    def test_returns_success_with_parsed_findings(self):
        adapter = NucleiAdapter(executor=_executor_returning(NUCLEI_JSONL))
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tool_id, "nuclei")
        self.assertEqual(len(result.findings), 2)

    def test_reports_failed_when_binary_missing(self):
        adapter = NucleiAdapter(executor=_executor_raising(FileNotFoundError("nuclei")))
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertIn("nuclei", result.error)
        self.assertEqual(result.findings, [])

    def test_reports_timeout_status(self):
        adapter = NucleiAdapter(
            executor=_executor_raising(subprocess.TimeoutExpired(cmd="nuclei", timeout=300))
        )
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "TIMEOUT")

    def test_reports_failed_on_unexpected_error(self):
        adapter = NucleiAdapter(executor=_executor_raising(RuntimeError("boom")))
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "FAILED")
        self.assertIn("boom", result.error)

    def test_registry_execute_path_returns_result(self):
        adapter = NucleiAdapter(executor=_executor_returning(NUCLEI_JSONL))
        registry = ToolRegistry()
        registry.register(adapter)
        result = registry.execute("nuclei", TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(registry.audit()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
