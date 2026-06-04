from __future__ import annotations

import json
from collections import defaultdict
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel


VISIT_EVENTS = {"ZONE_ENTER", "PRODUCT_AREA_VISITED"}
DWELL_EVENTS = {"ZONE_DWELL"}


class ConfidenceFlag(StrEnum):
    """Data confidence for one heatmap zone."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ZoneHeatmapItem(BaseModel):
    """Heatmap metric for one store zone."""

    zone: str
    visit_count: int = Field(..., ge=0)
    avg_dwell: float = Field(..., ge=0)
    normalized_score: float = Field(..., ge=0, le=1)
    confidence_flag: ConfidenceFlag


class StoreHeatmapResponse(BaseModel):
    """Response returned by GET /stores/{id}/heatmap."""

    store_id: str
    zones: list[ZoneHeatmapItem]


class ZoneAccumulator(BaseModel):
    """Mutable aggregation bucket for one zone."""

    zone: str
    visit_count: int = 0
    dwell_samples: list[float] = Field(default_factory=list)

    @property
    def avg_dwell(self) -> float:
        if not self.dwell_samples:
            return 0.0
        return sum(self.dwell_samples) / len(self.dwell_samples)

    @property
    def observation_count(self) -> int:
        return self.visit_count + len(self.dwell_samples)


router = APIRouter()


@router.get("/stores/{store_id}/heatmap", response_model=StoreHeatmapResponse)
def get_store_heatmap(
    store_id: str,
    db: Session = Depends(get_db_session),
) -> StoreHeatmapResponse:
    """Compute zone heatmap scores from persisted zone events."""

    events = _load_store_events(db=db, store_id=store_id)
    buckets = _aggregate_zone_events(events)
    max_raw_score = max((_raw_score(bucket) for bucket in buckets.values()), default=0.0)

    items = [
        ZoneHeatmapItem(
            zone=bucket.zone,
            visit_count=bucket.visit_count,
            avg_dwell=bucket.avg_dwell,
            normalized_score=_normalized_score(
                raw_score=_raw_score(bucket),
                max_raw_score=max_raw_score,
            ),
            confidence_flag=_confidence_flag(bucket.observation_count),
        )
        for bucket in sorted(buckets.values(), key=lambda item: item.zone)
    ]

    return StoreHeatmapResponse(store_id=store_id, zones=items)


def _load_store_events(db: Session, store_id: str) -> list[EventModel]:
    rows = db.execute(select(EventModel).order_by(EventModel.timestamp.asc())).scalars().all()
    return [
        event
        for event in rows
        if event.zone_id
        and _metadata_value(event.metadata_json, "store_id") == store_id
        and not _is_staff(event.metadata_json)
    ]


def _aggregate_zone_events(events: list[EventModel]) -> dict[str, ZoneAccumulator]:
    buckets: dict[str, ZoneAccumulator] = {}

    for event in events:
        zone_key = _zone_key(event)
        if zone_key not in buckets:
            buckets[zone_key] = ZoneAccumulator(zone=zone_key)

        bucket = buckets[zone_key]
        if event.event_type in VISIT_EVENTS:
            bucket.visit_count += 1
        elif event.event_type in DWELL_EVENTS:
            dwell_ms = _event_dwell_ms(event)
            if dwell_ms is not None:
                bucket.dwell_samples.append(dwell_ms)

    return {
        zone: bucket
        for zone, bucket in buckets.items()
        if bucket.visit_count > 0 or bucket.dwell_samples
    }


def _zone_key(event: EventModel) -> str:
    return event.zone_name or event.zone_id or "unknown"


def _raw_score(bucket: ZoneAccumulator) -> float:
    dwell_seconds = bucket.avg_dwell / 1000.0
    return float(bucket.visit_count) * (1.0 + dwell_seconds)


def _normalized_score(raw_score: float, max_raw_score: float) -> float:
    if max_raw_score <= 0:
        return 0.0
    return raw_score / max_raw_score


def _confidence_flag(observation_count: int) -> ConfidenceFlag:
    if observation_count >= 20:
        return ConfidenceFlag.HIGH
    if observation_count >= 5:
        return ConfidenceFlag.MEDIUM
    return ConfidenceFlag.LOW


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables when running this module as a standalone heatmap API."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence Heatmap API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
