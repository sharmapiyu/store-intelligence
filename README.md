# Store Intelligence System

This project is a production-oriented retail analytics service built for the Purplle store intelligence challenge. It takes CCTV footage through a detection pipeline, converts tracked people into zone events, persists those events, and exposes operational metrics through FastAPI and a Streamlit dashboard.

The important rule in this build is simple: metrics are computed from stored events. The API does not return hardcoded business outputs, and the dashboard does not read the database directly.

## What Is Included

```text
pipeline/
  detect.py       YOLOv8n + ByteTrack video detection CLI
  tracker.py      person tracking wrapper and structured detection models
  zones.py        polygon-based zone definitions and assignment
  events.py       detection-to-event conversion

app/
  main.py         combined FastAPI application
  ingestion.py    POST /events/ingest
  metrics.py      GET /stores/{id}/metrics
  funnel.py       GET /stores/{id}/funnel
  heatmap.py      GET /stores/{id}/heatmap
  anomalies.py    GET /stores/{id}/anomalies
  health.py       GET /health
  database.py     SQLAlchemy SQLite setup
  models.py       events, sessions, transactions tables
  schemas.py      ingestion request/response models

dashboard/
  streamlit_app.py
  api_client.py

tests/
  pytest suite for ingestion, metrics, funnel, heatmap, health, and anomalies
```

## Run With Docker Compose

Start Docker Desktop first, then run:

```bash
cd store-intelligence
docker compose up --build
```

Services:

```text
FastAPI:   http://localhost:8000
Dashboard: http://localhost:8501
SQLite:    /app/data/store_intelligence.db inside the api container volume
```

The API creates SQLite tables automatically on startup. No migration step is needed for this challenge version.

## Local Development

```bash
cd store-intelligence
pip install -r requirements.txt
set DATABASE_URL=sqlite:///./data/store_intelligence.db
uvicorn app.main:app --reload
```

Dashboard:

```bash
set API_BASE_URL=http://localhost:8000
set STORE_ID=store-1
streamlit run dashboard/streamlit_app.py
```

## Process A Video

Example:

```bash
python -m pipeline.detect ^
  --video "C:\path\to\CAM 1.mp4" ^
  --output detections.json ^
  --max-frames 100
```

Convert detections into zone events:

```bash
python -m pipeline.events --detections detections.json --output events.json
```

The default zones are conservative full-HD bands: entry on the left, shopping in the middle, exit on the right. In a real store rollout these polygons should be replaced with camera-specific calibrated zones.

## Ingest Events

```bash
curl -X POST http://localhost:8000/events/ingest ^
  -H "Content-Type: application/json" ^
  -d "{\"events\":[{\"event_id\":\"evt-1\",\"event_type\":\"ENTRY\",\"visitor_id\":\"v1\",\"timestamp\":\"2026-05-31T10:00:00Z\",\"metadata\":{\"store_id\":\"store-1\"}}]}"
```

The ingestion endpoint accepts batches up to 500 events. `event_id` is used as the idempotency key when supplied. If it is not supplied, the service fingerprints the stable event fields.

## API Endpoints

```text
GET  /health
POST /events/ingest
GET  /stores/{id}/metrics
GET  /stores/{id}/funnel
GET  /stores/{id}/heatmap
GET  /stores/{id}/anomalies
```

## Verification Commands

```bash
python -B -m pytest -q tests -p no:cacheprovider
```

Coverage check:

```bash
set DATABASE_URL=sqlite:///:memory:
set COVERAGE_FILE=C:\tmp\store-intelligence.coverage
python -B -m pytest -q tests --cov=app --cov-report=term-missing --cov-fail-under=70 -p no:cacheprovider
```

Current validation result during build:

```text
12 tests passed
Total app coverage: 90.13%
```

## Assumptions

- `store_id` is stored in event and transaction `metadata_json`.
- Staff are excluded when metadata has `is_staff: true`, `person_type: "staff"`, or `role: "staff"`.
- Transaction rows represent completed purchases.
- Re-entry creates a new inferred visit session when explicit `session_id` is not present.
- SQLite is acceptable for the challenge and local deployment; PostgreSQL would be preferred for concurrent production writes.

