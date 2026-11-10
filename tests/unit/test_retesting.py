"""T25 request/result contracts and safe historical data validation."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))
from src.services.retesting import RetestError, RetestResult, RetestSnapshot, fingerprint, request_key, receipt, validate_receipt, validate_result
from retest_db import KEY, START, OBSERVED, END, BASE


SNAPSHOT = RetestSnapshot(101, 1, 1, "SENSITIVE", "sensitive-scanner", "a" * 32, "CONFIRMED", "b" * 64,
                         BASE, "fixture.invalid", "WHOLE", None, "c" * 64)
RESULT = RetestResult("CLEAN", OBSERVED, "d" * 64, "e" * 64, True)


class RetestContractTests(unittest.TestCase):
    def test_uuid_is_canonical_and_invalid_values_are_not_echoed(self):
        self.assertEqual(request_key(KEY), KEY)
        for key in (None, True, [], KEY.upper(), "fixture-only-token", "../request"):
            with self.assertRaises(RetestError) as error:
                request_key(key)
            self.assertNotIn("fixture-only-token", str(error.exception))

    def test_complete_fresh_observation_is_valid(self):
        validate_result(RESULT, SNAPSHOT, START, END)

    def test_non_success_incomplete_old_and_future_results_are_rejected(self):
        for result in (None, replace(RESULT, outcome="INCONCLUSIVE"), replace(RESULT, outcome=[]),
                       replace(RESULT, coverage_complete=False), replace(RESULT, observed_at=BASE),
                       replace(RESULT, observed_at="2099-01-01 00:00:00"), replace(RESULT, source_sha256="bad")):
            with self.assertRaises(RetestError):
                validate_result(result, SNAPSHOT, START, END)

    def test_snapshot_fingerprint_is_stable_and_changes_with_business_input(self):
        self.assertEqual(fingerprint({"a": 1, "b": 2}), fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(fingerprint({"a": 1}), fingerprint({"a": 2}))
        with self.assertRaises(RetestError):
            fingerprint({"data": float("nan")})

    def test_success_receipt_is_safe_and_complete(self):
        data = receipt(KEY, "CONFIRMED", "FIXED", "CLEAN", result=RESULT)
        validate_receipt(data)
        self.assertNotIn("url", data)

    def test_unknown_fields_and_impossible_state_relations_are_rejected(self):
        baseline = receipt(KEY, "CONFIRMED", "FIXED", "CLEAN", result=RESULT)
        for changes in ({"extra": "fixture-only-token"}, {"outcome": "MATCH"}, {"status": "CONFIRMED"},
                        {"error_code": True}, {"rules_sha256": "bad"}, {"alert_id": 0}):
            with self.assertRaises(RetestError):
                validate_receipt(dict(baseline, **changes))

    def test_clean_without_provenance_is_not_a_success(self):
        with self.assertRaises(RetestError):
            receipt(KEY, "CONFIRMED", "FIXED", "CLEAN")

    def test_error_receipt_preserves_prior_state(self):
        data = receipt(KEY, "CONFIRMED", "CONFIRMED", "INCONCLUSIVE", error=40903)
        self.assertEqual(data["status"], data["from_status"])
        self.assertEqual(data["error_code"], 40903)


if __name__ == "__main__":
    unittest.main()
