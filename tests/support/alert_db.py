"""Fixed T24 records in the private SQLite fixture; no configured DB is touched."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/support"))

from src.models import Finding
from src.repositories.alerts import SqlAlchemyAlertRepository
from src.services.finding_verification import Principal
from verification_db import verification_database

AT = "2026-10-16 09:00:00"
LATER = "2026-10-16 10:00:00"
LATEST = "2026-10-16 11:00:00"
ACTOR = Principal(1, "SEC_OPS", frozenset({1, 2}))


@contextmanager
def alert_database(*, sqlite_file=None):
    with verification_database(sqlite_file=sqlite_file) as (_, sessions):
        with sessions.begin() as session:
            for identifier in (101, 102, 103):
                finding = session.get(Finding, identifier)
                finding.dedup_key = ("a" if identifier != 102 else "b") * 32
                finding.last_seen = finding.first_seen = finding.updated_at = datetime.fromisoformat(AT)
        yield SqlAlchemyAlertRepository(sessions), sessions


def change(sessions, identifier=101, **values):
    for name in ("last_seen", "updated_at"):
        if name in values and isinstance(values[name], str):
            values[name] = datetime.fromisoformat(values[name])
    with sessions.begin() as session:
        finding = session.get(Finding, identifier)
        for name, value in values.items():
            setattr(finding, name, value)
