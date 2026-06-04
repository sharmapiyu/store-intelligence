from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SessionModel(Base):
    """Visitor session persisted across detected movement events."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_visitor_id", "visitor_id"),
        Index("ix_sessions_timestamp", "timestamp"),
        Index("ix_sessions_visitor_id_timestamp", "visitor_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list[EventModel]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list[TransactionModel]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class EventModel(Base):
    """Validated store event generated from the detection and zone pipeline."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_event_uid", "event_uid", unique=True),
        Index("ix_events_visitor_id", "visitor_id"),
        Index("ix_events_timestamp", "timestamp"),
        Index("ix_events_visitor_id_timestamp", "visitor_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_uid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frame_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zone_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[SessionModel | None] = relationship(back_populates="events")


class TransactionModel(Base):
    """Retail transaction associated with a visitor session when available."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_visitor_id", "visitor_id"),
        Index("ix_transactions_timestamp", "timestamp"),
        Index("ix_transactions_visitor_id_timestamp", "visitor_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[SessionModel | None] = relationship(back_populates="transactions")
