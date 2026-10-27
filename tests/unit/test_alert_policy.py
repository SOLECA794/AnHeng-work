"""T24 alert eligibility and strict external policy configuration."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.alerts import AlertError, AlertPolicy, DEFAULT_POLICY, load_alert_policy


class AlertPolicyTests(unittest.TestCase):
    def test_default_high_threshold_and_type_exceptions(self):
        policy = load_alert_policy()
        for kind, severity, expected in (("VULN", "HIGH", True), ("VULN", "CRITICAL", True),
                                         ("WEAK_CONFIG", "MEDIUM", False), ("TAMPER", "LOW", True),
                                         ("SENSITIVE", "LOW", True), ("SENSITIVE", "INFO", False)):
            self.assertEqual(policy.matches(kind, severity), expected)

    def test_disabled_policy_never_sends(self):
        self.assertFalse(AlertPolicy(enabled=False).matches("VULN", "CRITICAL"))

    def test_custom_threshold_is_effective(self):
        policy = AlertPolicy(min_severity="MEDIUM", always_types=frozenset())
        self.assertTrue(policy.matches("WEAK_CONFIG", "MEDIUM"))
        self.assertFalse(policy.matches("SENSITIVE", "LOW"))

    def test_info_never_triggers_even_for_forced_type(self):
        self.assertFalse(AlertPolicy().matches("TAMPER", "INFO"))

    def test_invalid_config_types_and_enums_rejected(self):
        for options in (dict(enabled=1), dict(min_severity="INFO"), dict(min_severity=[]),
                        dict(always_types=[]), dict(always_types=frozenset({"FINGERPRINT"})), dict(version="bad\n")):
            with self.assertRaises(AlertError):
                AlertPolicy(**options)

    def test_invalid_detection_category_is_not_silently_eligible(self):
        for kind, level in (("FINGERPRINT", "INFO"), ("VULN", "bad"), ([], "HIGH")):
            with self.assertRaises(AlertError):
                AlertPolicy().matches(kind, level)

    def test_digest_tracks_all_configuration(self):
        policy = AlertPolicy()
        self.assertEqual(len(policy.sha256), 64)
        for other in (replace(policy, enabled=False), replace(policy, min_severity="CRITICAL"),
                      replace(policy, always_types=frozenset()), replace(policy, version="v2")):
            self.assertNotEqual(policy.sha256, other.sha256)

    def test_file_configuration_and_no_input_echo(self):
        # Use the workspace cache so the fixture also works in a restricted Windows sandbox.
        cache = ROOT / "tests/.cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            path = Path(directory) / "policy.json"
            data = DEFAULT_POLICY.read_text(encoding="utf-8")
            self.assertEqual(load_alert_policy().sha256, load_alert_policy(DEFAULT_POLICY).sha256)
            for text in ('{"version":"fixture-only-token"}', data.replace('"enabled": true', '"enabled": true, "enabled": false'),
                         data.replace('["TAMPER", "SENSITIVE"]', '["TAMPER", "TAMPER"]'), "fixture-only-token", " " * 8193):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(AlertError) as error:
                    load_alert_policy(path)
                self.assertNotIn("fixture-only-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
