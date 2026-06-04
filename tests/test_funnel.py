from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient

from app.database import SessionLocal, create_tables
from app.funnel import app
from app.models import EventModel, SessionModel, TransactionModel


def _metadata(store_id: str, **extra: object) -> str:
    payload = {"store_id": store_id}
    payload.update(extra)
    return json.dumps(payload)


def _event(
    uid: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    store_id: str = "store-1",
    zone_id: str | None = None,
    zone_name: str | None = None,
    session_id: int | None = None,
    **metadata: object,
) -> EventModel:
    return EventModel(
        event_uid=uid,
        visitor_id=visitor_id,
        session_id=session_id,
        event_type=event_type,
        timestamp=timestamp,
        zone_id=zone_id,
        zone_name=zone_name,
        metadata_json=_metadata(store_id, **metadata),
    )


def _reset_database() -> None:
    create_tables()
    db = SessionLocal()
    try:
        db.query(TransactionModel).delete()
        db.query(EventModel).delete()
        db.query(SessionModel).delete()
        db.commit()
    finally:
        db.close()


def test_funnel_is_session_based_reentry_aware_and_deduplicated() -> None:
    _reset_database()
    start = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
    db = SessionLocal()
    try:
        db.add_all(
            [
                _event("e1", "visitor-1", "ENTRY", start),
                _event("e2", "visitor-1", "ZONE_ENTER", start + timedelta(seconds=10), zone_id="aisle"),
                _event("e3", "visitor-1", "ZONE_ENTER", start + timedelta(seconds=20), zone_id="aisle"),
                _event("e4", "visitor-1", "CHECKOUT_ENTER", start + timedelta(seconds=30)),
                _event("e5", "visitor-1", "CHECKOUT_ENTER", start + timedelta(seconds=40)),
                _event("e6", "visitor-1", "EXIT", start + timedelta(seconds=50)),
                _event("e7", "visitor-1", "REENTRY", start + timedelta(minutes=5)),
                _event("e8", "visitor-1", "ZONE_DWELL", start + timedelta(minutes=5, seconds=10), zone_id="aisle"),
                _event("e9", "staff-1", "ENTRY", start, is_staff=True),
                _event("e10", "staff-1", "ZONE_ENTER", start + timedelta(seconds=5), zone_id="aisle", is_staff=True),
            ]
        )
        db.add(
            TransactionModel(
                visitor_id="visitor-1",
                transaction_id="txn-1",
                timestamp=start + timedelta(seconds=45),
                amount=100.0,
                metadata_json=_metadata("store-1"),
            )
        )
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        response = client.get("/stores/store-1/funnel")

    assert response.status_code == 200
    body = response.json()
    counts = {stage["stage"]: stage["sessions"] for stage in body["stages"]}

    assert body["total_sessions"] == 2
    assert counts == {
        "ENTRY": 2,
        "ZONE_VISIT": 2,
        "BILLING": 1,
        "PURCHASE": 1,
    }


def test_funnel_uses_explicit_session_ids_when_available() -> None:
    _reset_database()
    start = datetime(2026, 5, 31, 11, 0, tzinfo=UTC)
    db = SessionLocal()
    try:
        db.add_all(
            [
                SessionModel(
                    id=101,
                    visitor_id="visitor-2",
                    timestamp=start,
                    ended_at=start + timedelta(seconds=30),
                    status="completed",
                ),
                SessionModel(
                    id=102,
                    visitor_id="visitor-2",
                    timestamp=start + timedelta(minutes=10),
                    ended_at=start + timedelta(minutes=11),
                    status="completed",
                ),
            ]
        )
        db.add_all(
            [
                _event("s1-e1", "visitor-2", "ENTRY", start, session_id=101),
                _event("s1-e2", "visitor-2", "ZONE_ENTER", start + timedelta(seconds=10), zone_id="aisle", session_id=101),
                _event("s2-e1", "visitor-2", "ENTRY", start + timedelta(minutes=10), session_id=102),
                _event("s2-e2", "visitor-2", "CHECKOUT_ENTER", start + timedelta(minutes=10, seconds=10), session_id=102),
            ]
        )
        db.add(
            TransactionModel(
                visitor_id="visitor-2",
                session_id=102,
                transaction_id="txn-2",
                timestamp=start + timedelta(minutes=10, seconds=20),
                amount=50.0,
                metadata_json=_metadata("store-1"),
            )
        )
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        response = client.get("/stores/store-1/funnel")

    assert response.status_code == 200
    counts = {stage["stage"]: stage["sessions"] for stage in response.json()["stages"]}

    assert counts["ENTRY"] == 2
    assert counts["ZONE_VISIT"] == 1
    assert counts["BILLING"] == 1
    assert counts["PURCHASE"] == 1
