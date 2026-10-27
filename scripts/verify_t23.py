"""Deterministic T23 acceptance: fixed synthetic records and F's local fixtures only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.findings import normalize_finding, deduplicate_findings
from src.services.risk_grading import (
    DEFAULT_POLICY, GradingContext, GradingPolicy, GradingValidationError,
    cvss_to_severity, grade_finding, graded_orm_values, load_grading_policy,
)
from src.services.discovery import Page
from src.tools.tamper import Baseline, detect_tamper
from src.tools.external_link import scan_page
from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner, load_rules


def normalize(source, tool="fixture-scanner", scope=1):
    return normalize_finding(source, task_id=1, scope_id=scope, tool_name=tool, seen_at="2026-10-15 09:00:00")


def fixture(kind="SENSITIVE", severity="MEDIUM", status="OPEN"):
    return normalize(dict(type=kind, severity=severity, status=status, url="http://fixture.invalid/private",
                          rule_id="fixture-rule", evidence="fixture-only-token"))


def ctx(item, **options):
    return GradingContext(item["scope_id"], item["raw"]["identity"]["target_sha256"], **options)


def evaluate():
    checks = []
    policy = load_grading_policy()

    def check(name, passed, evidence):
        checks.append(dict(name=name, passed=bool(passed), evidence=evidence))

    def grade(item, **options):
        return grade_finding(item, ctx(item, **options), policy)

    boundaries = [(0, "INFO"), (0.1, "LOW"), (3.9, "LOW"), (4, "MEDIUM"), (6.9, "MEDIUM"),
                  (7, "HIGH"), (8.9, "HIGH"), (9, "CRITICAL"), (10, "CRITICAL")]
    actual = [dict(score=score, expected=expected, actual=cvss_to_severity(score)) for score, expected in boundaries]
    check("CVSS v3.1 severity boundaries", all(item["expected"] == item["actual"] for item in actual), actual)

    item = fixture("VULN", "HIGH")
    result = grade(item)
    check("missing CVSS explicitly falls back to detector severity", result["severity"] == "HIGH" and
          "CVSS_MISSING_FALLBACK" in result["grading"]["reason_codes"], result)
    result = grade(item, cvss_score=0, page_importance="CORE", exposure="INTERNET")
    check("CVSS zero remains information despite exposure", result["severity"] == "INFO", result)
    result = grade(item, cvss_score=4)
    check("provided CVSS takes priority over detector grade", result["severity"] == "MEDIUM", result)

    item = fixture()
    pages = [grade(item, page_importance=page) for page in ("HOME", "LOGIN", "CORE")]
    check("important page floors", all(result["severity"] == "HIGH" for result in pages), pages)
    result = grade(item, exposure="INTERNET")
    check("Internet exposure floor", result["severity"] == "HIGH", result)
    results = [grade(item, page_importance="NORMAL", exposure=exposure) for exposure in ("INTERNAL", "RESTRICTED")]
    check("ordinary internal and restricted findings keep base severity", all(result["severity"] == "MEDIUM" for result in results), results)
    result = grade(item, page_importance="LOGIN", exposure="INTERNET")
    check("impact factors take maximum without additive escalation", result["severity"] == "HIGH", result)
    result = grade(fixture(severity="CRITICAL"), exposure="INTERNET")
    check("existing critical severity never downgraded by floors", result["severity"] == "CRITICAL", result)
    result = grade(item)
    check("unknown context is explicit", result["severity"] == "MEDIUM" and
          {"PAGE_UNKNOWN", "EXPOSURE_UNKNOWN"} <= set(result["grading"]["reason_codes"]), result)

    data = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    data["rule_overrides"].append(dict(type="SENSITIVE", tool_name="fixture-scanner", rule_id="fixture-rule", severity="CRITICAL"))
    configured = GradingPolicy(data)
    rule_result = grade_finding(item, ctx(item, rule_id="fixture-rule"), configured)
    other_tool = dict(item, tool_name="different-scanner")
    other_result = grade_finding(other_tool, ctx(other_tool, rule_id="fixture-rule"), configured)
    check("rule override is configured and tool-isolated", rule_result["severity"] == "CRITICAL" and
          other_result["severity"] == "MEDIUM" and configured.sha256 != policy.sha256, [rule_result, other_result])

    bad = GradingContext(2, item["raw"]["identity"]["target_sha256"])
    rejected = False
    try:
        grade_finding(item, bad, policy)
    except GradingValidationError:
        rejected = True
    check("asset context mismatch rejected", rejected, dict(rejected=rejected))

    original = fixture(status="CONFIRMED")
    before = deepcopy(original)
    merged = deduplicate_findings([original, original])[0]
    values = graded_orm_values(merged, ctx(merged, page_importance="CORE"), policy)
    check("dedup then grade preserves state identity evidence and inputs", values["status"] == "CONFIRMED" and
          values["dedup_key"] == original["dedup_key"] and values["evidence"] == original["evidence"] and
          values["occurrence"] == 1 and original == before, dict(status=values["status"],
          dedup_key=values["dedup_key"], occurrence=values["occurrence"], grading=values["raw_data"]["grading"]))

    all_types = [grade(fixture(kind)) for kind in ("VULN", "WEAK_CONFIG", "MALICIOUS_LINK", "MALICIOUS_SCRIPT", "TAMPER", "SENSITIVE")]
    check("all risk categories have redacted evidence and useful fallback text", all(result["evidence"].startswith("[REDACTED]") and
          result["impact"] and "复测" in result["suggestion"] for result in all_types), all_types)

    dataset_path = ROOT / "tests/dataset/v1.0.0.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    scanner = SensitiveScanner(load_rules(ROOT / "src/tools/rules/sensitive-defaults.json"))
    file_map = {"D-01": "d/index.html", "D-02": "d/synthetic-env.txt", "D-03": "d/synthetic-backup.txt", "E-04": "e/index.html"}
    sensitive_cases = []
    for case in dataset["cases"]:
        if case["id"] not in file_map:
            continue
        origin = dataset["sites"][case["site"]]
        document = ScanDocument(origin.rstrip("/") + case["path"],
                                (ROOT / "tests/fixtures/sites" / file_map[case["id"]]).read_text(encoding="utf-8"))
        findings = scanner.scan(document, [AuthorizedScope(case["site"], origin)])
        grades = [grade(normalize(value, value["tool_name"]), rule_id=value["rule_id"], exposure="INTERNET") for value in findings]
        sensitive_cases.append(dict(case_id=case["id"], expected_positive=case["positive"], finding_count=len(findings),
                                    passed=bool(grades) == case["positive"], grades=grades))
    check("F v1.0.0 sensitive positives and negative pass through T20 T21 T23", len(sensitive_cases) == 4 and
          all(case["passed"] for case in sensitive_cases), sensitive_cases)

    url = "http://fixture.invalid/"
    page = Page(url=url, normalized_url=url, title="synthetic", content_hash="b" * 64, depth=0)
    tamper = detect_tamper([page], {url: Baseline(url, "a" * 64)})
    grades = [grade(normalize(value, value.tool_name), page_importance="HOME") for value in tamper]
    check("T18 dataclass output integrates through T21 into T23", len(grades) == 1 and grades[0]["severity"] == "HIGH", grades)
    script = scan_page('<script src="http://c2.example.invalid/load.js"></script>', url,
                       site_host="fixture.invalid", blacklist={"c2.example.invalid"})
    grades = [grade(normalize(value, value.tool_name), rule_id=value.rule_id, page_importance="HOME", exposure="INTERNET") for value in script]
    check("T19 malicious script uses configured rule grading", bool(grades) and all(result["grading"]["base_source"] == "RULE_OVERRIDE" and
          result["severity"] == "HIGH" for result in grades), grades)

    encoded = json.dumps(checks, ensure_ascii=False)
    check("export excludes synthetic raw secret and URL path", "fixture-only-token" not in encoded and "/private" not in encoded,
          dict(raw_secret_absent="fixture-only-token" not in encoded, raw_path_absent="/private" not in encoded))
    return dict(task="T23", fixture_version="t23-offline-v1", fixed_time="2026-10-15 09:00:00",
                mode="offline pure grading and local detector fixtures; no network or production database",
                policy_version=policy.version, policy_sha256=policy.sha256,
                dataset_version=dataset["version"], dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                checks=checks, passed_checks=sum(check["passed"] for check in checks), total_checks=len(checks),
                passed=all(check["passed"] for check in checks),
                limitations="No CVSS vector calculation, live Hengnao generation, production persistence, or online pipeline integration was performed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"T23 {'PASS' if report['passed'] else 'FAIL'}: {report['passed_checks']}/{report['total_checks']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
