"""T25 deterministic clocks and actual T20 fixture bindings on a private DB."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))

from src.models import Finding
from sqlalchemy.orm.attributes import flag_modified
from src.repositories.retesting import SqlAlchemyRetestRepository
from src.services.finding_verification import Principal
from src.services.findings import normalize_finding, to_orm_values
from src.services.retesting import RetestResult
from src.tools.sensitive import AuthorizedScope, ScanDocument, SensitiveScanner, load_rules
from src.tools.sensitive_retest import FreshDocument, SensitiveRetestRunner, SensitiveRetestTarget, rules_fingerprint
from alert_db import alert_database, change

BASE = "2026-10-16 09:00:00"
START = "2026-10-17 09:00:00"
OBSERVED = "2026-10-17 09:01:00"
END = "2026-10-17 09:02:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1, 2}))
KEY = "ac6ac280-2dd2-45c4-9fbc-27a23eccd42b"
OTHER_KEY = "56f1f90c-807c-4ee0-b331-4d7a23629ca2"
URL = "http://fixture.invalid/sensitive"


class Clock:
    def __init__(self, *values):
        self.values = list(values or (START, END))
        self.last = self.values[-1]

    def __call__(self):
        return self.values.pop(0) if self.values else self.last


class Runner:
    def __init__(self, outcome="CLEAN", observed=OBSERVED, callback=None):
        self.outcome, self.observed, self.callback, self.calls = outcome, observed, callback, 0

    def run(self, snapshot):
        self.calls += 1
        if self.callback:
            self.callback(snapshot)
        return RetestResult(self.outcome, self.observed, "c" * 64, "d" * 64, True)


@contextmanager
def retest_database(*, sqlite_file=None):
    with alert_database(sqlite_file=sqlite_file) as (_, sessions):
        # SQLAlchemy's onupdate=now() must not move a fixed historical fixture
        # beyond its October 17 detector clock when the suite runs on a later day.
        change(sessions, status="CONFIRMED", updated_at=BASE)
        yield SqlAlchemyRetestRepository(sessions), sessions


def sensitive_fixture(sessions, *, current_text="public safe content", url=URL, rule_id="fixture-token"):
    scanner = SensitiveScanner(load_rules(ROOT / "src/tools/rules/sensitive-defaults.json"))
    source = scanner.scan(ScanDocument(url, "DEMO_ACCESS_TOKEN=fixture-only-token"), [AuthorizedScope("1", "http://fixture.invalid")])
    source = next(value for value in source if value["rule_id"] == rule_id)
    standard = normalize_finding(source, task_id=1, scope_id=1, tool_name="sensitive-scanner", seen_at=BASE)
    with sessions.begin() as session:
        finding = session.get(Finding, 101)
        for key, value in to_orm_values(standard).items():
            setattr(finding, key, value)
        finding.status = "CONFIRMED"
        finding.updated_at = datetime.fromisoformat(BASE)
        flag_modified(finding, "updated_at")
    binding = SensitiveRetestTarget(1, standard["dedup_key"], url, rule_id, rules_fingerprint(scanner))
    return SensitiveRetestRunner(scanner, {101: binding}, lambda target: FreshDocument(ScanDocument(target.url, current_text), OBSERVED)), binding
