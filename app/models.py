"""ORM models for Nihon Loop: System, Cycle, Message."""
import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Float,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class System(Base):
    __tablename__ = "systems"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_standard: Mapped[str] = mapped_column(
        Text, nullable=False, default="1. No standard defined yet."
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    cycles: Mapped[list["Cycle"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    system_id: Mapped[str] = mapped_column(ForeignKey("systems.id"), nullable=False)

    # plan | do | check | act | closed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="plan")

    job: Mapped[str] = mapped_column(Text, nullable=False)
    standard_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    tool_budget: Mapped[int] = mapped_column(Integer, default=8)
    tools_used: Mapped[int] = mapped_column(Integer, default=0)

    artifacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    waste_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    five_whys_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposed_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_change: Mapped[str | None] = mapped_column(Text, nullable=True)

    before_metric_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_metric_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    job_failed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    system: Mapped["System"] = relationship(back_populates="cycles")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    system_id: Mapped[str] = mapped_column(ForeignKey("systems.id"), nullable=False)
    cycle_id: Mapped[str | None] = mapped_column(ForeignKey("cycles.id"), nullable=True)

    # user | agent | system
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    system: Mapped["System"] = relationship(back_populates="messages")
    cycle: Mapped["Cycle | None"] = relationship(back_populates="messages")
