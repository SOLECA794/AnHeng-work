from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.finding_verification import VerificationError
from src.services.reporting import period, pagination
from src.services.report_files import TOKEN, canonical, digest, render_html


class ReportContractTests(unittest.TestCase):
    def test_inclusive_interval(self):
        first, last, begin, end = period("2026-10-18", "2026-10-19")
        self.assertEqual(begin, datetime(2026, 10, 18))
        self.assertEqual(end, datetime(2026, 10, 20))

    def test_leap_year_366_days(self):
        self.assertEqual(period("2024-01-01", "2024-12-31")[1].day, 31)

    def test_invalid_intervals(self):
        for begin, end in [(None, "2026-10-19"), (True, False), ("2026-1-01", "2026-01-02"),
                           ("2026-02-29", "2026-03-01"), ("2026-10-20", "2026-10-19"),
                           ("2024-01-01", "2025-01-01"), ("9999-12-31", "9999-12-31"),
                           ("２０２６-01-01", "2026-01-02")]:
            with self.subTest(begin=begin), self.assertRaises(VerificationError) as error:
                period(begin, end)
            self.assertEqual(error.exception.code, 40001)

    def test_pagination_rejects_bool_string_and_oversize(self):
        for args in [(True, 20), (1, False), ("1", 20), (0, 20), (1, 101), (100001, 20)]:
            with self.subTest(args=args), self.assertRaises(VerificationError):
                pagination(*args)

    def test_canonical_snapshot_and_hash(self):
        self.assertEqual(canonical({"b": "中文", "a": 1}), canonical({"a": 1, "b": "中文"}))
        self.assertEqual(len(digest(b"test")), 64)

    def test_storage_token_rejects_path_traversal(self):
        for token in ["../report.pdf", "c:/report.pdf", "A" * 32 + "-" + "a" * 64, "a" * 32 + "-" + "b" * 63]:
            self.assertIsNone(TOKEN.fullmatch(token))

    def test_html_escape_is_complete(self):
        from unittest.mock import patch
        with patch("src.services.report_files.sections", return_value=[("<script>x</script>", ['<img src="x" onerror="x">'])]):
            content = render_html({}).decode()
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img", content)
        self.assertIn("&lt;img", content)


if __name__ == "__main__":
    unittest.main()
