from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient

from app.ingestion import app


def test_ingest_events_is_idempotent() -> None:
    payload = {
        "events": [
            {
                "event_id": "evt-1",
                "event_type": "ENTRY",
                "visitor_id": "visitor-1",
                "timestamp": "2026-05-31T10:00:00Z",
                "frame_number": 1,
                "timestamp_ms": 33.3,
                "zone_id": "entry",
                "zone_name": "Entry",
                "confidence": 0.91,
                "metadata": {"camera": "cam-1"},
            },
            {
                "event_id": "evt-2",
                "event_type": "ZONE_ENTER",
                "track_id": 2,
                "occurred_at": "2026-05-31T10:00:01Z",
                "frame_number": 2,
                "timestamp_ms": 66.6,
                "zone_id": "shopping",
                "zone_name": "Shopping",
                "confidence": 0.87,
            },
        ]
    }

    with TestClient(app) as client:
        first_response = client.post("/events/ingest", json=payload)
        second_response = client.post("/events/ingest", json=payload)

    assert first_response.status_code == 200
    assert first_response.json()["accepted"] == 2
    assert first_response.json()["duplicates"] == 0

    assert second_response.status_code == 200
    assert second_response.json()["accepted"] == 0
    assert second_response.json()["duplicates"] == 2


def test_ingest_events_returns_structured_validation_error() -> None:
    with TestClient(app) as client:
        response = client.post("/events/ingest", json={"events": []})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"]


def test_ingest_events_rejects_batches_larger_than_500() -> None:
    event = {
        "event_type": "ENTRY",
        "visitor_id": "visitor-1",
        "timestamp": "2026-05-31T10:00:00Z",
    }
    payload = {"events": [event for _ in range(501)]}

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
