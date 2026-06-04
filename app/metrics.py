from __future__ import annotations

import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel, TransactionModel


QUEUE_ENTER_EVENTS = {"QUEUE_ENTER", "QUEUE_JOIN", "QUEUE_JOINED", "CHECKOUT_ENTER"}
QUEUE_EXIT_EVENTS = {"QUEUE_EXIT", "QUEUE_LEAVE", "QUEUE_LEFT", "CHECKOUT_EXIT", "EXIT"}
CONVERSION_EVENTS = {"PURCHASE", "TRANSACTION", "CHECKOUT_COMPLETE", "CHECKOUT_COMPLETED"}
ENTRY_EVENTS = {"ENTRY", "REENTRY"}
DWELL_EVENT = "ZONE_DWELL"


class ZoneDwellMetric(BaseModel):
    """Average dwell time for one zone."""

    zone_id: str
    zone_name: str | None = None
    avg_dwell_ms: float = Field(..., ge=0)
    samples: int = Field(..., ge=0)


class StoreMetricsResponse(BaseModel):
    """Metrics returned by GET /stores/{id}/metrics."""

    store_id: str
    unique_visitors: int = Field(..., ge=0)
    conversion_rate: float = Field(..., ge=0, le=1)
    avg_dwell_per_zone: list[ZoneDwellMetric]
    queue_depth: int = Field(..., ge=0)
    abandonment_rate: float = Field(..., ge=0, le=1)


router = APIRouter()


@router.get("/stores/{store_id}/metrics", response_model=StoreMetricsResponse)
def get_store_metrics(
    store_id: str,
    db: Session = Depends(get_db_session),
) -> StoreMetricsResponse:
    """Compute store metrics from persisted events and transactions."""

    events = _load_store_events(db=db, store_id=store_id)
    transactions = _load_store_transactions(db=db, store_id=store_id)

    visitor_ids = _visitor_ids(events=events, transactions=transactions)
    converted_visitors = _converted_visitors(events=events, transactions=transactions)
    entry_visitors = {
        event.visitor_id
        for event in events
        if event.event_type in ENTRY_EVENTS
    }

    denominator = len(visitor_ids)
    unique_visitors = len(visitor_ids)
    conversion_rate = _safe_ratio(len(converted_visitors), denominator)
    abandonment_rate = _safe_ratio(
        len(entry_visitors - converted_visitors),
        len(entry_visitors),
    )

    return StoreMetricsResponse(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=_avg_dwell_per_zone(events),
        queue_depth=_queue_depth(events),
        abandonment_rate=abandonment_rate,
    )


def _load_store_events(db: Session, store_id: str) -> list[EventModel]:
    rows = db.execute(select(EventModel).order_by(EventModel.timestamp.asc())).scalars().all()
    return [
        event
        for event in rows
        if _metadata_value(event.metadata_json, "store_id") == store_id
        and not _is_staff(event.metadata_json)
    ]


def _load_store_transactions(db: Session, store_id: str) -> list[TransactionModel]:
    rows = db.execute(select(TransactionModel).order_by(TransactionModel.timestamp.asc())).scalars().all()
    return [
        transaction
        for transaction in rows
        if _metadata_value(transaction.metadata_json, "store_id") == store_id
        and not _is_staff(transaction.metadata_json)
    ]


def _visitor_ids(
    events: list[EventModel],
    transactions: list[TransactionModel],
) -> set[str]:
    return {event.visitor_id for event in events} | {
        transaction.visitor_id for transaction in transactions
    }


def _converted_visitors(
    events: list[EventModel],
    transactions: list[TransactionModel],
) -> set[str]:
    converted_from_events = {
        event.visitor_id
        for event in events
        if event.event_type in CONVERSION_EVENTS
    }
    converted_from_transactions = {transaction.visitor_id for transaction in transactions}
    return converted_from_events | converted_from_transactions


def _avg_dwell_per_zone(events: list[EventModel]) -> list[ZoneDwellMetric]:
    dwell_by_zone: dict[str, list[float]] = defaultdict(list)
    zone_names: dict[str, str | None] = {}

    for event in events:
        if event.event_type != DWELL_EVENT or not event.zone_id:
            continue

        dwell_ms = _event_dwell_ms(event)
        if dwell_ms is None:
            continue

        dwell_by_zone[event.zone_id].append(dwell_ms)
        zone_names[event.zone_id] = event.zone_name

    return [
        ZoneDwellMetric(
            zone_id=zone_id,
            zone_name=zone_names.get(zone_id),
            avg_dwell_ms=sum(values) / len(values),
            samples=len(values),
        )
        for zone_id, values in sorted(dwell_by_zone.items())
        if values
    ]


def _queue_depth(events: list[EventModel]) -> int:
    latest_queue_state: dict[str, bool] = {}

    for event in events:
        if event.event_type in QUEUE_ENTER_EVENTS:
            latest_queue_state[event.visitor_id] = True
        elif event.event_type in QUEUE_EXIT_EVENTS:
            latest_queue_state[event.visitor_id] = False

    return sum(1 for is_waiting in latest_queue_state.values() if is_waiting)


def _event_dwell_ms(event: EventModel) -> float | None:
    metadata = _metadata(event.metadata_json)
    raw_dwell = metadata.get("dwell_time_ms")
    if raw_dwell is None:
        return event.timestamp_ms

    try:
        dwell_ms = float(raw_dwell)
    except (TypeError, ValueError):
        return None

    return max(0.0, dwell_ms)


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
    """Create tables when running this module as a standalone metrics API."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence Metrics API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
