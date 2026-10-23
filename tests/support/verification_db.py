"""SQLite-compatible T22 fixture; production PostgreSQL mappings stay untouched."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import BigInteger, Integer, JSON, MetaData, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models import Base
from src.models.entities import AuthScope, DetectionTask, Finding, SysUser


SOURCE_HASH = "a" * 64
FIXED_TIME = datetime(2026, 10, 14, 9, 0, 0)


@contextmanager
def verification_database(*, sqlite_file=None):
    """Yield (repository, session factory) backed by a fresh private in-memory DB.

    Only cloned test DDL substitutes JSONB and INTEGER primary keys. Connections
    use foreign-key enforcement; StaticPool shares the same private database
    with TestClient's worker thread. No configured application database is read.
    """
    from src.repositories.finding_verification import SqlAlchemyVerificationRepository

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.primary_key and isinstance(column.type, BigInteger):
                column.type = Integer()
    url = URL.create("sqlite+pysqlite", database=str(sqlite_file) if sqlite_file is not None else ":memory:")
    options = {} if sqlite_file is not None else {"poolclass": StaticPool}
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10}, **options)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([
            SysUser(id=1, username="fixture-secops", password="fixture-not-a-password",
                    role="SEC_OPS", status=1, deleted=0),
            SysUser(id=2, username="fixture-rule-admin", password="fixture-not-a-password",
                    role="RULE_ADMIN", status=1, deleted=0),
            SysUser(id=3, username="fixture-admin", password="fixture-not-a-password",
                    role="ADMIN", status=1, deleted=0),
            AuthScope(id=1, domain="fixture.invalid", scope_type="WHOLE", status="ENABLED", deleted=0),
            AuthScope(id=2, domain="second.invalid", scope_type="WHOLE", status="ENABLED", deleted=0),
        ])
        session.flush()
        session.add_all([
            DetectionTask(id=1, scope_id=1, trigger_type="MANUAL", status="DONE"),
            DetectionTask(id=2, scope_id=2, trigger_type="MANUAL", status="DONE"),
        ])
        session.flush()
        session.add_all([
            Finding(id=identifier, task_id=1 if scope == 1 else 2, scope_id=scope,
                    finding_type="SENSITIVE", severity="MEDIUM", title="Synthetic sensitive marker",
                    url="http://fixture.invalid/?token=fixture-only-token", evidence="fixture-only-token",
                    impact="synthetic impact", suggestion="synthetic suggestion", tool_name="fixture-detector",
                    raw_data={"source_sha256": SOURCE_HASH}, dedup_key=f"fixture-{identifier}",
                    status="OPEN", occurrence=1, first_seen=FIXED_TIME, last_seen=FIXED_TIME)
            for identifier, scope in ((101, 1), (102, 1), (103, 2))
        ])
    try:
        yield SqlAlchemyVerificationRepository(sessions), sessions
    finally:
        engine.dispose()
