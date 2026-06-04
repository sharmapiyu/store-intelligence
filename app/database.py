from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


DEFAULT_DATABASE_URL = "sqlite:///./data/store_intelligence.db"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


def get_database_url() -> str:
    """Read the database URL from the environment with a SQLite default."""

    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent folder for file-based SQLite databases."""

    if not database_url.startswith("sqlite:///"):
        return

    database_path = database_url.replace("sqlite:///", "", 1)
    if database_path == ":memory:":
        return

    Path(database_path).parent.mkdir(parents=True, exist_ok=True)


def _connect_args(database_url: str) -> dict[str, bool]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _engine_kwargs(database_url: str) -> dict[str, object]:
    if database_url == "sqlite:///:memory:":
        return {"poolclass": StaticPool}
    return {}


DATABASE_URL = get_database_url()
_ensure_sqlite_directory(DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args(DATABASE_URL),
    future=True,
    **_engine_kwargs(DATABASE_URL),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    """Enable SQLite foreign-key checks for every new connection."""

    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_tables() -> None:
    """Create all configured tables automatically."""

    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI-compatible database session dependency."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
