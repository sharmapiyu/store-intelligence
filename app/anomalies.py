from __future__ import annotations

import json
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel, TransactionModel


QUEUE_ENTER_EVENTS = {"QUEUE_ENTER", "QUEUE_JOIN", "QUEUE_JOINED", "CHECKOUT_ENTER"}
QUEUE_EXIT_EVENTS = {"QUEUE_EXIT", "QUEUE_LEAVE", "QUEUE_LEFT", "CHECKOUT_EXIT", "EXIT"}
ENTRY_EVENTS = {"ENTRY", "REENTRY"}
CONVERSION_EVENTS = {"PURCHASE", "TRANSACTION", "CHECKOUT_COMPLETE", "CHECKOUT_COMPLETED"}
ZONE_VISIT_EVENTS = {"ZONE_ENTER", "PRODUCT_AREA_VISITED"}


class AnomalyType(StrEnum):
    """Supported anomaly categories."""

    QUEUE_SPIKE = "QUEUE_SPIKE"
    CONVERSION_DROP = "CONVERSION_DROP"
    DEAD_ZONE = "DEAD_ZONE"


class Severity(StrEnum):
    """Business severity for detected anomalies."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AnomalyThresholds(BaseModel):
    """Runtime-configurable thresholds for anomaly detection."""

    queue_spike_depth: int = Field(..., ge=1)
    conversion_drop_rate: float = Field(..., ge=0, le=1)
    conversion_min_visitors: int = Field(..., ge=1)
    dead_zone_visit_count: int = Field(..., ge=0)
    dead_zone_min_store_visits: int = Field(..., ge=1)


class StoreAnomaly(BaseModel):
    """One detected anomaly with operator guidance."""

    anomaly_type: AnomalyType
    severity: Severity
    description: str
    suggested_action: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class StoreAnomalyResponse(BaseModel):
    """Response returned by GET /stores/{id}/anomalies."""

    store_id: str
    thresholds: AnomalyThresholds
    anomalies: list[StoreAnomaly]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


router = APIRouter()


@router.get("/stores/{store_id}/anomalies", response_model=StoreAnomalyResponse)
def get_store_anomalies(
    store_id: str,
    queue_spike_depth: Annotated[
        int,
        Query(ge=1, description="Current queue depth that triggers QUEUE_SPIKE."),
    ] = _env_int("ANOMALY_QUEUE_SPIKE_DEPTH", 5),
    conversion_drop_rate: Annotated[
        float,
        Query(ge=0, le=1, description="Conversion rate below this value triggers CONVERSION_DROP."),
    ] = _env_float("ANOMALY_CONVERSION_DROP_RATE", 0.2),
    conversion_min_visitors: Annotated[
        int,
        Query(ge=1, description="Minimum entered visitors before conversion anomalies are evaluated."),
    ] = _env_int("ANOMALY_CONVERSION_MIN_VISITORS", 10),
    dead_zone_visit_count: Annotated[
        int,
        Query(ge=0, description="Zone visit count at or below this value can trigger DEAD_ZONE."),
    ] = _env_int("ANOMALY_DEAD_ZONE_VISIT_COUNT", 0),
    dead_zone_min_store_visits: Annotated[
        int,
        Query(ge=1, description="Minimum total store zone visits before dead zones are evaluated."),
    ] = _env_int("ANOMALY_DEAD_ZONE_MIN_STORE_VISITS", 20),
    db: Session = Depends(get_db_session),
) -> StoreAnomalyResponse:
    """Detect operational anomalies from persisted store events."""

    thresholds = AnomalyThresholds(
        queue_spike_depth=queue_spike_depth,
        conversion_drop_rate=conversion_drop_rate,
        conversion_min_visitors=conversion_min_visitors,
        dead_zone_visit_count=dead_zone_visit_count,
        dead_zone_min_store_visits=dead_zone_min_store_visits,
    )
    events = _load_store_events(db=db, store_id=store_id)
    transactions = _load_store_transactions(db=db, store_id=store_id)

    anomalies: list[StoreAnomaly] = []
    queue_spike = _detect_queue_spike(events=events, threshold=thresholds.queue_spike_depth)
    if queue_spike is not None:
        anomalies.append(queue_spike)

    conversion_drop = _detect_conversion_drop(
        events=events,
        transactions=transactions,
        rate_threshold=thresholds.conversion_drop_rate,
        min_visitors=thresholds.conversion_min_visitors,
    )
    if conversion_drop is not None:
        anomalies.append(conversion_drop)

    anomalies.extend(
        _detect_dead_zones(
            events=events,
            zone_visit_threshold=thresholds.dead_zone_visit_count,
            min_store_visits=thresholds.dead_zone_min_store_visits,
        )
    )

    return StoreAnomalyResponse(
        store_id=store_id,
        thresholds=thresholds,
        anomalies=anomalies,
    )


def _detect_queue_spike(events: list[EventModel], threshold: int) -> StoreAnomaly | None:
    latest_queue_state: dict[str, bool] = {}

    for event in events:
        if event.event_type in QUEUE_ENTER_EVENTS:
            latest_queue_state[event.visitor_id] = True
        elif event.event_type in QUEUE_EXIT_EVENTS:
            latest_queue_state[event.visitor_id] = False

    queue_depth = sum(1 for is_waiting in latest_queue_state.values() if is_waiting)
    if queue_depth < threshold:
        return None

    return StoreAnomaly(
        anomaly_type=AnomalyType.QUEUE_SPIKE,
        severity=_severity_from_ratio(queue_depth / threshold),
        description=f"Current queue depth is {queue_depth}, meeting or exceeding the configured threshold of {threshold}.",
        suggested_action="Open an additional billing counter or assign staff to queue management.",
        evidence={"queue_depth": queue_depth, "threshold": threshold},
    )


def _detect_conversion_drop(
    events: list[EventModel],
    transactions: list[TransactionModel],
    rate_threshold: float,
    min_visitors: int,
) -> StoreAnomaly | None:
    entered_visitors = {
        event.visitor_id
        for event in events
        if event.event_type in ENTRY_EVENTS
    }
    if len(entered_visitors) < min_visitors:
        return None

    converted_visitors = {
        event.visitor_id
        for event in events
        if event.event_type in CONVERSION_EVENTS
    } | {transaction.visitor_id for transaction in transactions}
    conversion_rate = len(entered_visitors & converted_visitors) / len(entered_visitors)

    if conversion_rate >= rate_threshold:
        return None

    severity_gap = 1.0 if rate_threshold == 0 else (rate_threshold - conversion_rate) / rate_threshold
    return StoreAnomaly(
        anomaly_type=AnomalyType.CONVERSION_DROP,
        severity=_severity_from_ratio(1.0 + severity_gap),
        description=(
            f"Conversion rate is {conversion_rate:.2%}, below the configured threshold "
            f"of {rate_threshold:.2%}."
        ),
        suggested_action="Review checkout availability, pricing friction, and product availability for the current period.",
        evidence={
            "entered_visitors": len(entered_visitors),
            "converted_visitors": len(entered_visitors & converted_visitors),
            "conversion_rate": conversion_rate,
            "threshold": rate_threshold,
        },
    )


def _detect_dead_zones(
    events: list[EventModel],
    zone_visit_threshold: int,
    min_store_visits: int,
) -> list[StoreAnomaly]:
    zone_visits: dict[str, int] = defaultdict(int)

    for event in events:
        if not event.zone_id:
            continue
        zone = _zone_name(event)
        zone_visits.setdefault(zone, 0)
        if event.event_type in ZONE_VISIT_EVENTS:
            zone_visits[zone] += 1

    total_visits = sum(zone_visits.values())
    if total_visits < min_store_visits:
        return []

    anomalies: list[StoreAnomaly] = []
    for zone, visit_count in sorted(zone_visits.items()):
        if visit_count > zone_visit_threshold:
            continue

        anomalies.append(
            StoreAnomaly(
                anomaly_type=AnomalyType.DEAD_ZONE,
                severity=Severity.MEDIUM if visit_count == 0 else Severity.LOW,
                description=(
                    f"Zone '{zone}' has {visit_count} visits while the configured dead-zone "
                    f"threshold is {zone_visit_threshold}."
                ),
                suggested_action="Review zone visibility, signage, planogram placement, and staff guidance for this area.",
                evidence={
                    "zone": zone,
                    "visit_count": visit_count,
                    "threshold": zone_visit_threshold,
                    "total_store_zone_visits": total_visits,
                },
            )
        )

    return anomalies


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


def _zone_name(event: EventModel) -> str:
    return event.zone_name or event.zone_id or "unknown"


def _severity_from_ratio(ratio: float) -> Severity:
    if ratio >= 2.0:
        return Severity.HIGH
    if ratio >= 1.25:
        return Severity.MEDIUM
    return Severity.LOW


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
    """Create tables when running this module as a standalone anomaly API."""

    del app
    create_tables()
    yield


app = FastAPI(
    title="Store Intelligence Anomaly API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
