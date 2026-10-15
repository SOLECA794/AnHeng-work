"""T20 behavioral and safety checks for the offline sensitive-information engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from src.tools.contract import ToolRegistry
from src.tools.sensitive import (
    AuthorizedScope,
    RuleValidationError,
    ScanDocument,
    ScanLimitError,
    SensitiveRule,
    SensitiveScanner,
    SensitiveToolAdapter,
    UnauthorizedTargetError,
    load_rules,
)


ORIGIN = "http://127.0.0.1:18084"
SCOPES = [AuthorizedScope("site-d", ORIGIN)]


class SensitiveScannerTests(unittest.TestCase):
    def scan(self, rules, text="", headers=None, url=ORIGIN + "/", scopes=None, **limits):
        scanner = SensitiveScanner(rules, **limits)
        return scanner.scan(
            ScanDocument(url, text, headers or {}),
            SCOPES if scopes is None else scopes,
        )

    def test_keyword_is_literal_and_case_sensitive(self):
        rule = SensitiveRule("keyword", "Literal marker", "KEYWORD", "a.b")
        self.assertEqual(len(self.scan([rule], "a.b")), 1)
        self.assertEqual(self.scan([rule], "axb A.B"), [])

    def test_regex_matches_and_rejects_negative(self):
        rule = SensitiveRule("phone", "Phone", "REGEX", r"(?<!\d)1[3-9]\d{9}(?!\d)")
        self.assertEqual(len(self.scan([rule], "contact: 13800138000")), 1)
        self.assertEqual(self.scan([rule], "code: 138001380000, normal: 12345"), [])

    def test_rule_can_be_disabled(self):
        rule = SensitiveRule("disabled", "Disabled", "KEYWORD", "SECRET", enabled=False)
        self.assertEqual(self.scan([rule], "SECRET"), [])

    def test_scope_binding_is_enforced(self):
        rule = SensitiveRule("bound", "Bound rule", "KEYWORD", "SECRET", scope_bind="site-d")
        self.assertEqual(len(self.scan([rule], "SECRET")), 1)
        self.assertEqual(
            self.scan([rule], "SECRET", scopes=[AuthorizedScope("other-id", ORIGIN)]),
            [],
        )

    def test_empty_scope_binding_is_global(self):
        rule = SensitiveRule("global", "Global rule", "KEYWORD", "SECRET", scope_bind="")
        for scope_id in ("site-d", "another-id"):
            with self.subTest(scope_id=scope_id):
                findings = self.scan([rule], "SECRET", scopes=[AuthorizedScope(scope_id, ORIGIN)])
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["scope_id"], scope_id)

    def test_scans_body_and_response_headers_with_locations(self):
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        findings = self.scan([rule], "prefix SECRET suffix", {"X-Debug": "SECRET"})
        self.assertEqual(len(findings), 2)
        by_source = {finding["location"]["source"]: finding for finding in findings}
        self.assertEqual(set(by_source), {"body", "header"})
        self.assertEqual(by_source["body"]["location"]["start"], 7)
        self.assertEqual(by_source["body"]["location"]["end"], 13)
        self.assertEqual(by_source["header"]["location"]["header"], "X-Debug")

    def test_filetype_uses_decoded_url_path_not_body_or_query(self):
        rule = SensitiveRule("env", "Environment file", "FILETYPE", ".env")
        for path in ("/.env", "/%2Eenv", "/CONFIG.ENV"):
            with self.subTest(path=path):
                findings = self.scan([rule], url=ORIGIN + path)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["location"]["source"], "url_path")
        self.assertEqual(self.scan([rule], "a link to /.env", url=ORIGIN + "/?file=.env"), [])
        self.assertEqual(self.scan([rule], url=ORIGIN + "/notes.env.txt"), [])

    def test_literal_sample(self):
        rule = SensitiveRule("sample", "Sample", "SAMPLE", "fixture-only-token")
        self.assertEqual(len(self.scan([rule], "prefix fixture-only-token suffix")), 1)
        self.assertEqual(self.scan([rule], "fixture-only-toke"), [])

    def test_sha256_sample_matches_whole_source(self):
        text = "approved synthetic sample\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rule = SensitiveRule("fingerprint", "Fingerprint", "SAMPLE", "sha256:" + digest)
        self.assertEqual(len(self.scan([rule], text)), 1)
        self.assertEqual(self.scan([rule], text + "extra"), [])

    def test_four_rule_types_return_normalized_findings(self):
        rules = [
            SensitiveRule("keyword", "Marker", "KEYWORD", "DEMO_ACCESS_TOKEN"),
            SensitiveRule("email", "Email", "REGEX", r"[\w.-]+@[\w.-]+\.[A-Za-z]{2,}"),
            SensitiveRule("file", "Backup", "FILETYPE", ".bak"),
            SensitiveRule("sample", "Token sample", "SAMPLE", "fixture-only-token"),
        ]
        findings = self.scan(
            rules,
            "DEMO_ACCESS_TOKEN=fixture-only-token\nfixture@example.invalid",
            url=ORIGIN + "/backup.bak",
        )
        self.assertEqual({finding["rule_type"] for finding in findings},
                         {"KEYWORD", "REGEX", "FILETYPE", "SAMPLE"})
        for finding in findings:
            self.assertEqual(finding["finding_type"], "SENSITIVE")
            self.assertEqual(finding["status"], "OPEN")
            self.assertEqual(finding["tool_name"], "sensitive-scanner")
            self.assertEqual(finding["severity"], "MEDIUM")
            self.assertIsInstance(finding["evidence"], str)
            self.assertTrue(finding["evidence"])
            self.assertIsInstance(finding["raw_data"], dict)

    def test_serialized_findings_remove_secrets_and_url_credentials(self):
        rules = [
            SensitiveRule("email", "Email", "REGEX", r"[\w.-]+@[\w.-]+\.[A-Za-z]{2,}"),
            SensitiveRule("phone", "Phone", "REGEX", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            SensitiveRule("token", "Token", "SAMPLE", "fixture-only-token"),
        ]
        findings = self.scan(
            rules,
            "fixture@example.invalid 13800138000 fixture-only-token",
            {"X-Contact": "fixture@example.invalid fixture-only-token"},
            url=ORIGIN + "/?key=query-secret-should-not-leak#fragment-secret",
        )
        self.assertGreater(len(findings), 0)
        serialized = json.dumps(findings, ensure_ascii=False)
        for secret in ("fixture@example.invalid", "13800138000", "fixture-only-token",
                       "query-secret-should-not-leak", "fragment-secret"):
            self.assertNotIn(secret, serialized)
        self.assertTrue(all(finding["url"] == ORIGIN + "/" for finding in findings))

    def test_sensitive_url_path_is_redacted_including_percent_encoding(self):
        secret = "fixture-only-token"
        rule = SensitiveRule("token", "Token sample", "SAMPLE", secret)
        encoded = "".join(f"%{ord(character):02X}" for character in secret)
        for path in ("/" + secret, "/" + encoded):
            with self.subTest(path=path):
                target = ORIGIN + path + "?key=query-secret#fragment-secret"
                findings = self.scan([rule], secret, url=target)
                self.assertEqual(len(findings), 1)
                serialized = unquote(json.dumps(findings, ensure_ascii=False))
                self.assertNotIn(secret, serialized)
                self.assertNotIn("query-secret", serialized)
                self.assertNotIn("fragment-secret", serialized)
                self.assertIn("[REDACTED]", unquote(findings[0]["url"]))
                self.assertEqual(findings[0]["raw_data"]["target_sha256"],
                                 hashlib.sha256(target.encode("utf-8")).hexdigest())

    def test_sensitive_header_name_is_redacted_in_all_location_metadata(self):
        secret = "fixture-only-token"
        rule = SensitiveRule("token", "Token sample", "SAMPLE", secret)
        findings = self.scan([rule], headers={"X-" + secret: secret})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["location"]["source"], "header")
        self.assertEqual(findings[0]["location"]["header"], "X-[REDACTED]")
        self.assertNotIn(secret, json.dumps(findings, ensure_ascii=False))

    def test_fingerprint_match_redacts_actual_sample_from_url_and_header_name(self):
        secret = "fixture-only-token"
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        rule = SensitiveRule("fingerprint", "Fingerprint", "SAMPLE", "sha256:" + digest)
        encoded = "".join(f"%{ord(character):02X}" for character in secret)
        for path in ("/" + secret, "/" + encoded):
            with self.subTest(path=path):
                findings = self.scan([rule], secret, {"X-" + secret: secret}, url=ORIGIN + path)
                self.assertEqual(len(findings), 2)
                self.assertNotIn(secret, unquote(json.dumps(findings, ensure_ascii=False)))
                header_finding = next(finding for finding in findings if finding["location"]["source"] == "header")
                self.assertEqual(header_finding["location"]["header"], "X-[REDACTED]")
                self.assertTrue(all("[REDACTED]" in unquote(finding["url"]) for finding in findings))

    def test_contextual_regex_match_redacts_naked_secret_from_url_and_header_name(self):
        secret = "fixture-only-token"
        rule = SensitiveRule("contextual", "Contextual token", "REGEX", r"(?<=token=)fixture-only-token")
        encoded = "".join(f"%{ord(character):02X}" for character in secret)
        for path in ("/" + secret, "/" + encoded):
            with self.subTest(path=path):
                findings = self.scan([rule], "token=" + secret,
                                     {"X-" + secret: "token=" + secret}, url=ORIGIN + path)
                self.assertEqual(len(findings), 2)
                self.assertNotIn(secret, unquote(json.dumps(findings, ensure_ascii=False)))
                header_finding = next(finding for finding in findings if finding["location"]["source"] == "header")
                self.assertEqual(header_finding["location"]["header"], "X-[REDACTED]")
                self.assertTrue(all("[REDACTED]" in unquote(finding["url"]) for finding in findings))

    def test_normal_page_is_negative(self):
        rules = [
            SensitiveRule("marker", "Marker", "KEYWORD", "DEMO_ACCESS_TOKEN"),
            SensitiveRule("phone", "Phone", "REGEX", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            SensitiveRule("file", "Environment file", "FILETYPE", ".env"),
            SensitiveRule("sample", "Sample", "SAMPLE", "fixture-only-token"),
        ]
        self.assertEqual(self.scan(rules, "普通的内部公告页面，无需联系电话。"), [])

    def test_unauthorized_host_port_scheme_and_missing_scope(self):
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        targets = [
            "http://example.invalid:18084/",
            "http://127.0.0.1:18085/",
            "https://127.0.0.1:18084/",
            "file:///etc/passwd",
            "http://user:password@127.0.0.1:18084/",
        ]
        for target in targets:
            with self.subTest(target=target):
                with self.assertRaises(UnauthorizedTargetError) as raised:
                    self.scan([rule], "SECRET", url=target)
                self.assertEqual(raised.exception.code, 40310)
        with self.assertRaises(UnauthorizedTargetError):
            self.scan([rule], "SECRET", scopes=[])

    def test_path_scope_requires_component_boundary(self):
        scopes = [AuthorizedScope("path", ORIGIN, "/safe")]
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        for path in ("/safe", "/safe/", "/safe/page"):
            with self.subTest(path=path):
                self.assertEqual(len(self.scan([rule], "SECRET", url=ORIGIN + path, scopes=scopes)), 1)
        for path in ("/safe-other", "/private", "/safe/../private", "/safe/%2e%2e/private",
                     "/safe/%252e%252e/private", "/safe%2f..%2fprivate", "/safe\\..\\private"):
            with self.subTest(path=path):
                with self.assertRaises(UnauthorizedTargetError):
                    self.scan([rule], "SECRET", url=ORIGIN + path, scopes=scopes)

    def test_invalid_regex_and_unknown_rule_type_are_rejected(self):
        for rule in (SensitiveRule("bad-regex", "Invalid", "REGEX", "["),
                     SensitiveRule("bad-type", "Invalid", "UNSUPPORTED", "x")):
            with self.subTest(rule=rule.id):
                with self.assertRaises(RuleValidationError):
                    SensitiveScanner([rule])

    def test_pathological_regex_has_bounded_execution(self):
        rule = SensitiveRule("slow", "Bounded expression", "REGEX", "^(a+)+$")
        start = time.monotonic()
        with self.assertRaises(ScanLimitError):
            self.scan([rule], "a" * 100_000 + "!", regex_timeout=0.005)
        self.assertLess(time.monotonic() - start, 2.0)

    def test_content_limit_does_not_report_truncated_input_as_success(self):
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        with self.assertRaises(ScanLimitError):
            self.scan([rule], "x" * 100 + "SECRET", max_content_chars=100)

    def test_header_content_is_subject_to_content_limit(self):
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        with self.assertRaises(ScanLimitError):
            self.scan([rule], "", {"X-Large": "x" * 100 + "SECRET"}, max_content_chars=100)

    def test_finding_limit_does_not_silently_drop_matches(self):
        rule = SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")
        with self.assertRaises(ScanLimitError):
            self.scan([rule], "SECRET SECRET", max_findings=1)


class RuleLoadingTests(unittest.TestCase):
    def test_loads_config_and_preserves_enabled_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps([
                {"id": "marker", "name": "Marker", "rule_type": "KEYWORD",
                 "pattern": "SECRET", "severity": "HIGH", "enabled": False,
                 "scope_bind": "site-d"},
            ]), encoding="utf-8")
            rules = load_rules(path)
        self.assertEqual(len(rules), 1)
        self.assertIsInstance(rules[0], SensitiveRule)
        self.assertFalse(rules[0].enabled)
        self.assertEqual(rules[0].scope_bind, "site-d")
        self.assertEqual(rules[0].severity, "HIGH")

    def test_defaults_cover_all_four_types(self):
        path = Path(__file__).resolve().parents[1] / "src/tools/rules/sensitive-defaults.json"
        rules = load_rules(path)
        self.assertEqual({rule.rule_type for rule in rules}, {"KEYWORD", "REGEX", "FILETYPE", "SAMPLE"})
        self.assertEqual(len({rule.id for rule in rules}), len(rules))


class SensitiveAdapterTests(unittest.TestCase):
    def setUp(self):
        scanner = SensitiveScanner([SensitiveRule("marker", "Marker", "KEYWORD", "SECRET")])
        self.adapter = SensitiveToolAdapter(scanner, SCOPES)
        self.registry = ToolRegistry()
        self.registry.register(self.adapter)

    def test_registry_call_is_local_read_only_and_audited(self):
        self.assertTrue(self.adapter.manifest.read_only)
        self.assertEqual(self.adapter.manifest.execution_mode, "local")
        self.assertIn("SENSITIVE", self.adapter.manifest.categories)
        with patch("socket.create_connection", side_effect=AssertionError("network access")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network access")), \
             patch("builtins.open", side_effect=AssertionError("file access")):
            result = self.registry.execute(
                self.adapter.manifest.id, ORIGIN + "/", {"text": "SECRET", "headers": {}},
            )
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(self.registry.audit()[-1]["status"], "SUCCESS")

    def test_registry_rejects_out_of_scope_target_and_audits_rejection(self):
        result = self.registry.execute(
            self.adapter.manifest.id, "http://127.0.0.1:18085/", {"text": "SECRET"},
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.findings, [])
        self.assertEqual(self.registry.audit()[-1]["status"], "REJECTED")

    def test_adapter_requires_local_input_and_rejects_mutating_requests(self):
        self.assertFalse(self.adapter.non_destructive_check(ORIGIN + "/", {"fetch": True}))
        self.assertFalse(self.adapter.non_destructive_check(
            ORIGIN + "/", {"text": "SECRET", "method": "POST"},
        ))
        self.assertFalse(self.adapter.non_destructive_check(
            ORIGIN + "/", {"file_path": "tests/fixtures/sites/d/index.html"},
        ))

    def test_adapter_rejects_malformed_parameter_containers(self):
        for params in (None, 1, ["text"]):
            with self.subTest(params=params):
                self.assertFalse(self.adapter.non_destructive_check(ORIGIN + "/", params))
                result = self.adapter.run(ORIGIN + "/", params)
                self.assertEqual(result.status, "REJECTED")
                self.assertEqual(result.findings, [])

    def test_adapter_manifests_have_independent_enabled_flags(self):
        other = SensitiveToolAdapter(self.adapter.scanner, SCOPES)
        self.adapter.manifest.enabled = False
        self.assertTrue(other.manifest.enabled)
        first = self.registry.execute(self.adapter.manifest.id, ORIGIN + "/", {"text": "SECRET"})
        self.assertEqual(first.status, "REJECTED")
        other_registry = ToolRegistry()
        other_registry.register(other)
        second = other_registry.execute(other.manifest.id, ORIGIN + "/", {"text": "SECRET"})
        self.assertEqual(second.status, "SUCCESS")

    def test_registry_audit_masks_targets_on_success_and_rejection(self):
        targets = [
            (ORIGIN + "/fixture-only-token?key=query-secret#fragment-secret", "SUCCESS"),
            ("http://example.invalid/fixture-only-token?key=query-secret#fragment-secret", "REJECTED"),
        ]
        for target, status in targets:
            result = self.registry.execute(self.adapter.manifest.id, target, {"text": "SECRET"})
            self.assertEqual(result.status, status)
            entry = self.registry.audit()[-1]
            self.assertEqual(entry["status"], status)
            self.assertEqual(entry["target"], "sha256:" + hashlib.sha256(target.encode("utf-8")).hexdigest())
        audit = json.dumps(self.registry.audit())
        for secret in ("fixture-only-token", "query-secret", "fragment-secret"):
            self.assertNotIn(secret, audit)


if __name__ == "__main__":
    unittest.main()
