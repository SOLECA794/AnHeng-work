"""Reproduce T21's deterministic offline normalization and deduplication acceptance."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.findings import deduplicate_findings, normalize_finding, to_orm_values


EARLY = "2026-10-13 09:00:00"
LATE = "2026-10-13 10:00:00"
URL = "http://fixture.invalid/risk"
FEATURE = {"control": "synthetic-config-check", "parameter": "header"}


def make(*, task=11, scope=21, tool="nuclei", time=EARLY, url=URL,
         category="WEAK_CONFIG", feature=None, status="OPEN", raw=None):
    data = {"type": category, "severity": "MEDIUM", "url": url, "status": status,
            "title": "fixture-only-token", "evidence": "fixture-only-token",
            "impact": "fixture-only-token", "suggestion": "fixture-only-token",
            "raw": {"template-id": "synthetic-config-check"} if raw is None else raw}
    return normalize_finding(data, task_id=task, scope_id=scope, tool_name=tool,
                             seen_at=time, evidence_feature=FEATURE if feature is None else feature)


def evaluate() -> dict:
    scenarios = []

    def check(name, condition, records):
        scenarios.append({"name": name, "passed": bool(condition), "records": records})

    first, second = make(), make(task=12, time=LATE)
    merged = deduplicate_findings([first, second])
    check("same risk across two inspection rounds", len(merged) == 1 and
          merged[0]["occurrence"] == 2 and merged[0]["first_seen"] == EARLY and
          merged[0]["last_seen"] == LATE, merged)

    retry = deduplicate_findings([first, first, make(time=LATE)])
    check("same task retries are idempotent", len(retry) == 1 and retry[0]["occurrence"] == 1 and
          retry[0]["last_seen"] == LATE, retry)

    cross_tool = deduplicate_findings([first, make(tool="zap")])
    check("cross-tool shared evidence feature", len(cross_tool) == 1 and cross_tool[0]["occurrence"] == 1 and
          {item["tool_name"] for item in cross_tool[0]["raw"]["observations"]} == {"nuclei", "zap"}, cross_tool)

    equivalent = [make(url="HTTP://Fixture.INVALID:80/a/./%7Erisk?b=2&a=1#f"),
                  make(url="http://fixture.invalid/a/~risk?a=1&b=2")]
    equivalent_merged = deduplicate_findings(equivalent)
    check("canonical URL equivalence", len(equivalent_merged) == 1, equivalent_merged)

    distinct_risks = deduplicate_findings([first, make(feature={"control": "different-risk"}),
                                         make(category="VULN"), make(url=URL + "?id=1"), make(url=URL + "?id=2")])
    check("different features categories and query values remain distinct", len(distinct_risks) == 5, distinct_risks)

    scopes = deduplicate_findings([first, make(scope=22)])
    check("scope separation", len(scopes) == 2 and scopes[0]["dedup_key"] == scopes[1]["dedup_key"], scopes)

    sensitive = [make(category="SENSITIVE", url="http://fixture.invalid/%5BREDACTED%5D",
                      raw={"rule_id": "token", "target_sha256": digest * 64,
                           "location": {"source": "body", "start": 0, "end": 18}})
                 for digest in ("a", "b")]
    opaque = deduplicate_findings(sensitive)
    check("T20 opaque target identities prevent redacted URL collisions", len(opaque) == 2, opaque)

    history = [make(status="CONFIRMED")]
    incoming = [make(task=12, time=LATE)]
    snapshots = deepcopy((history, incoming))
    preserved = deduplicate_findings(incoming, existing=history)
    check("preserve existing confirmed status and immutable inputs", len(preserved) == 1 and
          preserved[0]["status"] == "CONFIRMED" and preserved[0]["occurrence"] == 2 and
          (history, incoming) == snapshots, preserved)

    orm = to_orm_values(first)
    check("SDD-02 ORM field names and datetime conversion", orm["finding_type"] == "WEAK_CONFIG" and
          "type" not in orm and "raw_data" in orm and "raw" not in orm and
          orm["first_seen"].strftime("%Y-%m-%d %H:%M:%S") == EARLY, [first])

    all_records = [item for scenario in scenarios for item in scenario["records"]]
    safe = "fixture-only-token" not in json.dumps(all_records, ensure_ascii=False)
    check("untrusted free text is absent from exported records", safe, [])
    return {
        "task": "T21", "fixture_version": "t21-synthetic-v1", "mode": "offline pure functions; no database/network",
        "clock": "explicit fixed Asia/Shanghai timestamps; no runtime clock",
        "deduplication_unit": "scope_id plus dedup_key; occurrence counts distinct inspection task IDs",
        "scenarios": scenarios,
        "passed_checks": sum(item["passed"] for item in scenarios), "total_checks": len(scenarios),
        "passed": all(item["passed"] for item in scenarios),
        "limitations": "Pure transformation acceptance only; no database persistence, live tool execution or T22 status transitions.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write a deterministic redacted JSON acceptance report")
    arguments = parser.parse_args()
    try:
        report = evaluate()
    except Exception as error:
        print(f"T21 acceptance could not run: {type(error).__name__}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized, encoding="utf-8")
        verdict = "PASS" if report["passed"] else "FAIL"
        print(f"T21 {verdict}: {report['passed_checks']}/{report['total_checks']} checks passed")
        print(f"Redacted report: {arguments.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
