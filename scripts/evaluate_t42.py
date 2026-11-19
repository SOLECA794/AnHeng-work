"""T42: score observed detector results against the versioned T40 case labels.

This program never scans a target. It only validates a run's case-level
observations and computes reproducible confusion-matrix counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "tests/dataset/v1.0.0.json"
GROUPS = {
    "漏洞与弱配置": ("VULN", "WEAK_CONFIG"),
    "外链与挂马": ("MALICIOUS_LINK", "MALICIOUS_SCRIPT"),
    "页面篡改": ("TAMPER",),
    "敏感信息": ("SENSITIVE",),
}
CATEGORIES = tuple(category for values in GROUPS.values() for category in values)
STATUSES = {"executed", "blocked", "not_run"}


class EvaluationError(ValueError):
    """Input cannot be scored without guessing or silently dropping cases."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"JSON 顶层必须是对象：{path}")
    return value


def load_dataset(path: Path) -> tuple[dict[str, Any], str]:
    dataset = _read_json(path)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvaluationError(f"无法读取样例文件：{path}: {exc}") from exc
    if not isinstance(dataset.get("version"), str) or not dataset["version"]:
        raise EvaluationError("样例集缺少 version")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationError("样例集缺少非空 cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError("样例项必须是对象")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise EvaluationError(f"样例 ID 缺失或重复：{case_id!r}")
        seen.add(case_id)
        if case.get("category") not in CATEGORIES:
            raise EvaluationError(f"{case_id} 的 category 不在 T42 口径内")
        if type(case.get("positive")) is not bool:
            raise EvaluationError(f"{case_id} 的 positive 必须是布尔值")
        if not isinstance(case.get("site"), str) or not case["site"]:
            raise EvaluationError(f"{case_id} 缺少 site")
    return dataset, digest


def input_template(dataset: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "dataset_version": dataset["version"],
        "dataset_sha256": digest,
        "run_id": "",
        "code_commit": "",
        "scan_config": {},
        "results": [
            {
                "case_id": case["id"],
                "status": "not_run",
                "detected": None,
                "finding_ids": [],
                "evidence": [],
                "reason": "",
            }
            for case in dataset["cases"]
        ],
    }


def validate_input(
    dataset: dict[str, Any], digest: str, run: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if run.get("schema_version") != "1":
        raise EvaluationError("schema_version 必须为 '1'")
    if run.get("dataset_version") != dataset["version"]:
        raise EvaluationError("样例集版本不匹配")
    if run.get("dataset_sha256") != digest:
        raise EvaluationError("样例文件 SHA-256 不匹配；不能只凭版本号混用结果")
    for key in ("run_id", "code_commit"):
        if not isinstance(run.get(key), str) or not run[key].strip():
            raise EvaluationError(f"{key} 必须填写")
    if not isinstance(run.get("scan_config"), dict) or not run["scan_config"]:
        raise EvaluationError("scan_config 必须记录非空扫描配置")

    results = run.get("results")
    if not isinstance(results, list):
        raise EvaluationError("results 必须是数组")
    expected_ids = {case["id"] for case in dataset["cases"]}
    by_id: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise EvaluationError("结果项必须是对象")
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or case_id in by_id:
            raise EvaluationError(f"结果 case_id 缺失或重复：{case_id!r}")
        by_id[case_id] = result
        status = result.get("status")
        if status not in STATUSES:
            raise EvaluationError(f"{case_id} 的 status 必须是 executed/blocked/not_run")
        detected = result.get("detected")
        if status == "executed" and type(detected) is not bool:
            raise EvaluationError(f"{case_id} 已执行，detected 必须是布尔值")
        if status != "executed" and detected is not None:
            raise EvaluationError(f"{case_id} 未执行，detected 必须为 null")
        for field in ("finding_ids", "evidence"):
            values = result.get(field)
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise EvaluationError(f"{case_id} 的 {field} 必须是字符串数组")
        if status == "executed" and not result["evidence"]:
            raise EvaluationError(f"{case_id} 已执行，须提供结果证据路径或可追踪 ID")
        if status == "executed" and detected is False and result["finding_ids"]:
            raise EvaluationError(f"{case_id} 未命中指定类别，finding_ids 应为空")
        if status == "blocked" and not str(result.get("reason", "")).strip():
            raise EvaluationError(f"{case_id} 阻塞，须写明原因")
    missing = expected_ids - by_id.keys()
    extra = by_id.keys() - expected_ids
    if missing or extra:
        raise EvaluationError(f"样例 ID 不完整；缺少 {sorted(missing)}，多出 {sorted(extra)}")
    return by_id


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _counts(cases: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "blocked": 0, "not_run": 0}
    for case in cases:
        result = by_id[case["id"]]
        if result["status"] != "executed":
            counts[result["status"]] += 1
        elif case["positive"]:
            counts["tp" if result["detected"] else "fn"] += 1
        else:
            counts["fp" if result["detected"] else "tn"] += 1
    executed = sum(counts[key] for key in ("tp", "fn", "fp", "tn"))
    tp, fn, fp, tn = (counts[key] for key in ("tp", "fn", "fp", "tn"))
    return {
        **counts,
        "total": len(cases),
        "executed": executed,
        "coverage": _rate(executed, len(cases)),
        "status": "complete" if executed == len(cases) else "partial",
        "recall": _rate(tp, tp + fn),
        "precision": _rate(tp, tp + fp),
        "false_positive_rate": _rate(fp, fp + tn),
        "false_discovery_rate": _rate(fp, tp + fp),
    }


def evaluate(dataset: dict[str, Any], digest: str, run: dict[str, Any]) -> dict[str, Any]:
    by_id = validate_input(dataset, digest, run)
    cases = dataset["cases"]
    categories = {
        category: _counts([case for case in cases if case["category"] == category], by_id)
        for category in CATEGORIES
    }
    groups = {
        name: _counts([case for case in cases if case["category"] in members], by_id)
        for name, members in GROUPS.items()
    }
    overall = _counts(cases, by_id)
    clean_site = _counts([case for case in cases if case["site"] == "E"], by_id)
    warnings = []
    if overall["status"] != "complete":
        warnings.append("样例未全部执行；指标仅为已执行样例的部分观察值，不得作最终达标结论。")
    for category in CATEGORIES:
        positives = [case for case in cases if case["category"] == category and case["positive"]]
        if not any(by_id[case["id"]]["status"] == "executed" and by_id[case["id"]]["detected"] for case in positives):
            warnings.append(f"{category} 无已命中的正对照；该类负例的零命中不能单独证明检测器有效。")
    warnings.append("E 靶标当前没有 MALICIOUS_SCRIPT、TAMPER 负例；E 的误报结果不覆盖这两类。")
    return {
        "schema_version": "1",
        "run_id": run["run_id"],
        "code_commit": run["code_commit"],
        "scan_config": run["scan_config"],
        "dataset_version": dataset["version"],
        "dataset_sha256": digest,
        "overall": overall,
        "groups": groups,
        "categories": categories,
        "clean_site_E": clean_site,
        "cases": [
            {
                "case_id": case["id"],
                "site": case["site"],
                "category": case["category"],
                "positive": case["positive"],
                **by_id[case["id"]],
            }
            for case in cases
        ],
        "warnings": warnings,
    }


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def _row(name: str, value: dict[str, Any]) -> str:
    return (
        f"| {name} | {value['executed']}/{value['total']} | "
        f"{value['tp']} | {value['fn']} | {value['fp']} | {value['tn']} | "
        f"{_percent(value['recall'])} | {_percent(value['precision'])} | "
        f"{_percent(value['false_positive_rate'])} | "
        f"{_percent(value['false_discovery_rate'])} | {value['status']} |"
    )


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# T42 检测效果评估报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 代码提交：`{report['code_commit']}`",
        f"- 样例集：`v{report['dataset_version']}`；SHA-256：`{report['dataset_sha256']}`",
        f"- 扫描配置：`{json.dumps(report['scan_config'], ensure_ascii=False, sort_keys=True)}`",
        f"- 覆盖状态：**{report['overall']['status']}**（{report['overall']['executed']}/{report['overall']['total']}）",
        "",
        "计数单位为样例 ID 与类别的组合；同一单位的重复 Finding 只计一次。未执行、阻塞不计为 FN/TN。",
        "召回率 = TP/(TP+FN)；准确率 = TP/(TP+FP)；主误报率 FPR = FP/(FP+TN)；告警中误报占比 FDR = FP/(TP+FP)。分母为零记 N/A。",
        "",
        "## 四类汇总",
        "",
        "| 类别组 | 已执行/总数 | TP | FN | FP | TN | 召回率 | 准确率 | FPR | FDR | 状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(_row(name, value) for name, value in report["groups"].items())
    lines.extend(
        [
            "",
            "## 原始类别与正常站点",
            "",
            "| 类别 | 已执行/总数 | TP | FN | FP | TN | 召回率 | 准确率 | FPR | FDR | 状态 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(_row(name, value) for name, value in report["categories"].items())
    lines.append(_row("E 靶标负例", report["clean_site_E"]))
    lines.extend(
        [
            "",
            "## 逐例证据",
            "",
            "| 样例 | 类别 | 真值 | 状态 | 命中 | Finding ID | 证据 | 阻塞原因 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in report["cases"]:
        fields = (
            case["case_id"], case["category"], "正" if case["positive"] else "负",
            case["status"], "—" if case["detected"] is None else ("是" if case["detected"] else "否"),
            ", ".join(case["finding_ids"]) or "—",
            ", ".join(case["evidence"]) or "—",
            str(case.get("reason") or "—"),
        )
        lines.append("| " + " | ".join(str(field).replace("|", "\\|").replace("\n", " ") for field in fields) + " |")
    lines.extend(["", "## 限制与复核", ""])
    lines.extend(f"- {warning}" for warning in report["warnings"])
    lines.extend(["- 本工具只计算指标，不设自动通过阈值；最终结论需结合原始证据人工复核。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init-input", type=Path, help="生成待填写的逐例输入 JSON，不覆盖已有文件")
    mode.add_argument("--input", type=Path, help="已有的一轮逐例执行结果 JSON")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()
    try:
        dataset, digest = load_dataset(args.dataset)
        if args.init_input:
            if args.json_out or args.markdown_out:
                parser.error("--init-input 不能同时指定报告输出")
            with args.init_input.open("x", encoding="utf-8") as handle:
                json.dump(input_template(dataset, digest), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"已生成 {len(dataset['cases'])} 条待填写样例：{args.init_input}")
            return 0
        if not args.json_out or not args.markdown_out:
            parser.error("--input 必须同时指定 --json-out 和 --markdown-out")
        report = evaluate(dataset, digest, _read_json(args.input))
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.markdown_out.write_text(markdown_report(report), encoding="utf-8")
        print(f"已复算 {report['overall']['executed']}/{report['overall']['total']} 条；报告：{args.markdown_out}")
        return 0
    except (EvaluationError, OSError) as exc:
        parser.exit(2, f"T42 输入错误：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
