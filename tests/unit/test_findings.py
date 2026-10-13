"""T21 normalization, risk identity, idempotence and provenance checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.findings import (
    FindingValidationError,
    NonRiskFindingError,
    deduplicate_findings,
    normalize_finding,
    normalize_url,
    to_orm_values,
)


EARLY = "2026-10-13 09:00:00"
LATE = "2026-10-13 10:00:00"
URL = "http://fixture.invalid/risk"
FEATURE = {"control": "synthetic-config-check", "parameter": "header"}


def source(**changes):
    result = {
        "type": "WEAK_CONFIG", "severity": "medium", "url": URL,
        "title": "synthetic title", "evidence": "synthetic evidence",
        "impact": "synthetic impact", "suggestion": "synthetic suggestion",
        "raw": {"template-id": "synthetic-config-check"},
    }
    result.update(changes)
    return result


def record(data=None, *, task_id=11, scope_id=21, tool_name="nuclei", seen_at=EARLY, **options):
    return normalize_finding(source() if data is None else data, task_id=task_id, scope_id=scope_id,
                             tool_name=tool_name, seen_at=seen_at, **options)


class URLNormalizationTests(unittest.TestCase):
    def test_scheme_host_default_port_path_query_and_fragment_normalize(self):
        self.assertEqual(
            normalize_url("HTTP://Fixture.INVALID:80/a/./x/../%7Erisk?z=3&a=1#ignored"),
            "http://fixture.invalid/a/~risk?a=1&z=3",
        )
        self.assertEqual(normalize_url("https://Fixture.INVALID:443"), "https://fixture.invalid/")

    def test_duplicate_query_value_order_and_reserved_path_separators_are_preserved(self):
        first = normalize_url("http://fixture.invalid/risk?z=9&a=1&a=2")
        self.assertEqual(first, normalize_url("http://fixture.invalid/risk?a=1&z=9&a=2"))
        self.assertNotEqual(first, normalize_url("http://fixture.invalid/risk?a=2&a=1&z=9"))
        self.assertNotEqual(normalize_url("http://fixture.invalid/a%2Fb"),
                            normalize_url("http://fixture.invalid/a/b"))

    def test_nondefault_ports_path_case_and_query_values_are_distinct(self):
        self.assertNotEqual(normalize_url(URL), normalize_url("http://fixture.invalid:8080/risk"))
        self.assertNotEqual(normalize_url(URL), normalize_url("http://fixture.invalid/Risk"))
        self.assertNotEqual(normalize_url(URL + "?id=1"), normalize_url(URL + "?id=2"))

    def test_invalid_or_credentialed_targets_are_rejected(self):
        for target in ("file:///local", "/relative", "http://user:secret@fixture.invalid/",
                       "http://fixture.invalid:99999/", "http://fixture.invalid/\nheader"):
            with self.subTest(target=target):
                with self.assertRaises(FindingValidationError):
                    normalize_url(target)


class FindingNormalizationTests(unittest.TestCase):
    def test_standard_fields_context_and_defaults(self):
        finding = record(source(page_id=31))
        expected = {"type", "severity", "title", "url", "evidence", "impact", "suggestion",
                    "tool_name", "raw", "dedup_key", "first_seen", "last_seen", "status",
                    "occurrence", "task_id", "scope_id", "page_id"}
        self.assertTrue(expected <= set(finding))
        self.assertEqual(finding["type"], "WEAK_CONFIG")
        self.assertEqual(finding["severity"], "MEDIUM")
        self.assertEqual(finding["task_id"], 11)
        self.assertEqual(finding["scope_id"], 21)
        self.assertEqual(finding["page_id"], 31)
        self.assertEqual(finding["tool_name"], "nuclei")
        self.assertEqual(finding["status"], "OPEN")
        self.assertEqual(finding["occurrence"], 1)
        self.assertEqual(finding["first_seen"], EARLY)
        self.assertEqual(finding["last_seen"], EARLY)
        self.assertRegex(finding["dedup_key"], r"^[0-9a-f]{32}$")

    def test_dict_camelcase_and_dataclass_aliases_are_supported(self):
        data = source()
        data["findingType"] = data.pop("type")
        data["rawData"] = data.pop("raw")
        self.assertEqual(record(data)["dedup_key"], record()["dedup_key"])

        @dataclass
        class FindingInput:
            finding_type: str = "WEAK_CONFIG"
            severity: str = "MEDIUM"
            url: str = URL
            evidence: str = "synthetic evidence"

        self.assertEqual(record(FindingInput(), evidence_feature=FEATURE)["type"], "WEAK_CONFIG")

    def test_conflicting_type_and_raw_aliases_are_rejected(self):
        for data in (source(finding_type="SENSITIVE"), source(raw_data={"template-id": "different"})):
            with self.subTest(data=data):
                with self.assertRaises(FindingValidationError):
                    record(data)

    def test_supported_risk_categories_and_fingerprint_exclusion(self):
        for category in ("VULN", "WEAK_CONFIG", "MALICIOUS_LINK", "MALICIOUS_SCRIPT", "TAMPER", "SENSITIVE"):
            with self.subTest(category=category):
                self.assertEqual(record(source(type=category), evidence_feature=FEATURE)["type"], category)
        with self.assertRaises(NonRiskFindingError):
            record(source(type="FINGERPRINT"), evidence_feature=FEATURE)
        with self.assertRaises(FindingValidationError):
            record(source(type="UNSUPPORTED"), evidence_feature=FEATURE)

    def test_invalid_severity_status_context_and_time_are_rejected(self):
        for data in (source(severity="urgent"), source(status="UNKNOWN")):
            with self.subTest(data=data):
                with self.assertRaises(FindingValidationError):
                    record(data)
        for context in ({"task_id": 0}, {"scope_id": 0}, {"seen_at": "not-a-date"}):
            with self.subTest(context=context):
                with self.assertRaises(FindingValidationError):
                    record(**context)

    def test_utc_and_naive_datetimes_use_china_local_time(self):
        for instant in ("2026-10-13T01:00:00Z", datetime(2026, 10, 13, 1, tzinfo=timezone.utc),
                        datetime(2026, 10, 13, 9)):
            with self.subTest(instant=instant):
                finding = record(seen_at=instant)
                self.assertEqual(finding["first_seen"], EARLY)
                self.assertEqual(finding["last_seen"], EARLY)

    def test_normalization_is_pure_and_does_not_retain_original_raw(self):
        data = source(raw={"template-id": "synthetic-config-check", "response": {"body": "nested-secret"}})
        snapshot = deepcopy(data)
        finding = record(data)
        self.assertEqual(data, snapshot)
        self.assertNotIn("nested-secret", json.dumps(finding))
        finding["raw"]["observations"].append({"test": True})
        self.assertEqual(data, snapshot)

    def test_all_free_text_query_fragment_and_sensitive_path_are_safe(self):
        secret = "fixture-only-token"
        data = source(type="SENSITIVE", title=secret, evidence=secret, impact=secret,
                      suggestion=secret, url="http://fixture.invalid/" + secret + "?token=" + secret + "#" + secret,
                      raw={"rule_id": "fixture-token", "location": {"source": "body", "start": 0, "end": 18},
                           "response": secret, "headers": {"X-Secret": secret}})
        finding = record(data, tool_name="sensitive-scanner", evidence_feature={"secret": secret})
        serialized = json.dumps(finding, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("?", finding["url"])
        self.assertNotIn("#", finding["url"])
        self.assertIn("[REDACTED]", finding["evidence"])
        self.assertTrue(all(finding[key] for key in ("title", "impact", "suggestion")))

    def test_normalized_provenance_uses_hashes_and_traces_context(self):
        finding = record(source(page_id=31))
        raw = finding["raw"]
        self.assertRegex(raw["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(raw["identity"]["target_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(raw["identity"]["feature_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(raw["observations"]), 1)
        observation = raw["observations"][0]
        self.assertEqual(observation["task_id"], 11)
        self.assertEqual(observation["tool_name"], "nuclei")
        self.assertEqual(observation["seen_at"], EARLY)
        self.assertEqual(observation["page_id"], 31)
        self.assertEqual(observation["source_sha256"], raw["source_sha256"])


class RiskIdentityTests(unittest.TestCase):
    def test_equivalent_urls_and_tool_round_time_do_not_change_identity(self):
        first = record(source(url="HTTP://Fixture.INVALID:80/a/./%7Erisk?b=2&a=1#f"),
                       evidence_feature=FEATURE)
        second = record(source(url="http://fixture.invalid/a/~risk?a=1&b=2"),
                        task_id=12, tool_name="zap", seen_at=LATE, evidence_feature=FEATURE)
        self.assertEqual(first["dedup_key"], second["dedup_key"])

    def test_query_values_rule_ids_types_and_sensitive_locations_do_not_collapse(self):
        baseline = record(evidence_feature=FEATURE)
        changed = [record(source(url=URL + "?id=1"), evidence_feature=FEATURE),
                   record(source(type="VULN"), evidence_feature=FEATURE),
                   record(evidence_feature={"control": "other-risk"})]
        self.assertTrue(all(item["dedup_key"] != baseline["dedup_key"] for item in changed))
        sensitive = source(type="SENSITIVE", raw={"rule_id": "phone", "location": {"source": "body", "start": 1, "end": 12}})
        other = deepcopy(sensitive)
        other["raw"]["location"]["start"] = 20
        self.assertNotEqual(record(sensitive)["dedup_key"], record(other)["dedup_key"])

    def test_structured_rules_and_nuclei_templates_override_varying_evidence(self):
        for raw in ({"rule_id": "synthetic-rule"}, {"template-id": "synthetic-template"}):
            with self.subTest(raw=raw):
                self.assertEqual(record(source(raw=raw, evidence="evidence one"))["dedup_key"],
                                 record(source(raw=raw, evidence="evidence two"))["dedup_key"])

    def test_zap_plugin_parameter_distinguishes_risks(self):
        first = record(source(raw={"pluginId": "synthetic-plugin", "param": "username"}), tool_name="zap")
        second = record(source(raw={"pluginId": "synthetic-plugin", "param": "password"}), tool_name="zap")
        self.assertNotEqual(first["dedup_key"], second["dedup_key"])

    def test_fallback_normalizes_whitespace_but_keeps_distinct_evidence(self):
        self.assertEqual(record(source(raw={}, evidence="same  synthetic\n evidence"))["dedup_key"],
                         record(source(raw={}, evidence="same synthetic evidence"))["dedup_key"])
        self.assertNotEqual(record(source(raw={}, evidence="first evidence"))["dedup_key"],
                            record(source(raw={}, evidence="second evidence"))["dedup_key"])

    def test_feature_json_object_order_does_not_change_key(self):
        self.assertEqual(record(evidence_feature={"a": 1, "b": 2})["dedup_key"],
                         record(evidence_feature={"b": 2, "a": 1})["dedup_key"])

    def test_local_rule_ids_are_namespaced_until_shared_feature_is_explicit(self):
        first = source(raw={"rule_id": "001"}, evidence="synthetic missing-header issue")
        second = source(raw={"rule_id": "001"}, evidence="synthetic directory-listing issue")
        local = [record(first, tool_name="detector-a"), record(second, tool_name="detector-b")]
        self.assertNotEqual(local[0]["dedup_key"], local[1]["dedup_key"])
        self.assertEqual(len(deduplicate_findings(local)), 2)
        shared = [record(first, tool_name="detector-a", evidence_feature=FEATURE),
                  record(second, tool_name="detector-b", evidence_feature=FEATURE)]
        self.assertEqual(len(deduplicate_findings(shared)), 1)

    def test_generic_redacted_sensitive_evidence_requires_structured_identity(self):
        with self.assertRaises(FindingValidationError):
            record(source(type="SENSITIVE", evidence="[REDACTED]", raw={}))

    def test_opaque_t20_identity_prevents_distinct_redacted_target_collisions(self):
        template = source(type="SENSITIVE", url="http://fixture.invalid/%5BREDACTED%5D", evidence="[REDACTED]",
                          raw={"rule_id": "token", "target_sha256": "a" * 64,
                               "location": {"source": "body", "start": 0, "end": 18}})
        different = deepcopy(template)
        different["raw"]["target_sha256"] = "b" * 64
        self.assertNotEqual(record(template)["dedup_key"], record(different)["dedup_key"])
        self.assertEqual(record(template, identity_url=URL)["dedup_key"],
                         record(different, identity_url=URL)["dedup_key"])
        invalid = deepcopy(template)
        invalid["raw"]["target_sha256"] = "not-a-digest"
        with self.assertRaises(FindingValidationError):
            record(invalid)


class FindingDeduplicationTests(unittest.TestCase):
    def test_two_rounds_merge_update_time_count_and_trace(self):
        first = record(source(page_id=31), evidence_feature=FEATURE)
        second = record(source(page_id=32), task_id=12, seen_at=LATE, evidence_feature=FEATURE)
        merged = deduplicate_findings([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["first_seen"], EARLY)
        self.assertEqual(merged[0]["last_seen"], LATE)
        self.assertEqual(merged[0]["occurrence"], 2)
        self.assertEqual({o["task_id"] for o in merged[0]["raw"]["observations"]}, {11, 12})
        self.assertEqual({o["page_id"] for o in merged[0]["raw"]["observations"]}, {31, 32})

    def test_same_task_retries_and_two_tools_are_idempotent(self):
        first = record(evidence_feature=FEATURE)
        retry = record(seen_at=LATE, evidence_feature=FEATURE)
        other_tool = record(tool_name="zap", seen_at=LATE, evidence_feature=FEATURE)
        merged = deduplicate_findings([first, first, retry, other_tool])[0]
        self.assertEqual(merged["occurrence"], 1)
        self.assertEqual(merged["last_seen"], LATE)
        self.assertEqual({o["tool_name"] for o in merged["raw"]["observations"]}, {"nuclei", "zap"})
        self.assertEqual(len(merged["raw"]["observations"]), 2)
        nuclei_observation = next(item for item in merged["raw"]["observations"] if item["tool_name"] == "nuclei")
        self.assertEqual(nuclei_observation["seen_at"], LATE)
        self.assertEqual(merged["first_seen"], EARLY)

    def test_existing_history_keeps_confirmed_status_and_inputs_unchanged(self):
        first = record(source(status="CONFIRMED"), evidence_feature=FEATURE)
        second = record(task_id=12, seen_at=LATE, evidence_feature=FEATURE)
        existing = deduplicate_findings([second], existing=[first])
        incoming = [record(task_id=13, seen_at="2026-10-13 11:00:00", evidence_feature=FEATURE)]
        before = deepcopy((existing, incoming))
        result = deduplicate_findings(incoming, existing=existing)
        self.assertEqual((existing, incoming), before)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "CONFIRMED")
        self.assertEqual(result[0]["occurrence"], 3)
        self.assertEqual(result[0]["first_seen"], EARLY)
        self.assertEqual(result[0]["last_seen"], "2026-10-13 11:00:00")
        result[0]["raw"]["observations"].clear()
        self.assertEqual((existing, incoming), before)

    def test_different_scope_or_risk_does_not_merge(self):
        first = record(evidence_feature=FEATURE)
        other_scope = record(scope_id=22, evidence_feature=FEATURE)
        other_risk = record(evidence_feature={"control": "different"})
        self.assertEqual(first["dedup_key"], other_scope["dedup_key"])
        self.assertEqual(len(deduplicate_findings([first, other_scope, other_risk])), 3)

    def test_unsorted_rounds_use_min_first_and_max_last(self):
        records = [record(task_id=13, seen_at=LATE, evidence_feature=FEATURE),
                   record(task_id=11, seen_at=EARLY, evidence_feature=FEATURE),
                   record(task_id=12, seen_at="2026-10-13 09:30:00", evidence_feature=FEATURE)]
        merged = deduplicate_findings(records)[0]
        self.assertEqual(merged["first_seen"], EARLY)
        self.assertEqual(merged["last_seen"], LATE)
        self.assertEqual(merged["occurrence"], 3)

    def test_existing_statuses_are_kept_without_t22_transition(self):
        for status in ("CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED"):
            with self.subTest(status=status):
                old = record(source(status=status), evidence_feature=FEATURE)
                new = record(task_id=12, seen_at=LATE, evidence_feature=FEATURE)
                self.assertEqual(deduplicate_findings([new], existing=[old])[0]["status"], status)

    def test_same_key_with_incompatible_identity_is_rejected(self):
        first = record(evidence_feature=FEATURE)
        forged = deepcopy(first)
        forged["raw"]["identity"]["feature_sha256"] = "f" * 64
        with self.assertRaises(FindingValidationError):
            deduplicate_findings([first, forged])

    def test_existing_records_reject_untrusted_payloads_in_normalized_fields(self):
        for field in ("raw_extra", "observation_extra", "title"):
            with self.subTest(field=field):
                forged = record()
                if field == "raw_extra":
                    forged["raw"]["response_body"] = "fixture-only-token"
                elif field == "observation_extra":
                    forged["raw"]["observations"][0]["authorization"] = "fixture-only-token"
                else:
                    forged["title"] = "fixture-only-token"
                with self.assertRaises(FindingValidationError):
                    deduplicate_findings([], existing=[forged])

    def test_normalized_records_reject_invalid_enums_containers_and_missing_tool(self):
        variants = []
        for key, value in (("type", "UNKNOWN"), ("severity", "urgent"), ("status", "UNKNOWN"),
                           ("occurrence", True), ("raw", [])):
            forged = record()
            forged[key] = value
            variants.append(forged)
        no_tool = record()
        del no_tool["tool_name"]
        variants.append(no_tool)
        invalid_observations = record()
        invalid_observations["raw"]["observations"] = {}
        variants.append(invalid_observations)
        for index, forged in enumerate(variants):
            with self.subTest(index=index):
                with self.assertRaises(FindingValidationError):
                    deduplicate_findings([forged])

    def test_occurrence_must_agree_with_distinct_observed_tasks(self):
        forged = record()
        forged["occurrence"] = 2
        with self.assertRaises(FindingValidationError):
            deduplicate_findings([], existing=[forged])

    def test_record_times_must_enclose_and_match_observations(self):
        for change in ("late_observation", "late_first_seen", "late_last_seen"):
            with self.subTest(change=change):
                forged = record()
                if change == "late_observation":
                    forged["raw"]["observations"][0]["seen_at"] = LATE
                elif change == "late_first_seen":
                    forged["first_seen"] = LATE
                else:
                    forged["last_seen"] = LATE
                with self.assertRaises(FindingValidationError):
                    deduplicate_findings([], existing=[forged])

    def test_conflicting_historical_statuses_are_rejected(self):
        confirmed = record(source(status="CONFIRMED"))
        fixed = record(source(status="FIXED"))
        for history in ([confirmed, fixed], [fixed, confirmed]):
            with self.subTest(statuses=[item["status"] for item in history]):
                with self.assertRaises(FindingValidationError):
                    deduplicate_findings([], existing=history)

    def test_empty_input_keeps_history_without_mutation(self):
        self.assertEqual(deduplicate_findings([]), [])
        existing = [record()]
        self.assertEqual(deduplicate_findings([], existing=existing), existing)

    def test_orm_mapping_converts_names_times_and_keeps_safe_trace(self):
        finding = record()
        before = deepcopy(finding)
        values = to_orm_values(finding)
        self.assertEqual(finding, before)
        self.assertEqual(values["finding_type"], "WEAK_CONFIG")
        self.assertEqual(values["raw_data"], finding["raw"])
        self.assertNotIn("type", values)
        self.assertNotIn("raw", values)
        self.assertIsInstance(values["first_seen"], datetime)
        self.assertIsInstance(values["last_seen"], datetime)
        self.assertEqual(values["first_seen"].strftime("%Y-%m-%d %H:%M:%S"), EARLY)


class ExistingAdapterCompatibilityTests(unittest.TestCase):
    def test_current_nuclei_and_zap_parse_fixtures_without_execution(self):
        from src.tools.adapters.nuclei import NucleiAdapter
        from src.tools.adapters.zap import ZapAdapter

        with patch("subprocess.run", side_effect=AssertionError("binary invocation")), \
             patch("socket.create_connection", side_effect=AssertionError("network access")), \
             patch("httpx.get", side_effect=AssertionError("HTTP access")):
            nuclei = NucleiAdapter().parse_jsonl(json.dumps({
                "template-id": "synthetic-config-check", "matched-at": URL,
                "info": {"name": "fixture", "severity": "medium", "tags": ["misconfig"]},
                "response": "fixture-only-token",
            }))[0]
            zap = ZapAdapter().parse_alerts({"alerts": [{
                "pluginId": "synthetic-plugin", "alert": "Missing Header", "url": URL,
                "risk": "Medium", "evidence": "fixture-only-token", "param": "header",
            }]})[0]
            normalized = [record(nuclei, tool_name="nuclei", evidence_feature=FEATURE),
                          record(zap, tool_name="zap", evidence_feature=FEATURE)]
            merged = deduplicate_findings(normalized)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["type"], "WEAK_CONFIG")
        self.assertNotIn("fixture-only-token", json.dumps(merged))
        self.assertEqual({o["tool_name"] for o in merged[0]["raw"]["observations"]}, {"nuclei", "zap"})

    def test_current_tamper_and_malicious_dataclasses_normalize(self):
        from src.tools.tamper import TamperFinding
        from src.tools.external_link import MaliciousFinding

        tamper = TamperFinding(URL, "HIGH", "fixture title", "changed hash", "impact", "suggestion", page_id=31)
        malicious = MaliciousFinding("synthetic-link-rule", "MALICIOUS_LINK", URL, "HIGH",
                                     "fixture title", "fixture evidence", "impact", "suggestion")
        self.assertEqual(record(tamper, tool_name="tamper-detector")["type"], "TAMPER")
        self.assertEqual(record(tamper, tool_name="tamper-detector")["page_id"], 31)
        self.assertEqual(record(malicious, tool_name="external-link-detector")["type"], "MALICIOUS_LINK")

    def test_current_zap_parameter_suffix_preserves_distinct_risks(self):
        from src.tools.adapters.zap import ZapAdapter

        payload = {"alerts": [
            {"pluginId": "synthetic-plugin", "alert": "SQL Injection", "risk": "High", "url": URL,
             "param": parameter} for parameter in ("username", "password")
        ]}
        findings = [record(item, tool_name="zap") for item in ZapAdapter().parse_alerts(payload)]
        self.assertEqual(len(findings), 2)
        self.assertNotEqual(findings[0]["dedup_key"], findings[1]["dedup_key"])
        self.assertEqual(len(deduplicate_findings(findings)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
