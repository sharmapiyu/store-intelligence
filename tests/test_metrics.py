from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.metrics import app
from conftest import event, transaction


def test_metrics_excludes_staff_and_handles_zero_purchases(db_session) -> None:
    start = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
    db_session.add_all(
        [
            event("m1", "visitor-1", "ENTRY", start),
            event("m2", "visitor-1", "ZONE_DWELL", start + timedelta(seconds=5), zone_id="aisle", zone_name="Aisle", dwell_time_ms=1500),
            event("m3", "visitor-2", "ENTRY", start + timedelta(seconds=10)),
            event("m4", "staff-1", "ENTRY", start + timedelta(seconds=20), is_staff=True),
            event("m5", "staff-1", "ZONE_DWELL", start + timedelta(seconds=25), zone_id="aisle", zone_name="Aisle", dwell_time_ms=9999, is_staff=True),
        ]
    )
    db_session.commit()

    with TestClient(app) as client:
        response = client.get("/stores/store-1/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["unique_visitors"] == 2
    assert body["conversion_rate"] == 0.0
    assert body["abandonment_rate"] == 1.0
    assert body["avg_dwell_per_zone"][0]["avg_dwell_ms"] == 1500.0


def test_metrics_empty_store_returns_zeroes(db_session) -> None:
    with TestClient(app) as client:
        response = client.get("/stores/missing-store/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["avg_dwell_per_zone"] == []
    assert body["queue_depth"] == 0
    assert body["abandonment_rate"] == 0.0


def test_metrics_counts_conversion_from_transactions(db_session) -> None:
    start = datetime(2026, 5, 31, 11, 0, tzinfo=UTC)
    db_session.add_all(
        [
            event("m6", "visitor-1", "ENTRY", start),
            event("m7", "visitor-2", "ENTRY", start + timedelta(seconds=1)),
            transaction("txn-m1", "visitor-1", start + timedelta(minutes=1)),
        ]
    )
    db_session.commit()

    with TestClient(app) as client:
        response = client.get("/stores/store-1/metrics")

    assert response.status_code == 200
    assert response.json()["conversion_rate"] == 0.5
