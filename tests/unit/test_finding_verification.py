"""T22 transition, authorization and evidence-review behavior without a database."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.finding_verification import (
    FindingSnapshot, Principal, RuleReview, VerificationError, VerificationService,
)


AT = "2026-10-14 09:00:00"
SOURCE = "a" * 64
SEC_OPS = Principal(1, "SEC_OPS", frozenset({1}))


class MemoryTransaction:
    def __init__(self, repository):
        self.repository = repository
        self.findings = deepcopy(repository.findings)
        self.audits = deepcopy(repository.audits)

    def get_finding(self, finding_id):
        return self.findings.get(finding_id)

    def compare_and_set(self, finding_id, scope_id, expected_status, new_status, at, *, source_sha256=None):
        if self.repository.conflict:
            return False
        current = self.findings[finding_id]
        if current.scope_id != scope_id or current.status != expected_status:
            return False
        if source_sha256 is not None and current.source_sha256 != source_sha256:
            return False
        self.findings[finding_id] = replace(current, status=new_status)
        return True

    def add_audit(self, event):
        if self.repository.fail_audit:
            raise RuntimeError("fixture-only-token")
        self.audits.append(deepcopy(event))

    def list_audits(self, finding_id, limit):
        return [deepcopy(item) for item in self.audits if item["finding_id"] == finding_id][-limit:]


class MemoryRepository:
    def __init__(self):
        self.findings = {
            101: FindingSnapshot(101, 1, 1, "OPEN", True, SOURCE),
            102: FindingSnapshot(102, 1, 1, "OPEN", True, SOURCE),
        }
        self.audits = []
        self.fail_audit = False
        self.conflict = False

    @contextmanager
    def transaction(self):
        transaction = MemoryTransaction(self)
        yield transaction
        self.findings, self.audits = transaction.findings, transaction.audits


class FindingVerificationTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.service = VerificationService(self.repository)

    def verify(self, finding_id=101, status="CONFIRMED", reason="EVIDENCE_REVIEWED", principal=SEC_OPS, **options):
        return self.service.verify(finding_id, status, reason, principal, observed_at=AT, **options)

    def assert_denied(self, code, operation):
        with self.assertRaises(VerificationError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.repository.findings[101].status, "OPEN")
        self.assertTrue(self.repository.audits, "denied operations must leave a blocked audit")
        self.assertEqual(self.repository.audits[-1]["result"], "BLOCKED")
        self.assertEqual(self.repository.audits[-1]["error_code"], code)

    def test_manual_confirmation_and_false_positive_reason_mapping(self):
        confirmed = self.verify()
        false_positive = self.verify(102, "FALSE_POSITIVE", "EXPECTED_PUBLIC_CONTENT")
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(false_positive["status"], "FALSE_POSITIVE")
        self.assertEqual(self.repository.findings[101].status, "CONFIRMED")
        self.assertEqual(self.repository.findings[102].status, "FALSE_POSITIVE")
        event = confirmed["verification"]
        self.assertEqual(event["from_status"], "OPEN")
        self.assertEqual(event["to_status"], "CONFIRMED")
        self.assertEqual(event["method"], "MANUAL")
        self.assertEqual(event["actor_id"], 1)
        self.assertEqual(event["at"], AT)
        self.assertEqual(event["result"], "SUCCESS")

    def test_note_content_is_hashed_and_absent_from_audit(self):
        note = "fixture-only-token fixture@example.invalid"
        result = self.verify(note=note)
        self.assertEqual(result["verification"]["note_sha256"], hashlib.sha256(note.encode()).hexdigest())
        serialized = json.dumps((result, self.repository.audits), ensure_ascii=False)
        self.assertNotIn("fixture-only-token", serialized)
        self.assertNotIn("fixture@example.invalid", serialized)

    def test_wrong_reason_for_transition_is_rejected(self):
        self.assert_denied(40001, lambda: self.verify(reason="AUTHORIZED_CHANGE"))

    def test_fixed_retested_and_open_are_not_t22_targets(self):
        for status in ("FIXED", "RETESTED", "OPEN"):
            with self.subTest(status=status):
                self.assert_denied(40001, lambda: self.verify(status=status))

    def test_repeat_verification_rejects_non_open_and_keeps_final_state(self):
        self.verify()
        with self.assertRaises(VerificationError) as raised:
            self.verify(status="FALSE_POSITIVE", reason="RULE_FALSE_MATCH")
        self.assertEqual(raised.exception.code, 40902)
        self.assertEqual(self.repository.findings[101].status, "CONFIRMED")
        self.assertEqual(self.repository.audits[-1]["result"], "BLOCKED")

    def test_only_security_operations_can_write(self):
        for role in ("RULE_ADMIN", "ADMIN", "VIEWER"):
            with self.subTest(role=role):
                self.assert_denied(40300, lambda: self.verify(principal=Principal(1, role, frozenset({1}))))

    def test_missing_inactive_and_malformed_principals_are_unauthenticated(self):
        for principal in (None, Principal(1, "SEC_OPS", frozenset({1}), active=False),
                          {"user_id": 1, "role": "SEC_OPS", "scope_ids": [1]}):
            with self.subTest(principal=principal):
                self.assert_denied(40100, lambda: self.verify(principal=principal))

    def test_scope_membership_and_enabled_asset_are_required(self):
        self.assert_denied(40310, lambda: self.verify(principal=Principal(1, "SEC_OPS", frozenset({2}))))
        self.repository.findings[101] = replace(self.repository.findings[101], scope_enabled=False)
        self.assert_denied(40310, lambda: self.verify())

    def test_missing_finding_returns_not_found(self):
        self.assert_denied(40400, lambda: self.verify(finding_id=999))

    def test_rule_match_and_false_match_have_distinct_audit_reasons(self):
        match = RuleReview("MATCH", SOURCE, SOURCE, "fixture-rules-v1")
        false_match = RuleReview("FALSE_MATCH", SOURCE, SOURCE, "fixture-rules-v1")
        one = self.service.review(101, match, SEC_OPS, observed_at=AT)
        two = self.service.review(102, false_match, SEC_OPS, observed_at=AT)
        self.assertEqual(one["status"], "CONFIRMED")
        self.assertEqual(one["verification"]["reason_code"], "RULE_REVIEW_MATCH")
        self.assertEqual(two["status"], "FALSE_POSITIVE")
        self.assertEqual(two["verification"]["reason_code"], "RULE_REVIEW_FALSE_MATCH")
        self.assertEqual(one["verification"]["method"], "RULE_REVIEW")

    def test_inconclusive_review_and_source_mismatch_keep_open(self):
        for review in (RuleReview("INCONCLUSIVE", SOURCE, SOURCE, "fixture-v1"),
                       RuleReview("MATCH", "d" * 64, "d" * 64, "fixture-v1"),
                       RuleReview("MATCH", SOURCE, "b" * 64, "fixture-v1")):
            with self.subTest(review=review):
                self.assert_denied(40903, lambda: self.service.review(101, review, SEC_OPS, observed_at=AT))

    def test_malformed_rule_review_is_rejected(self):
        review = RuleReview("MATCH", "not-a-hash", "b" * 64, "fixture-v1")
        self.assert_denied(40001, lambda: self.service.review(101, review, SEC_OPS, observed_at=AT))

    def test_malformed_review_outcome_container_is_audited_as_invalid_input(self):
        review = RuleReview([], SOURCE, SOURCE, "fixture-v1")
        self.assert_denied(40001, lambda: self.service.review(101, review, SEC_OPS, observed_at=AT))

    def test_invalid_unicode_note_is_rejected_with_blocked_audit(self):
        self.assert_denied(40001, lambda: self.verify(note="\ud800"))

    def test_compare_and_set_conflict_has_no_success_audit(self):
        self.repository.conflict = True
        self.assert_denied(40902, lambda: self.verify())
        self.assertTrue(all(event["result"] != "SUCCESS" for event in self.repository.audits))

    def test_audit_failure_rolls_back_state_and_sanitizes_error(self):
        self.repository.fail_audit = True
        with self.assertRaises(VerificationError) as raised:
            self.verify()
        self.assertEqual(raised.exception.code, 50000)
        self.assertNotIn("fixture-only-token", str(raised.exception))
        self.assertEqual(self.repository.findings[101].status, "OPEN")
        self.assertEqual(self.repository.audits, [])

    def test_rule_admin_can_read_but_admin_has_no_implicit_access(self):
        self.verify()
        trail = self.service.get(101, Principal(2, "RULE_ADMIN", frozenset({1})))
        self.assertEqual(trail["status"], "CONFIRMED")
        self.assertTrue(any(event["result"] == "SUCCESS" for event in trail["trail"]))
        with self.assertRaises(VerificationError) as raised:
            self.service.get(101, Principal(3, "ADMIN", frozenset({1})))
        self.assertEqual(raised.exception.code, 40300)

    def test_utc_observation_time_is_normalized_to_shanghai(self):
        result = self.service.verify(101, "CONFIRMED", "EVIDENCE_REVIEWED", SEC_OPS,
                                     observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
        self.assertEqual(result["verification"]["at"], AT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
