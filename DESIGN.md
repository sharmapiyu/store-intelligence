# Design

## System Goal

The system answers practical store-operations questions from CCTV-derived events:

- How many visitors are active?
- Which zones are getting attention?
- Where are customers dropping out of the funnel?
- Is the queue backing up?
- Is a feed stale?

The design intentionally separates computer vision from business analytics. Detection produces structured observations. Zone mapping turns observations into events. The API persists events. Metrics, funnel, heatmap, health, and anomalies read only persisted state.

## Architecture

```text
Video file / CCTV stream
  -> OpenCV frame reader
  -> YOLOv8n person detector
  -> ByteTrack person tracker
  -> polygon zone mapper
  -> event engine
  -> POST /events/ingest
  -> SQLite events, sessions, transactions
  -> metrics/funnel/heatmap/anomaly/health APIs
  -> Streamlit dashboard
```

The `pipeline` package can run offline against video files. The `app` package is the FastAPI service. The `dashboard` package is an API consumer only.

## Detection Layer

YOLOv8n was selected because it is small enough for a challenge environment and still accurate enough for person detection in retail CCTV. The system filters to COCO class `person`, then passes detections to ByteTrack through Ultralytics tracking support.

ByteTrack was chosen because store analytics needs stable identities more than perfect object classification. Track continuity is what allows dwell, re-entry, and funnel behavior to be computed later.

Each detection stores:

- `frame_number`
- `timestamp_ms`
- `track_id`
- `class_name`
- `confidence`
- bounding box

## Zone Mapping

Zones are polygons. The mapper uses the bottom-center of the person bounding box as a floor-position proxy and checks whether that point falls inside a zone polygon.

This is deliberately simple. It avoids camera calibration complexity while still being useful for the provided challenge footage. In production, the next step would be camera-specific homography calibration.

## Event Engine

The event engine emits:

- `ENTRY`
- `EXIT`
- `ZONE_ENTER`
- `ZONE_EXIT`
- `ZONE_DWELL`
- `REENTRY`

Events are validated with Pydantic before they are written as JSON or ingested. The API ingestion schema is a separate contract because API clients may provide events from other producers later.

## Database Design

SQLite tables:

```text
events
sessions
transactions
```

The important indexes are on `visitor_id`, `timestamp`, and the combined pair. `events.event_uid` is unique and is used for deduplication.

No migrations are included because the challenge asked for automatic table creation. The models are still structured so Alembic could be added later without rewriting the persistence layer.

## API Design

The API has one write path and several read paths:

```text
POST /events/ingest
GET  /stores/{id}/metrics
GET  /stores/{id}/funnel
GET  /stores/{id}/heatmap
GET  /stores/{id}/anomalies
GET  /health
```

The ingestion endpoint is batch-based with a maximum of 500 events. This is large enough for efficient uploads but small enough to keep request failure handling clear.

Read endpoints compute results at request time from stored rows. That keeps the implementation explainable for the challenge and avoids hidden background aggregation state.

## Funnel Design

The funnel is session-based and re-entry aware.

Stages:

```text
ENTRY -> ZONE_VISIT -> BILLING -> PURCHASE
```

Rules:

- A session counts a stage once, no matter how many repeated events occur.
- `REENTRY` starts a new inferred session.
- Explicit `session_id` wins when present.
- Transactions can mark `PURCHASE`.
- Staff are excluded.

This matters because visitor-level counting would undercount re-entry behavior and event-level counting would overcount repeated movement.

## Anomaly Design

Supported anomalies:

- `QUEUE_SPIKE`
- `CONVERSION_DROP`
- `DEAD_ZONE`

Thresholds are configurable by query parameters and environment defaults. That makes the endpoint usable during demos without rebuilding images.

## Health Design

`GET /health` reads the latest event timestamp overall and per store. A store becomes `STALE_FEED` when its latest event is older than the configured freshness window.

This is intentionally based on event freshness, not process liveness. A process can be alive while a camera feed is dead; the challenge needs the second signal.

## Dashboard Design

The Streamlit dashboard calls the API, not SQLite. It displays:

- live visitors
- conversion rate
- top zones
- active anomalies
- feed health

It refreshes automatically using a small browser-side timer. This keeps the dashboard simple and avoids adding another scheduler.

