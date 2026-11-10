"""T23 ORM values roundtrip in the existing isolated SQLite fixture."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src.models import Finding
from src.services.findings import deduplicate_findings, normalize_finding
from src.services.risk_grading import GradingContext, graded_orm_values, load_grading_policy
from support.verification_db import verification_database


class GradingPersistenceTests(unittest.TestCase):
    def values(self, status="OPEN"):
        item = normalize_finding(dict(type="SENSITIVE", severity="MEDIUM", status=status,
                                      url="http://fixture.invalid/private", rule_id="fixture-rule", evidence="synthetic"),
                                 task_id=1, scope_id=1, tool_name="fixture-scanner", seen_at="2026-10-15 09:00:00")
        item = deduplicate_findings([item])[0]
        ctx = GradingContext(1, item["raw"]["identity"]["target_sha256"], "CORE", "INTERNET")
        return graded_orm_values(item, ctx, load_grading_policy())

    def test_graded_values_roundtrip_with_provenance_and_templates(self):
        with verification_database() as (_, sessions):
            values = self.values()
            with sessions.begin() as session:
                finding = session.get(Finding, 101)
                for name, value in values.items():
                    setattr(finding, name, value)
            with sessions() as session:
                finding = session.get(Finding, 101)
                self.assertEqual(finding.severity, "HIGH")
                self.assertEqual(finding.raw_data["grading"]["base_severity"], "MEDIUM")
                self.assertEqual(finding.raw_data["grading"]["policy_sha256"], load_grading_policy().sha256)
                self.assertIn("核心路径", finding.impact)
                self.assertIn("复测", finding.suggestion)
                self.assertEqual(finding.evidence, values["evidence"])

    def test_grading_preserves_verified_status_and_deduplication_key(self):
        with verification_database() as (_, sessions):
            values = self.values("FALSE_POSITIVE")
            with sessions.begin() as session:
                finding = session.get(Finding, 101)
                for name, value in values.items():
                    setattr(finding, name, value)
            with sessions() as session:
                finding = session.get(Finding, 101)
                self.assertEqual(finding.status, "FALSE_POSITIVE")
                self.assertEqual(finding.dedup_key, values["dedup_key"])
                self.assertEqual(finding.first_seen, values["first_seen"])


if __name__ == "__main__":
    unittest.main()
