from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import SessionLocal, create_tables
from app.models import EventModel, SessionModel, TransactionModel


@pytest.fixture()
def db_session() -> Iterator[SessionLocal]:
    create_tables()
    session = SessionLocal()
    try:
        session.query(TransactionModel).delete()
        session.query(EventModel).delete()
        session.query(SessionModel).delete()
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()


def metadata(store_id: str = "store-1", **extra: object) -> str:
    payload = {"store_id": store_id}
    payload.update(extra)
    return json.dumps(payload)


def event(
    uid: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    store_id: str = "store-1",
    zone_id: str | None = None,
    zone_name: str | None = None,
    session_id: int | None = None,
    timestamp_ms: float | None = None,
    **metadata_extra: object,
) -> EventModel:
    return EventModel(
        event_uid=uid,
        visitor_id=visitor_id,
        session_id=session_id,
        event_type=event_type,
        timestamp=timestamp,
        timestamp_ms=timestamp_ms,
        zone_id=zone_id,
        zone_name=zone_name,
        metadata_json=metadata(store_id, **metadata_extra),
    )


def transaction(
    transaction_id: str,
    visitor_id: str,
    timestamp: datetime,
    amount: float = 100.0,
    store_id: str = "store-1",
    session_id: int | None = None,
    **metadata_extra: object,
) -> TransactionModel:
    return TransactionModel(
        transaction_id=transaction_id,
        visitor_id=visitor_id,
        session_id=session_id,
        timestamp=timestamp,
        amount=amount,
        metadata_json=metadata(store_id, **metadata_extra),
    )
