from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.health import app as health_app
from app.heatmap import app as heatmap_app
from conftest import event


def test_heatmap_excludes_staff_and_normalizes_scores(db_session) -> None:
    start = datetime(2026, 5, 31, 13, 0, tzinfo=UTC)
    db_session.add_all(
        [
            event("h1", "visitor-1", "ZONE_ENTER", start, zone_id="aisle", zone_name="Aisle"),
            event("h2", "visitor-1", "ZONE_DWELL", start + timedelta(seconds=1), zone_id="aisle", zone_name="Aisle", dwell_time_ms=2000),
            event("h3", "visitor-2", "ZONE_ENTER", start + timedelta(seconds=2), zone_id="checkout", zone_name="Checkout"),
            event("h4", "visitor-3", "ZONE_ENTER", start + timedelta(seconds=3), zone_id="checkout", zone_name="Checkout"),
            event("h5", "staff-1", "ZONE_ENTER", start + timedelta(seconds=4), zone_id="aisle", zone_name="Aisle", is_staff=True),
        ]
    )
    db_session.commit()

    with TestClient(heatmap_app) as client:
        response = client.get("/stores/store-1/heatmap")

    assert response.status_code == 200
    zones = {item["zone"]: item for item in response.json()["zones"]}
    assert zones["Aisle"]["visit_count"] == 1
    assert zones["Aisle"]["avg_dwell"] == 2000.0
    assert zones["Aisle"]["normalized_score"] == 1.0
    assert zones["Checkout"]["visit_count"] == 2


def test_health_detects_stale_feed(db_session) -> None:
    now = datetime.now(tz=UTC)
    db_session.add_all(
        [
            event("health-fresh", "visitor-1", "ENTRY", now - timedelta(seconds=10), store_id="fresh-store"),
            event("health-stale", "visitor-2", "ENTRY", now - timedelta(seconds=1000), store_id="stale-store"),
        ]
    )
    db_session.commit()

    with TestClient(health_app) as client:
        response = client.get("/health?stale_after_seconds=300")

    assert response.status_code == 200
    statuses = {item["store_id"]: item["status"] for item in response.json()["store_status"]}
    assert statuses["fresh-store"] == "ACTIVE"
    assert statuses["stale-store"] == "STALE_FEED"
