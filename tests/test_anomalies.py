from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.anomalies import app
from conftest import event, transaction


def test_anomaly_detection_returns_supported_anomalies(db_session) -> None:
    start = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    rows = []
    for index in range(6):
        rows.append(event(f"a-entry-{index}", f"visitor-{index}", "ENTRY", start + timedelta(seconds=index)))
    for index in range(3):
        rows.append(event(f"a-queue-{index}", f"visitor-{index}", "CHECKOUT_ENTER", start + timedelta(minutes=1, seconds=index)))
    for index in range(10):
        rows.append(event(f"a-zone-{index}", f"zone-visitor-{index}", "ZONE_ENTER", start + timedelta(minutes=2, seconds=index), zone_id="aisle", zone_name="Aisle"))
    rows.append(event("a-dead", "visitor-1", "ZONE_DWELL", start + timedelta(minutes=3), zone_id="corner", zone_name="Corner", dwell_time_ms=500))
    db_session.add_all(rows)
    db_session.add(transaction("txn-a1", "visitor-0", start + timedelta(minutes=4)))
    db_session.commit()

    with TestClient(app) as client:
        response = client.get(
            "/stores/store-1/anomalies",
            params={
                "queue_spike_depth": 3,
                "conversion_drop_rate": 0.5,
                "conversion_min_visitors": 5,
                "dead_zone_visit_count": 0,
                "dead_zone_min_store_visits": 5,
            },
        )

    assert response.status_code == 200
    anomaly_types = {item["anomaly_type"] for item in response.json()["anomalies"]}
    assert anomaly_types == {"QUEUE_SPIKE", "CONVERSION_DROP", "DEAD_ZONE"}


def test_anomaly_detection_empty_store_has_no_anomalies(db_session) -> None:
    with TestClient(app) as client:
        response = client.get("/stores/empty-store/anomalies")

    assert response.status_code == 200
    assert response.json()["anomalies"] == []
