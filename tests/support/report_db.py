"""Private, deterministic T26 report fixture; never uses configured application DB."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import tempfile

from src.models.entities import DetectionTask, Finding, SitePage
from src.repositories.reporting import SqlAlchemyReportRepository
from src.services.report_files import ReportStorage
from src.services.reporting import ReportService
from src.services.finding_verification import Principal
from verification_db import verification_database

ACTOR = Principal(1, "SEC_OPS", frozenset({1}))
AT = "2026-10-19 23:59:59"
BEGIN, END = "2026-10-18", "2026-10-19"


@contextmanager
def report_database():
    cache = Path(__file__).resolve().parents[1] / ".cache"
    cache.mkdir(exist_ok=True)
    with verification_database() as (_, sessions), tempfile.TemporaryDirectory(dir=cache, prefix="t26-") as folder:
        with sessions.begin() as session:
            # Task creation columns are naive UTC; interval requests are Shanghai dates.
            session.get(DetectionTask, 1).created_at = datetime(2026, 10, 17, 16)
            session.get(DetectionTask, 2).created_at = datetime(2026, 10, 19)
            session.add_all([
                DetectionTask(id=3, scope_id=1, trigger_type="MANUAL", status="DONE", created_at=datetime(2026, 10, 19, 15, 59, 59)),
                DetectionTask(id=4, scope_id=1, trigger_type="MANUAL", status="QUEUED", created_at=datetime(2026, 10, 19, 16)),
                SitePage(id=1, scope_id=1, url="https://fixture.invalid/private?token=fixture-only-token",
                         normalized_url="https://fixture.invalid/", depth=0, status="ACTIVE"),
            ])
            session.flush()
            first, second = session.get(Finding, 101), session.get(Finding, 102)
            first.dedup_key, second.dedup_key = "risk-a", "risk-b"
            first.first_seen = first.last_seen = datetime(2026, 10, 18)
            second.first_seen = second.last_seen = datetime(2026, 10, 18)
            second.status, second.severity = "FALSE_POSITIVE", "LOW"
            session.add_all([
                Finding(id=identifier, task_id=task, scope_id=1, finding_type="SENSITIVE", severity="HIGH",
                        title="<script>fixture-only-token</script>", evidence="fixture-only-token",
                        impact="fixture-only-token", suggestion="fixture-only-token", tool_name="fixture-detector",
                        raw_data={"source_sha256": "b" * 64, "secret": "fixture-only-token"},
                        dedup_key=key, status="CONFIRMED", occurrence=1,
                        first_seen=datetime(2026, 10, 19), last_seen=datetime(2026, 10, 19))
                for identifier, task, key in ((104, 3, "risk-a"), (105, 4, "future"))
            ])
        repository = SqlAlchemyReportRepository(sessions)
        storage = ReportStorage(folder)
        yield ReportService(repository, storage), sessions, storage
