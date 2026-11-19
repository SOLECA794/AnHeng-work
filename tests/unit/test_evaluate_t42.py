"""T42 scoring tests: denominators, partial runs, and input traceability."""

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_t42 import (  # noqa: E402
    EvaluationError,
    evaluate,
    input_template,
    load_dataset,
    markdown_report,
)


class EffectivenessEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset, cls.digest = load_dataset(ROOT / "tests/dataset/v1.0.0.json")

    def make_run(self):
        run = input_template(self.dataset, self.digest)
        run["run_id"] = "synthetic-unit-test"
        run["code_commit"] = "unit-test-commit"
        run["scan_config"] = {"mode": "synthetic scoring check"}
        return run

    @staticmethod
    def set_executed(result, detected):
        result["status"] = "executed"
        result["detected"] = detected
        result["evidence"] = ["tests/unit/test_evaluate_t42.py"]
        if detected:
            result["finding_ids"] = [f"finding-{result['case_id']}"]

    def test_template_pins_all_16_cases_and_dataset_hash(self):
        run = self.make_run()
        self.assertEqual(len(run["results"]), 16)
        self.assertEqual(len({item["case_id"] for item in run["results"]}), 16)
        self.assertEqual(run["dataset_sha256"], self.digest)
        report = evaluate(self.dataset, self.digest, run)
        self.assertEqual(report["overall"]["executed"], 0)
        self.assertEqual(report["overall"]["status"], "partial")
        self.assertIsNone(report["overall"]["recall"])
        self.assertIn("N/A", markdown_report(report))

    def test_confusion_matrix_counts_both_malware_categories_and_e_site(self):
        run = self.make_run()
        cases = {case["id"]: case for case in self.dataset["cases"]}
        flips = {"B-03", "B-04", "E-02", "D-02"}
        for result in run["results"]:
            case = cases[result["case_id"]]
            self.set_executed(result, case["positive"] ^ (case["id"] in flips))
            if result["case_id"] == "B-03":
                result["finding_ids"] = []
            if result["case_id"] == "B-01":
                result["finding_ids"].append("same-case-second-finding")
        report = evaluate(self.dataset, self.digest, run)
        self.assertEqual(report["overall"]["status"], "complete")
        self.assertEqual(
            tuple(report["overall"][key] for key in ("tp", "fn", "fp", "tn")),
            (6, 2, 2, 6),
        )
        malware = report["groups"]["外链与挂马"]
        self.assertEqual(tuple(malware[key] for key in ("tp", "fn", "fp", "tn")), (1, 1, 2, 1))
        self.assertEqual(malware["recall"], 0.5)
        self.assertAlmostEqual(malware["precision"], 1 / 3)
        self.assertAlmostEqual(malware["false_positive_rate"], 2 / 3)
        self.assertAlmostEqual(malware["false_discovery_rate"], 2 / 3)
        self.assertEqual(report["clean_site_E"]["fp"], 1)
        self.assertEqual(report["clean_site_E"]["tn"], 3)
        self.assertEqual(report["categories"]["MALICIOUS_SCRIPT"]["fn"], 1)
        self.assertEqual(report["categories"]["MALICIOUS_SCRIPT"]["fp"], 1)

    def test_blocked_positive_is_not_false_negative(self):
        run = self.make_run()
        by_id = {item["case_id"]: item for item in run["results"]}
        self.set_executed(by_id["B-01"], True)
        self.set_executed(by_id["B-02"], False)
        by_id["B-03"]["status"] = "blocked"
        by_id["B-03"]["reason"] = "检测器未部署"
        report = evaluate(self.dataset, self.digest, run)
        malware = report["groups"]["外链与挂马"]
        self.assertEqual((malware["tp"], malware["fn"], malware["tn"]), (1, 0, 1))
        self.assertEqual((malware["executed"], malware["blocked"], malware["not_run"]), (2, 1, 2))
        self.assertEqual(malware["status"], "partial")

    def test_rejects_changed_dataset_and_missing_evidence(self):
        run = self.make_run()
        wrong_hash = copy.deepcopy(run)
        wrong_hash["dataset_sha256"] = "0" * 64
        with self.assertRaisesRegex(EvaluationError, "SHA-256"):
            evaluate(self.dataset, self.digest, wrong_hash)
        self.set_executed(run["results"][0], True)
        run["results"][0]["evidence"] = []
        with self.assertRaisesRegex(EvaluationError, "证据"):
            evaluate(self.dataset, self.digest, run)

    def test_rejects_negative_with_matching_finding_ids(self):
        run = self.make_run()
        self.set_executed(run["results"][0], False)
        run["results"][0]["finding_ids"] = ["contradictory-finding"]
        with self.assertRaisesRegex(EvaluationError, "finding_ids 应为空"):
            evaluate(self.dataset, self.digest, run)

    def test_rejects_duplicate_or_missing_case_ids(self):
        run = self.make_run()
        run["results"][1]["case_id"] = run["results"][0]["case_id"]
        with self.assertRaisesRegex(EvaluationError, "重复"):
            evaluate(self.dataset, self.digest, run)
        run = self.make_run()
        run["results"].pop()
        with self.assertRaisesRegex(EvaluationError, "缺少"):
            evaluate(self.dataset, self.digest, run)

    def test_negative_result_without_positive_control_is_flagged(self):
        run = self.make_run()
        by_id = {item["case_id"]: item for item in run["results"]}
        self.set_executed(by_id["B-04"], False)
        report = evaluate(self.dataset, self.digest, run)
        self.assertTrue(any("MALICIOUS_SCRIPT 无已命中的正对照" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
