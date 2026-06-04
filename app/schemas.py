from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiErrorCode(StrEnum):
    """Stable error codes returned by the ingestion API."""

    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"


class EventType(StrEnum):
    """Store event names accepted by the ingestion endpoint."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    REENTRY = "REENTRY"


class EventIngestItem(BaseModel):
    """Single event payload accepted by POST /events/ingest."""

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional producer-side idempotency key.",
    )
    event_type: EventType
    visitor_id: str | None = Field(default=None, min_length=1, max_length=128)
    track_id: int | None = Field(default=None, ge=0)
    timestamp: datetime | None = None
    occurred_at: datetime | None = None
    frame_number: int | None = Field(default=None, ge=0)
    timestamp_ms: float | None = Field(default=None, ge=0)
    zone_id: str | None = Field(default=None, max_length=128)
    zone_name: str | None = Field(default=None, max_length=255)
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("visitor_id")
    @classmethod
    def visitor_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("visitor_id must not be blank.")
        return value

    def resolved_visitor_id(self) -> str:
        if self.visitor_id:
            return self.visitor_id
        if self.track_id is not None:
            return str(self.track_id)
        raise ValueError("Either visitor_id or track_id is required.")

    def resolved_timestamp(self) -> datetime:
        timestamp = self.timestamp or self.occurred_at
        if timestamp is None:
            raise ValueError("Either timestamp or occurred_at is required.")
        return timestamp


class EventBatchIngestRequest(BaseModel):
    """Batch request body for event ingestion."""

    model_config = ConfigDict(extra="forbid")

    events: list[EventIngestItem] = Field(..., min_length=1, max_length=500)


class EventIngestResult(BaseModel):
    """Per-event ingestion result."""

    event_uid: str
    visitor_id: str
    status: str
    database_id: int | None = None


class EventBatchIngestResponse(BaseModel):
    """Structured response for a batch ingestion call."""

    accepted: int
    duplicates: int
    total: int
    results: list[EventIngestResult]


class StructuredError(BaseModel):
    """Consistent API error response shape."""

    code: ApiErrorCode
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)
