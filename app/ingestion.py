from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import create_tables, get_db_session
from app.models import EventModel
from app.schemas import (
    ApiErrorCode,
    EventBatchIngestRequest,
    EventBatchIngestResponse,
    EventIngestItem,
    EventIngestResult,
    StructuredError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables when the ingestion service starts."""

    del app
    create_tables()
    yield


router = APIRouter()


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request
    error = StructuredError(
        code=ApiErrorCode.VALIDATION_ERROR,
        message="Request validation failed.",
        details=[
            {
                "location": list(item.get("loc", [])),
                "message": item.get("msg", ""),
                "type": item.get("type", ""),
            }
            for item in exc.errors()
        ],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error.model_dump(mode="json"),
    )


@router.post(
    "/events/ingest",
    response_model=EventBatchIngestResponse,
    status_code=status.HTTP_200_OK,
)
def ingest_events(
    payload: EventBatchIngestRequest,
    db: Session = Depends(get_db_session),
) -> EventBatchIngestResponse:
    """Persist a validated batch of events with idempotent duplicate handling."""

    try:
        prepared_items = [_prepare_event(item) for item in payload.events]
    except ValueError as exc:
        error = StructuredError(
            code=ApiErrorCode.VALIDATION_ERROR,
            message=str(exc),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error.model_dump(mode="json"),
        )

    existing_by_uid = _load_existing_event_ids(
        db=db,
        event_uids=[item["event_uid"] for item in prepared_items],
    )

    results: list[EventIngestResult] = []
    new_models: list[EventModel] = []
    seen_in_request: set[str] = set()

    for item in prepared_items:
        event_uid = item["event_uid"]
        visitor_id = item["visitor_id"]

        if event_uid in existing_by_uid:
            results.append(
                EventIngestResult(
                    event_uid=event_uid,
                    visitor_id=visitor_id,
                    status="duplicate",
                    database_id=existing_by_uid[event_uid],
                )
            )
            continue

        if event_uid in seen_in_request:
            results.append(
                EventIngestResult(
                    event_uid=event_uid,
                    visitor_id=visitor_id,
                    status="duplicate",
                    database_id=None,
                )
            )
            continue

        seen_in_request.add(event_uid)
        model = _to_event_model(item)
        new_models.append(model)
        results.append(
            EventIngestResult(
                event_uid=event_uid,
                visitor_id=visitor_id,
                status="accepted",
                database_id=None,
            )
        )

    try:
        db.add_all(new_models)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _retry_after_integrity_error(payload=payload, db=db)
    except SQLAlchemyError as exc:
        db.rollback()
        error = StructuredError(
            code=ApiErrorCode.DATABASE_ERROR,
            message="Failed to persist events.",
            details=[{"error": str(exc)}],
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error.model_dump(mode="json"),
        )

    new_id_by_uid = {model.event_uid: model.id for model in new_models}
    finalized_results = [
        result.model_copy(
            update={"database_id": new_id_by_uid.get(result.event_uid, result.database_id)}
        )
        for result in results
    ]
    duplicate_count = sum(1 for result in finalized_results if result.status == "duplicate")

    return EventBatchIngestResponse(
        accepted=len(finalized_results) - duplicate_count,
        duplicates=duplicate_count,
        total=len(finalized_results),
        results=finalized_results,
    )


def _retry_after_integrity_error(
    payload: EventBatchIngestRequest,
    db: Session,
) -> EventBatchIngestResponse | JSONResponse:
    """Handle concurrent duplicate inserts by re-reading and retrying once."""

    try:
        db.expire_all()
        return ingest_events(payload=payload, db=db)
    except SQLAlchemyError as exc:
        error = StructuredError(
            code=ApiErrorCode.DATABASE_ERROR,
            message="Failed to persist events after duplicate reconciliation.",
            details=[{"error": str(exc)}],
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error.model_dump(mode="json"),
        )


def _load_existing_event_ids(
    db: Session,
    event_uids: list[str],
) -> dict[str, int]:
    if not event_uids:
        return {}

    rows = db.execute(
        select(EventModel.event_uid, EventModel.id).where(EventModel.event_uid.in_(event_uids))
    ).all()
    return {event_uid: database_id for event_uid, database_id in rows}


def _prepare_event(event: EventIngestItem) -> dict[str, Any]:
    visitor_id = event.resolved_visitor_id()
    timestamp = event.resolved_timestamp()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    event_uid = event.event_id or _fingerprint_event(
        visitor_id=visitor_id,
        event_type=event.event_type.value,
        timestamp=timestamp.isoformat(),
        frame_number=event.frame_number,
        timestamp_ms=event.timestamp_ms,
        zone_id=event.zone_id,
    )

    return {
        "event_uid": event_uid,
        "visitor_id": visitor_id,
        "event_type": event.event_type.value,
        "timestamp": timestamp,
        "frame_number": event.frame_number,
        "timestamp_ms": event.timestamp_ms,
        "zone_id": event.zone_id,
        "zone_name": event.zone_name,
        "confidence": event.confidence,
        "metadata": event.metadata,
    }


def _fingerprint_event(
    visitor_id: str,
    event_type: str,
    timestamp: str,
    frame_number: int | None,
    timestamp_ms: float | None,
    zone_id: str | None,
) -> str:
    payload = {
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "frame_number": frame_number,
        "timestamp_ms": timestamp_ms,
        "zone_id": zone_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_event_model(item: dict[str, Any]) -> EventModel:
    metadata_json = json.dumps(item["metadata"], sort_keys=True)
    return EventModel(
        event_uid=item["event_uid"],
        visitor_id=item["visitor_id"],
        event_type=item["event_type"],
        timestamp=item["timestamp"],
        frame_number=item["frame_number"],
        timestamp_ms=item["timestamp_ms"],
        zone_id=item["zone_id"],
        zone_name=item["zone_name"],
        confidence=item["confidence"],
        metadata_json=metadata_json,
    )


app = FastAPI(
    title="Store Intelligence Ingestion API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.include_router(router)
