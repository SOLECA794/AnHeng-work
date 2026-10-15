"""Nuclei 适配器 —— 真实输出回归测试（T15 真机验证）。

数据来源：2026-10-13 对 DVWA 授权靶场（Docker，http://127.0.0.1:8080）的真实扫描输出，
以容器方式执行（`docker run projectdiscovery/nuclei:latest`，目标改写为 host.docker.internal），
参数与适配器 `build_command` 完全一致：

    -u <target> -jsonl -silent -no-interactsh -etags fuzz,intrusive,dos -rate-limit 30 -timeout 10
    -t http/misconfiguration/ -t http/exposures/ -t http/technologies/

夹具：tests/fixtures/tool_output/nuclei_dvwa_2026-10-13.jsonl（19 条真实命中，已脱敏保留原样）

本文件的作用：把「一次真机跑出来的真实输出」固化为回归资产——解析器此前只用人工样例测过，
真实输出会带来字段缺失、无 scheme 的 URL、重复条目等人工样例覆盖不到的情况。

运行：python tests/unit/test_nuclei_real_output.py -v
"""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.nuclei import NucleiAdapter  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/tool_output/nuclei_dvwa_2026-10-13.jsonl"
PROJECT_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


class NucleiRealOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_text(encoding="utf-8")
        cls.findings = NucleiAdapter().parse_jsonl(cls.raw)

    def test_fixture_holds_real_scan_lines(self):
        lines = [line for line in self.raw.splitlines() if line.strip()]
        self.assertEqual(len(lines), 19)
        for line in lines:  # 每行都应是合法 JSON（真机输出不含脏行）
            self.assertIsInstance(json.loads(line), dict)

    def test_parses_every_real_line(self):
        self.assertEqual(len(self.findings), 19)

    def test_every_finding_carries_required_fields(self):
        for finding in self.findings:
            with self.subTest(title=finding.get("title")):
                self.assertIn(finding["type"], {"VULN", "WEAK_CONFIG"})
                self.assertIn(finding["severity"], PROJECT_SEVERITIES)
                self.assertTrue(finding["title"])
                self.assertTrue(finding["evidence"])
                self.assertTrue(finding["suggestion"])
                self.assertEqual(finding["tool_name"], "nuclei")

    def test_maps_real_nuclei_severities(self):
        severities = {finding["severity"] for finding in self.findings}
        self.assertEqual(severities, {"INFO", "MEDIUM"})
        medium = [f for f in self.findings if f["severity"] == "MEDIUM"]
        self.assertEqual(len(medium), 1)
        self.assertIn("Configuration Files Listing", medium[0]["title"])

    def test_evidence_is_desensitized_and_bounded(self):
        # 证据只保留模板标识/命中位置/标签，不得包含响应正文
        for finding in self.findings:
            with self.subTest(title=finding["title"]):
                self.assertLessEqual(len(finding["evidence"]), 300)
                self.assertIn("模板：", finding["evidence"])

    def test_evidence_keeps_template_id_for_traceability(self):
        # 抽查：真实的 configuration-listing 模板应可从证据回溯
        listing = next(
            f for f in self.findings if "Configuration Files Listing" in f["title"]
        )
        self.assertIn("configuration-listing", listing["evidence"])

    def test_real_output_contains_repeated_findings_for_downstream_dedup(self):
        """真实输出会重复：同一模板逐条输出（如 Missing Security Headers 命中 10 条）。

        这是 T21（F4 结果归一化与去重）必须兜住的输入特征——
        重复条目的 (url, evidence) 完全相同，可由 dedup_key 归并。
        """
        signatures = [(f["url"], f["evidence"]) for f in self.findings]
        duplicated = len(signatures) - len(set(signatures))
        self.assertGreater(duplicated, 0, "真实输出中应存在需下游去重的重复条目")

    def test_records_bare_host_urls_without_scheme(self):
        """真实输出存在不带 scheme 的 matched-at（如 host.docker.internal:8080）。

        归一化层需补全 scheme 才能与原 URL 对齐；此处先固化这一真实特征。
        """
        bare = [f for f in self.findings if not f["url"].startswith("http")]
        self.assertTrue(bare, "真实输出中应有不带 scheme 的命中位置")
        for finding in bare:
            self.assertNotIn("://", finding["url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
