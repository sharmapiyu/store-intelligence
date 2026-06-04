from __future__ import annotations

import json
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel, TransactionModel


class FunnelStage(StrEnum):
    """Ordered session funnel stages."""

    ENTRY = "ENTRY"
    ZONE_VISIT = "ZONE_VISIT"
    BILLING = "BILLING"
    PURCHASE = "PURCHASE"


ENTRY_EVENTS = {"ENTRY"}
REENTRY_EVENTS = {"REENTRY"}
SESSION_END_EVENTS = {"EXIT"}
ZONE_VISIT_EVENTS = {"ZONE_ENTER", "ZONE_DWELL", "PRODUCT_AREA_VISITED"}
BILLING_EVENTS = {
    "BILLING",
    "BILLING_ENTER",
    "CHECKOUT_ENTER",
    "QUEUE_ENTER",
    "QUEUE_JOIN",
    "QUEUE_JOINED",
}
PURCHASE_EVENTS = {
    "PURCHASE",
    "TRANSACTION",
    "CHECKOUT_COMPLETE",
    "CHECKOUT_COMPLETED",
}


class FunnelStageMetric(BaseModel):
    """Count and conversion detail for one funnel stage."""

    stage: FunnelStage
    sessions: int = Field(..., ge=0)
    conversion_from_entry: float = Field(..., ge=0, le=1)
    dropoff_from_previous: float = Field(..., ge=0, le=1)


class StoreFunnelResponse(BaseModel):
    """Response returned by GET /stores/{id}/funnel."""

    store_id: str
    total_sessions: int = Field(..., ge=0)
    stages: list[FunnelStageMetric]


@dataclass
class FunnelSession:
    """In-memory visit session used only for funnel calculation."""

    session_key: str
    visitor_id: str
    started_at: datetime
    ended_at: datetime | None = None
    explicit_session_id: int | None = None
    stages: set[FunnelStage] = field(default_factory=set)

    def contains(self, timestamp: datetime) -> bool:
        if timestamp < self.started_at:
            return False
        if self.ended_at is None:
            return True
        return self.started_at <= timestamp <= self.ended_at


router = APIRouter()


@router.get("/stores/{store_id}/funnel", response_model=StoreFunnelResponse)
def get_store_funnel(
    store_id: str,
    db: Session = Depends(get_db_session),
) -> StoreFunnelResponse:
    """Compute the store funnel from persisted session events."""

    events = _load_store_events(db=db, store_id=store_id)
    transactions = _load_store_transactions(db=db, store_id=store_id)
    sessions = _build_sessions(events)
    _apply_transactions(sessions=sessions, transactions=transactions)
    return _build_response(store_id=store_id, sessions=sessions)


def _load_store_events(db: Session, store_id: str) -> list[EventModel]:
    rows = db.execute(select(EventModel).order_by(EventModel.timestamp.asc(), EventModel.id.asc())).scalars().all()
    return [
        event
        for event in rows
        if _metadata_value(event.metadata_json, "store_id") == store_id
        and not _is_staff(event.metadata_json)
    ]


def _load_store_transactions(db: Session, store_id: str) -> list[TransactionModel]:
    rows = db.execute(
        select(TransactionModel).order_by(TransactionModel.timestamp.asc(), TransactionModel.id.asc())
    ).scalars().all()
    return [
        transaction
        for transaction in rows
        if _metadata_value(transaction.metadata_json, "store_id") == store_id
        and not _is_staff(transaction.metadata_json)
    ]


def _build_sessions(events: list[EventModel]) -> list[FunnelSession]:
    explicit_events = [event for event in events if event.session_id is not None]
    inferred_events = [event for event in events if event.session_id is None]
    return _build_explicit_sessions(explicit_events) + _build_inferred_sessions(inferred_events)


def _build_explicit_sessions(events: list[EventModel]) -> list[FunnelSession]:
    grouped: dict[int, list[EventModel]] = defaultdict(list)
    for event in events:
        if event.session_id is not None:
            grouped[event.session_id].append(event)

    sessions: list[FunnelSession] = []
    for session_id, group in grouped.items():
        ordered = sorted(group, key=lambda item: (item.timestamp, item.id or 0))
        first_event = ordered[0]
        session = FunnelSession(
            session_key=f"db:{session_id}",
            visitor_id=first_event.visitor_id,
            started_at=first_event.timestamp,
            explicit_session_id=session_id,
        )
        for event in ordered:
            _apply_event_to_session(session=session, event=event)
            if event.event_type in SESSION_END_EVENTS:
                session.ended_at = event.timestamp
        sessions.append(session)
    return sessions


