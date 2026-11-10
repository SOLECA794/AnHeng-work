"""T23 grading decisions, policy validation and compatibility with T21."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.services.findings import normalize_finding, deduplicate_findings, to_orm_values
from src.services.risk_grading import (
    DEFAULT_POLICY, GradingContext, GradingPolicy, GradingValidationError,
    cvss_to_severity, grade_finding, graded_orm_values, load_grading_policy,
)


def record(kind="SENSITIVE", severity="MEDIUM", **options):
    source = dict(type=kind, severity=severity, url="http://fixture.invalid/private?token=fixture-only-token",
                  evidence="fixture-only-token", impact="fixture-only-token", suggestion="fixture-only-token",
                  rule_id="fixture-rule")
    source.update(options)
    return normalize_finding(source, task_id=1, scope_id=1, tool_name="fixture-scanner", seen_at="2026-10-15 09:00:00")


def context(item, **options):
    return GradingContext(scope_id=item["scope_id"], target_sha256=item["raw"]["identity"]["target_sha256"], **options)


def policy_data():
    return json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))


class CvssTests(unittest.TestCase):
    def test_all_band_boundaries(self):
        for value, expected in ((0, "INFO"), (0.1, "LOW"), (3.9, "LOW"), (4, "MEDIUM"),
                                (6.9, "MEDIUM"), (7, "HIGH"), (8.9, "HIGH"), (9, "CRITICAL"), (10, "CRITICAL")):
            with self.subTest(value=value):
                self.assertEqual(cvss_to_severity(value), expected)

    def test_decimal_and_numeric_strings(self):
        for value in ("7", "7.0", "7.00", Decimal("7.0")):
            self.assertEqual(cvss_to_severity(value), "HIGH")

    def test_invalid_scores_are_rejected_without_echo(self):
        invalid = (None, True, False, -0.1, 10.1, 3.95, float("nan"), float("inf"),
                   Decimal("NaN"), Decimal("sNaN"), Decimal("3.999999999999999999999999999999999"),
                   "NaN", "Infinity", "7e0", " 7", "7 ", "", "-0",
                   "+7", "7.1.0", "fixture-only-token", "CVSS:3.1/AV:N", {}, [], "1" * 100)
        for value in invalid:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(GradingValidationError, "^invalid CVSS score$"):
                    cvss_to_severity(value)

    def test_vulnerability_score_wins_over_detector_level(self):
        item = record("VULN", "CRITICAL")
        grade = grade_finding(item, context(item, cvss_score=4.0), load_grading_policy())
        self.assertEqual(grade["severity"], "MEDIUM")
        self.assertEqual(grade["grading"]["base_source"], "CVSS_3_1")

    def test_missing_score_uses_detector_and_is_distinct_from_zero(self):
        item = record("VULN", "HIGH")
        fallback = grade_finding(item, context(item), load_grading_policy())
        zero = grade_finding(item, context(item, cvss_score=0, page_importance="CORE", exposure="INTERNET"), load_grading_policy())
        self.assertEqual(fallback["severity"], "HIGH")
        self.assertIn("CVSS_MISSING_FALLBACK", fallback["grading"]["reason_codes"])
        self.assertEqual(zero["severity"], "INFO")


class PolicyTests(unittest.TestCase):
    def test_default_policy_and_order_independent_digest(self):
        data = policy_data()
        first = GradingPolicy(data)
        data["rule_overrides"].reverse()
        second = GradingPolicy(dict(reversed(list(data.items()))))
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(first.sha256), 64)

    def test_changed_configuration_changes_digest(self):
        data = policy_data()
        first = GradingPolicy(data)
        data["page_floors"]["CORE"] = "CRITICAL"
        self.assertNotEqual(first.sha256, GradingPolicy(data).sha256)

    def test_missing_and_unknown_policy_fields(self):
        for data in ({}, dict(policy_data(), extra="fixture-only-token"), []):
            with self.assertRaises(GradingValidationError):
                GradingPolicy(data)

    def test_invalid_version_and_floor_enums(self):
        for key, value in (("version", "fixture-only-token\n"), ("page_floors", {"UNKNOWN": "HIGH"}),
                           ("page_floors", {"CORE": []}), ("exposure_floors", {"UNKNOWN": "HIGH"}),
                           ("exposure_floors", {"INTERNET": "severe"})):
            data = policy_data()
            data[key] = value
            with self.assertRaises(GradingValidationError):
                GradingPolicy(data)

    def test_rule_fields_and_tool_risk_namespaces(self):
        baseline = dict(type="SENSITIVE", tool_name="fixture-scanner", rule_id="fixture-rule", severity="CRITICAL")
        for change in ({"type": "VULN"}, {"type": []}, {"tool_name": "bad tool"},
                       {"rule_id": "bad\n"}, {"severity": False}, {"extra": True}):
            data = policy_data()
            data["rule_overrides"] = [dict(baseline, **change)]
            with self.assertRaises(GradingValidationError):
                GradingPolicy(data)

    def test_duplicate_rules_and_rule_limit(self):
        for count in (2, 257):
            data = policy_data()
            data["rule_overrides"] = [data["rule_overrides"][0]] * count
            with self.assertRaises(GradingValidationError):
                GradingPolicy(data)

    def test_duplicate_nested_json_keys_are_rejected(self):
        text = DEFAULT_POLICY.read_text(encoding="utf-8").replace('"HOME": "HIGH"', '"HOME": "HIGH", "HOME": "LOW"')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(GradingValidationError, "duplicate policy JSON key"):
                load_grading_policy(path)

    def test_malformed_missing_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            with self.assertRaisesRegex(GradingValidationError, "invalid policy file"):
                load_grading_policy(path)
            for value in (b"not json fixture-only-token", b"\xff", b" " * 65_537):
                path.write_bytes(value)
                with self.assertRaises(GradingValidationError):
                    load_grading_policy(path)

    def test_policy_is_immutable_and_does_not_reference_source(self):
        data = policy_data()
        policy = GradingPolicy(data)
        data["page_floors"]["CORE"] = "LOW"
        self.assertEqual(policy.page_floors["CORE"], "HIGH")
        with self.assertRaises(TypeError):
            policy.page_floors["CORE"] = "LOW"
        with self.assertRaises(AttributeError):
            policy._values = ()


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_grading_policy()
        self.item = record()

    def grade(self, item=None, **options):
        item = self.item if item is None else item
        return grade_finding(item, context(item, **options), self.policy)

    def test_home_login_and_core_floor(self):
        for page in ("HOME", "LOGIN", "CORE"):
            result = self.grade(page_importance=page)
            self.assertEqual(result["severity"], "HIGH")
            self.assertIn("IMPORTANT_PAGE_FLOOR", result["grading"]["reason_codes"])

    def test_internet_floor_and_internal_no_promotion(self):
        for exposure, expected in (("INTERNET", "HIGH"), ("INTERNAL", "MEDIUM"), ("RESTRICTED", "MEDIUM")):
            self.assertEqual(self.grade(exposure=exposure)["severity"], expected)

    def test_two_impact_factors_take_max_not_additive(self):
        result = self.grade(page_importance="LOGIN", exposure="INTERNET")
        self.assertEqual(result["severity"], "HIGH")
        self.assertEqual(result["grading"]["base_severity"], "MEDIUM")

    def test_existing_critical_risk_is_not_downgraded(self):
        self.assertEqual(self.grade(record(severity="CRITICAL"), page_importance="CORE", exposure="INTERNET")["severity"], "CRITICAL")

    def test_info_is_not_promoted(self):
        result = self.grade(record(severity="INFO"), page_importance="CORE", exposure="INTERNET")
        self.assertEqual(result["severity"], "INFO")
        self.assertIn("INFO_PRESERVED", result["grading"]["reason_codes"])

    def test_unknown_metadata_is_explicit_and_not_inferred_from_display_url(self):
        result = self.grade()
        self.assertEqual(result["severity"], "MEDIUM")
        self.assertIn("PAGE_UNKNOWN", result["grading"]["reason_codes"])
        self.assertIn("EXPOSURE_UNKNOWN", result["grading"]["reason_codes"])
        self.assertIn("未知", result["impact"])

    def test_configured_floors_change_decision(self):
        data = policy_data()
        data["page_floors"]["CORE"] = "CRITICAL"
        data["exposure_floors"] = {}
        result = grade_finding(self.item, context(self.item, page_importance="CORE"), GradingPolicy(data))
        self.assertEqual(result["severity"], "CRITICAL")

    def test_rule_override_matches_exact_tool_and_type(self):
        data = policy_data()
        data["rule_overrides"] = [dict(type="SENSITIVE", tool_name="fixture-scanner", rule_id="fixture-rule", severity="CRITICAL")]
        policy = GradingPolicy(data)
        result = grade_finding(self.item, context(self.item, rule_id="fixture-rule"), policy)
        self.assertEqual(result["severity"], "CRITICAL")
        for item in (record("TAMPER"), dict(self.item, tool_name="other-scanner")):
            self.assertEqual(grade_finding(item, context(item, rule_id="fixture-rule"), policy)["severity"], "MEDIUM")

    def test_unconfigured_rule_retains_detector_level(self):
        result = self.grade(record(severity="HIGH"), rule_id="unconfigured")
        self.assertEqual(result["severity"], "HIGH")
        self.assertEqual(result["grading"]["base_source"], "DETECTOR_SEVERITY")

    def test_cvss_on_non_vulnerability_and_rule_on_vulnerability_rejected(self):
        with self.assertRaises(GradingValidationError):
            self.grade(cvss_score=7)
        with self.assertRaises(GradingValidationError):
            self.grade(record("VULN"), rule_id="fixture-rule")

    def test_scope_and_target_context_mismatch_rejected(self):
        ctx = context(self.item)
        for wrong in (replace(ctx, scope_id=2), replace(ctx, target_sha256="0" * 64)):
            with self.assertRaisesRegex(GradingValidationError, "context does not match"):
                grade_finding(self.item, wrong, self.policy)

    def test_context_invalid_types_and_enums_rejected(self):
        for key, value in (("scope_id", True), ("scope_id", 0), ("target_sha256", "fixture-only-token"),
                           ("page_importance", []), ("page_importance", "home"), ("exposure", "PUBLIC"),
                           ("rule_id", "bad\n"), ("cvss_score", "NaN")):
            values = dict(scope_id=1, target_sha256="a" * 64)
            values[key] = value
            with self.assertRaises(GradingValidationError):
                GradingContext(**values)

    def test_malformed_or_fingerprint_records_rejected(self):
        for change in ({"type": "FINGERPRINT"}, {"severity": "BAD"}, {"evidence": "fixture-only-token"},
                       {"impact": "fixture-only-token"}, {"raw": {"identity": {}}}, {"unexpected": "fixture-only-token"}):
            with self.assertRaisesRegex(GradingValidationError, "invalid normalized finding"):
                grade_finding(dict(self.item, **change), context(self.item), self.policy)

    def test_plain_request_context_and_policy_are_not_accepted(self):
        for ctx, pol in (({}, self.policy), (context(self.item), policy_data())):
            with self.assertRaises(GradingValidationError):
                grade_finding(self.item, ctx, pol)

    def test_every_risk_type_has_evidence_impact_and_actionable_suggestion(self):
        for kind in ("VULN", "WEAK_CONFIG", "MALICIOUS_LINK", "MALICIOUS_SCRIPT", "TAMPER", "SENSITIVE"):
            result = self.grade(record(kind))
            self.assertEqual(result["evidence"], self.item["evidence"] if kind == "SENSITIVE" else record(kind)["evidence"])
            self.assertGreater(len(result["impact"]), 20)
            self.assertIn("复测", result["suggestion"])

    def test_no_source_text_in_export_or_error(self):
        result = self.grade(rule_id="fixture-only-token")
        self.assertNotIn("fixture-only-token", json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(result["grading"]["rule_sha256"]), 64)

    def test_input_immutable_output_independent_and_repeatable(self):
        before = deepcopy(self.item)
        result = self.grade()
        self.assertEqual(result, self.grade())
        result["grading"]["reason_codes"].append("CHANGED")
        self.assertNotIn("CHANGED", self.grade()["grading"]["reason_codes"])
        self.assertEqual(self.item, before)

    def test_orm_conversion_preserves_identity_state_and_detection_times(self):
        item = record(status="CONFIRMED")
        original = to_orm_values(item)
        values = graded_orm_values(item, context(item, page_importance="CORE"), self.policy)
        self.assertEqual(values["severity"], "HIGH")
        for key in set(original) - {"severity", "impact", "suggestion", "raw_data"}:
            self.assertEqual(values[key], original[key])
        for key in original["raw_data"]:
            self.assertEqual(values["raw_data"][key], original["raw_data"][key])
        self.assertIn("grading", values["raw_data"])

    def test_deduplication_precedes_grading_and_remains_idempotent(self):
        merged = deduplicate_findings([self.item, self.item])[0]
        first = graded_orm_values(merged, context(merged, exposure="INTERNET"), self.policy)
        self.assertEqual(first, graded_orm_values(merged, context(merged, exposure="INTERNET"), self.policy))
        self.assertEqual(merged["occurrence"], 1)
        self.assertNotIn("grading", merged["raw"])

    def test_grading_performs_no_network_calls(self):
        with patch("socket.socket", side_effect=AssertionError("unexpected network")):
            self.assertEqual(self.grade()["severity"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()
