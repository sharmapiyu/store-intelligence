from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel


class HealthStatus(StrEnum):
    """Overall service health state."""

    OK = "OK"
    DEGRADED = "DEGRADED"


class StoreFeedStatus(StrEnum):
    """Freshness state for one store feed."""

    ACTIVE = "ACTIVE"
    STALE_FEED = "STALE_FEED"
    NO_EVENTS = "NO_EVENTS"


class StoreStatus(BaseModel):
    """Health details for one store."""

    store_id: str
    status: StoreFeedStatus
    last_event_timestamp: datetime | None = None
    seconds_since_last_event: float | None = Field(default=None, ge=0)


class HealthResponse(BaseModel):
    """Response returned by GET /health."""

    status: HealthStatus
    last_event_timestamp: datetime | None = None
    store_status: list[StoreStatus]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def get_health(
    stale_after_seconds: int = Query(
        default=_env_int("HEALTH_STALE_AFTER_SECONDS", 300),
        ge=1,
        description="Feed is stale when the latest event is older than this many seconds.",
    ),
    db: Session = Depends(get_db_session),
) -> HealthResponse:
    """Report database-backed service and store feed freshness."""

    now = datetime.now(tz=UTC)
    events = db.execute(select(EventModel).order_by(EventModel.timestamp.asc())).scalars().all()
    latest_event = max((_ensure_aware(event.timestamp) for event in events), default=None)
    store_status = _build_store_status(
        events=events,
        now=now,
        stale_after=timedelta(seconds=stale_after_seconds),
    )
    overall_status = (
        HealthStatus.DEGRADED
        if not events or any(item.status != StoreFeedStatus.ACTIVE for item in store_status)
        else HealthStatus.OK
    )

    return HealthResponse(
        status=overall_status,
        last_event_timestamp=latest_event,
        store_status=store_status,
    )


def _build_store_status(
    events: list[EventModel],
    now: datetime,
    stale_after: timedelta,
) -> list[StoreStatus]:
    latest_by_store: dict[str, datetime] = {}

    for event in events:
        store_id = _metadata_value(event.metadata_json, "store_id") or "unknown"
        timestamp = _ensure_aware(event.timestamp)
        if store_id not in latest_by_store or timestamp > latest_by_store[store_id]:
            latest_by_store[store_id] = timestamp

    if not latest_by_store:
        return [
            StoreStatus(
                store_id="unknown",
                status=StoreFeedStatus.NO_EVENTS,
                last_event_timestamp=None,
                seconds_since_last_event=None,
            )
        ]

    statuses: list[StoreStatus] = []
    for store_id, timestamp in sorted(latest_by_store.items()):
        seconds_since_last_event = max(0.0, (now - timestamp).total_seconds())
        feed_status = (
            StoreFeedStatus.STALE_FEED
            if now - timestamp > stale_after
            else StoreFeedStatus.ACTIVE
        )
        statuses.append(
            StoreStatus(
                store_id=store_id,
                status=feed_status,
                last_event_timestamp=timestamp,
                seconds_since_last_event=seconds_since_last_event,
            )
        )
    return statuses


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


def _ensure_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables when running this module as a standalone health API."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence Health API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