def _build_inferred_sessions(events: list[EventModel]) -> list[FunnelSession]:
    events_by_visitor: dict[str, list[EventModel]] = defaultdict(list)
    for event in events:
        events_by_visitor[event.visitor_id].append(event)

    sessions: list[FunnelSession] = []
    for visitor_id, visitor_events in events_by_visitor.items():
        current_session: FunnelSession | None = None
        visit_number = 0

        for event in sorted(visitor_events, key=lambda item: (item.timestamp, item.id or 0)):
            starts_new_visit = (
                current_session is None
                or event.event_type in REENTRY_EVENTS
                or (
                    event.event_type in ENTRY_EVENTS
                    and current_session.ended_at is not None
                )
            )

            if starts_new_visit:
                visit_number += 1
                current_session = FunnelSession(
                    session_key=f"inferred:{visitor_id}:{visit_number}",
                    visitor_id=visitor_id,
                    started_at=event.timestamp,
                )
                sessions.append(current_session)

            _apply_event_to_session(session=current_session, event=event)

            if event.event_type in SESSION_END_EVENTS:
                current_session.ended_at = event.timestamp

    return sessions


def _apply_event_to_session(session: FunnelSession, event: EventModel) -> None:
    if event.event_type in ENTRY_EVENTS or event.event_type in REENTRY_EVENTS:
        session.stages.add(FunnelStage.ENTRY)
    elif event.event_type in ZONE_VISIT_EVENTS:
        if _is_customer_zone(event):
            session.stages.add(FunnelStage.ZONE_VISIT)
    elif event.event_type in BILLING_EVENTS:
        session.stages.add(FunnelStage.BILLING)
    elif event.event_type in PURCHASE_EVENTS:
        session.stages.add(FunnelStage.PURCHASE)

    if event.timestamp < session.started_at:
        session.started_at = event.timestamp
    if session.ended_at is None or event.timestamp > session.ended_at:
        if event.event_type not in SESSION_END_EVENTS:
            session.ended_at = None


def _apply_transactions(
    sessions: list[FunnelSession],
    transactions: list[TransactionModel],
) -> None:
    sessions_by_id = {
        session.explicit_session_id: session
        for session in sessions
        if session.explicit_session_id is not None
    }
    sessions_by_visitor: dict[str, list[FunnelSession]] = defaultdict(list)
    for session in sessions:
        sessions_by_visitor[session.visitor_id].append(session)

    for transaction in transactions:
        matched_session = None
        if transaction.session_id is not None:
            matched_session = sessions_by_id.get(transaction.session_id)

        if matched_session is None:
            matched_session = _match_transaction_to_inferred_session(
                transaction=transaction,
                sessions=sessions_by_visitor.get(transaction.visitor_id, []),
            )

        if matched_session is not None:
            matched_session.stages.add(FunnelStage.PURCHASE)


def _match_transaction_to_inferred_session(
    transaction: TransactionModel,
    sessions: list[FunnelSession],
) -> FunnelSession | None:
    candidates = [session for session in sessions if session.contains(transaction.timestamp)]
    if candidates:
        return candidates[-1]

    earlier_sessions = [
        session
        for session in sessions
        if session.started_at <= transaction.timestamp
    ]
    return earlier_sessions[-1] if earlier_sessions else None


def _build_response(
    store_id: str,
    sessions: list[FunnelSession],
) -> StoreFunnelResponse:
    ordered_stages = [
        FunnelStage.ENTRY,
        FunnelStage.ZONE_VISIT,
        FunnelStage.BILLING,
        FunnelStage.PURCHASE,
    ]
    stage_counts = {
        stage: sum(1 for session in sessions if stage in session.stages)
        for stage in ordered_stages
    }
    entry_count = stage_counts[FunnelStage.ENTRY]
    previous_count: int | None = None
    stage_metrics: list[FunnelStageMetric] = []

    for stage in ordered_stages:
        count = stage_counts[stage]
        if previous_count is None:
            dropoff = 0.0
        else:
            dropoff = 0.0 if previous_count == 0 else max(0.0, (previous_count - count) / previous_count)

        stage_metrics.append(
            FunnelStageMetric(
                stage=stage,
                sessions=count,
                conversion_from_entry=_safe_ratio(count, entry_count),
                dropoff_from_previous=dropoff,
            )
        )
        previous_count = count

    return StoreFunnelResponse(
        store_id=store_id,
        total_sessions=len(sessions),
        stages=stage_metrics,
    )


def _is_customer_zone(event: EventModel) -> bool:
    zone_id = str(event.zone_id or "").lower()
    zone_name = str(event.zone_name or "").lower()
    if zone_id in {"entry", "exit"}:
        return False
    if zone_name in {"entry", "exit"}:
        return False
    return True


def _metadata_value(metadata_json: str | None, key: str) -> str | None:
    value = _metadata(metadata_json).get(key)
    if value is None:
        return None
    return str(value)


def _metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}

    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _is_staff(metadata_json: str | None) -> bool:
    metadata = _metadata(metadata_json)
    if metadata.get("is_staff") is True:
        return True

    person_type = str(metadata.get("person_type", "")).lower()
    role = str(metadata.get("role", "")).lower()
    return person_type == "staff" or role == "staff"


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables when running this module as a standalone funnel API."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence Funnel API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
