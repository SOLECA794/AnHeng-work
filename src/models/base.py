"""SQLAlchemy base and common columns for the inspection service."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


Id = Annotated[int, mapped_column(BigInteger, primary_key=True, autoincrement=True)]


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
