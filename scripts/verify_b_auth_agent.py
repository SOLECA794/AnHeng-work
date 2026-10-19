"""Repeatable T09/T10 acceptance, using private SQLite and recorded platform data."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "b-auth-agent-results.json")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for folder, pattern in (("unit", "test_auth_and_agent.py"), ("unit", "test_b_*.py"),
                            ("integration", "test_b_auth_api.py")):
        # Separate loaders avoid treating unrelated test directories as one package.
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests" / folder), pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "taskIds": ["T09", "T10"], "observedAt": datetime.now(timezone.utc).isoformat(),
        "testsRun": result.testsRun, "passed": result.wasSuccessful(),
        "failures": [str(test) for test, _ in result.failures],
        "errors": [str(test) for test, _ in result.errors],
        "boundaries": ["Private file SQLite, no production database access",
                       "httpx MockTransport and A's recorded data, no platform requests",
                       "T28-T30 end-to-end acceptance pending A's published agent ID/input contract",
                       "PostgreSQL runtime acceptance pending an available PostgreSQL service"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
