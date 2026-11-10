"""Only complete, authorized observations of the original rules may close risk."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.discovery import content_hash
from src.services.findings import normalize_finding
from src.services.retesting import RetestDispatcher, RetestSnapshot
from src.tools.contract import ToolResult
from src.tools.external_link import scan_page
from src.tools.page_retest import PageRetestBinding, PageRetestRunner, link_policy_sha256, normalize_page_finding
from src.tools.sensitive import ScanDocument
from src.tools.sensitive_retest import FreshDocument
from src.tools.tool_retest import CoveredToolRun, ToolRetestBinding, ToolRetestRunner


AT = "2026-11-10 09:00:00"
URL = "https://site.invalid/approved/page"
TARGET = "https://blocked.invalid/payload"
BLACKLIST = ["blocked.invalid"]


def snapshot(record):
    return RetestSnapshot(1, 1, 1, record["type"], record["tool_name"], record["dedup_key"], "OPEN",
                          "a" * 64, AT, "site.invalid", "PATH", "/approved",
                          record["raw"]["identity"]["target_sha256"])


class ExtendedRetestTest(unittest.TestCase):
    def link_case(self):
        html = f'<a href="{TARGET}">demo</a>'
        raw, = scan_page(html, URL, site_host="site.invalid", blacklist=BLACKLIST)
        record = normalize_page_finding(raw, scope_id=1, task_id=1, page_url=URL, seen_at=AT)
        binding = PageRetestBinding(1, record["dedup_key"], URL, record["type"], TARGET, raw.rule_id,
                                    link_policy_sha256(BLACKLIST))
        return snapshot(record), binding, html

    def test_external_link_matches_original_page_and_target(self):
        state, binding, html = self.link_case()
        calls = []
        def fresh(b):
            calls.append(b.page_url)
            return FreshDocument(ScanDocument(b.page_url, html), AT)
        runner = PageRetestRunner(lambda _: binding, fresh, blacklist_provider=lambda _: BLACKLIST)
        result = runner.run(state)
        self.assertEqual((result.outcome, result.source_sha256, calls), ("MATCH", content_hash(html), [URL]))
        self.assertEqual(runner.run(replace(state, scope_value="/elsewhere")).outcome, "INCONCLUSIVE")
        self.assertEqual(calls, [URL])

    def test_incomplete_page_or_changed_rule_policy_does_not_close(self):
        state, binding, _ = self.link_case()
        fresh = FreshDocument(ScanDocument(URL, ""), AT)
        for invalid in (replace(fresh, status_code=302), replace(fresh, coverage_complete=False),
                        replace(fresh, document=ScanDocument("https://other.invalid/", ""))):
            runner = PageRetestRunner(lambda _: binding, lambda _: invalid, blacklist_provider=lambda _: BLACKLIST)
            self.assertEqual(runner.run(state).outcome, "INCONCLUSIVE")
        runner = PageRetestRunner(lambda _: binding, lambda _: fresh, blacklist_provider=lambda _: [])
        self.assertEqual(runner.run(state).outcome, "INCONCLUSIVE")

    def tool_case(self, tool, kind):
        raw = {"template-id": "rule-1", "matcher-name": "specific"} if tool == "nuclei" else {"pluginId": "10021"}
        source = {"type": kind, "url": URL, "raw": raw, "evidence": "placeholder"}
        record = normalize_finding(source, task_id=1, scope_id=1, tool_name=tool, seen_at=AT)
        rule_id, qualifier = ("rule-1", "specific") if tool == "nuclei" else ("10021", None)
        binding = ToolRetestBinding(1, record["dedup_key"], kind, tool, URL, rule_id, qualifier, "b" * 64)
        result = ToolResult(tool_id=tool, status="SUCCESS", findings=[source])
        return snapshot(record), binding, CoveredToolRun(binding, AT, result, True, True)

    def test_covered_tool_retest_matches_only_the_original_rule(self):
        for tool, kind in (("nuclei", "VULN"), ("zap", "WEAK_CONFIG")):
            state, binding, receipt = self.tool_case(tool, kind)
            with self.subTest(tool=tool):
                runner = ToolRetestRunner(lambda _: binding, lambda _: receipt)
                self.assertEqual(runner.run(state).outcome, "MATCH")
                empty = replace(receipt, result=ToolResult(tool_id=tool, status="SUCCESS"))
                runner = ToolRetestRunner(lambda _: binding, lambda _: empty)
                self.assertEqual(runner.run(state).outcome, "CLEAN")
                self.assertEqual(RetestDispatcher({(kind, tool): runner}).run(
                    replace(state, tool_name="other")).outcome, "INCONCLUSIVE")

    def test_empty_failed_or_unattested_tool_result_is_inconclusive(self):
        state, binding, receipt = self.tool_case("nuclei", "VULN")
        bad_run = replace(receipt, result=ToolResult(tool_id="nuclei", status="FAILED"))
        malformed = replace(receipt, result=ToolResult(tool_id="nuclei", status="SUCCESS", findings=[{}]))
        for invalid in (bad_run, malformed, replace(receipt, coverage_complete=False),
                        replace(receipt, fresh_isolated_run=False),
                        replace(receipt, binding=replace(binding, rules_sha256="f" * 64))):
            self.assertEqual(ToolRetestRunner(lambda _: binding, lambda _: invalid).run(state).outcome, "INCONCLUSIVE")
