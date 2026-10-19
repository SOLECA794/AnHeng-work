"""Session factory; SQLite DDL adaptation is restricted to local development."""
from pathlib import Path

from sqlalchemy import BigInteger, Integer, JSON, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models import Base


def create_database(url: str, *, development: bool = False):
    parsed = make_url(url)
    sqlite = parsed.get_backend_name() == "sqlite"
    if sqlite and not development:
        raise ValueError("SQLite is only enabled for development; configure PostgreSQL")
    if sqlite and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    options = {"connect_args": {"check_same_thread": False}} if sqlite else {}
    if sqlite and parsed.database in (None, "", ":memory:"):
        options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if sqlite:
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            table.to_metadata(metadata)
        for table in metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.primary_key and isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
