"""Reproduce T20 acceptance against F's four versioned sensitive-information cases.

This runner reads fixed local fixtures and preserves their labeled virtual URLs.
It never starts a server or requests the URLs.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner, load_rules


DATASET_PATH = ROOT / "tests/dataset/v1.0.0.json"
RULES_PATH = ROOT / "src/tools/rules/sensitive-defaults.json"
FIXTURE_FILES = {
    "D-01": "tests/fixtures/sites/d/index.html",
    "D-02": "tests/fixtures/sites/d/synthetic-env.txt",
    "D-03": "tests/fixtures/sites/d/synthetic-backup.txt",
    "E-04": "tests/fixtures/sites/e/index.html",
}
EXPECTED_LABELS = {"D-01": True, "D-02": True, "D-03": True, "E-04": False}
REQUIRED_RULE_TYPES = {"KEYWORD", "REGEX", "FILETYPE", "SAMPLE"}


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate() -> dict:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if dataset.get("version") != "1.0.0":
        raise ValueError("unexpected dataset version")
    cases = {}
    for case in dataset["cases"]:
        if case["id"] in FIXTURE_FILES:
            if case["id"] in cases:
                raise ValueError("duplicate T20 case label")
            cases[case["id"]] = case
    if set(cases) != set(FIXTURE_FILES):
        raise ValueError("T20 requires D-01, D-02, D-03 and E-04")
    scanner = SensitiveScanner(load_rules(RULES_PATH))
    results = []
    confusion = {"TP": 0, "FN": 0, "TN": 0, "FP": 0}
    all_rule_types = set()
    for case_id, source in FIXTURE_FILES.items():
        case = cases[case_id]
        if case["category"] != "SENSITIVE" or case["positive"] is not EXPECTED_LABELS[case_id]:
            raise ValueError("T20 labels disagree with the versioned acceptance baseline")
        origin = dataset["sites"][case["site"]]
        url = urljoin(origin.rstrip("/") + "/", case["path"])
        content_bytes = (ROOT / source).read_bytes()
        document = ScanDocument(url, content_bytes.decode("utf-8"), {})
        result = {
            "case_id": case_id,
            "source_file": source,
            "source_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "labeled_url": url,
            "expected_positive": case["positive"],
        }
        try:
            findings = scanner.scan(document, [AuthorizedScope(case["site"], origin)])
        except Exception as error:
            # Only the exception class is recorded: error strings can contain raw input.
            result.update({"detected": None, "passed": False, "error_type": type(error).__name__,
                           "rule_types": [], "findings": []})
        else:
            detected = bool(findings)
            rule_types = sorted({finding["rule_type"] for finding in findings})
            all_rule_types.update(rule_types)
            result.update({"detected": detected, "passed": detected == case["positive"],
                           "rule_types": rule_types, "findings": findings})
            outcome = ("TP" if detected else "FN") if case["positive"] else ("FP" if detected else "TN")
            confusion[outcome] += 1
        results.append(result)
    tp, fn, tn, fp = (confusion[key] for key in ("TP", "FN", "TN", "FP"))
    evaluated = tp + fn + tn + fp
    coverage_passed = REQUIRED_RULE_TYPES <= all_rule_types
    return {
        "task": "T20",
        "mode": "offline; fixed local fixtures; no network access",
        "dataset_version": dataset["version"],
        "dataset_path": DATASET_PATH.relative_to(ROOT).as_posix(),
        "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        "rules_path": RULES_PATH.relative_to(ROOT).as_posix(),
        "rules_sha256": hashlib.sha256(RULES_PATH.read_bytes()).hexdigest(),
        "regex_version": version("regex"),
        "configuration": {"regex_timeout_seconds": 0.05, "max_content_chars": 1_000_000,
                          "max_findings": 1000},
        "metric_unit": "labeled URL case; multiple findings count once; errors excluded from metrics",
        "cases": results,
        "rule_types": sorted(all_rule_types),
        "four_rule_types_covered": coverage_passed,
        "confusion_matrix": confusion,
        "metrics": {"recall": ratio(tp, tp + fn), "precision": ratio(tp, tp + fp),
                    "accuracy": ratio(tp + tn, evaluated), "false_positive_rate": ratio(fp, fp + tn)},
        "evaluated_cases": evaluated,
        "error_cases": len(results) - evaluated,
        "passed": coverage_passed and all(result["passed"] for result in results),
        "limitations": "Three synthetic positive URLs and one negative URL; this is fixture acceptance, "
                       "not a production accuracy estimate or an HTTP crawling test.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the complete redacted JSON report to this file")
    arguments = parser.parse_args()
    try:
        report = evaluate()
    except Exception as error:
        print(f"T20 acceptance could not run: {type(error).__name__}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized, encoding="utf-8")
        verdict = "PASS" if report["passed"] else "FAIL"
        counts = report["confusion_matrix"]
        print(f"T20 {verdict}: dataset={report['dataset_version']}, "
              f"TP={counts['TP']} FN={counts['FN']} TN={counts['TN']} FP={counts['FP']}, "
              f"errors={report['error_cases']}, rule_types={','.join(report['rule_types'])}")
        print(f"Redacted report: {arguments.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
