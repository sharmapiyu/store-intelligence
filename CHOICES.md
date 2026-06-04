# Engineering Choices

## AI-Assisted Decisions

AI was used as a coding accelerator, not as a source of business truth. The useful AI-assisted decisions were:

- Keep analytics computed from stored events only.
- Use a single ingestion path instead of letting each metric write its own state.
- Split detection, zone mapping, event generation, persistence, API, and dashboard into separate modules.
- Add idempotency early through `event_uid`, because camera/video pipelines often retry batches.
- Make the funnel session-based rather than visitor-based.

Every metric rule was then encoded directly in code and backed by tests.

## Model Selection

YOLOv8n is the detector in this version.

Reasons:

- small model size
- quick startup
- available through Ultralytics
- good enough for person detection on CCTV footage
- works with ByteTrack in the same tooling path

Tradeoff:

- YOLOv8n is not the most accurate detector.
- It can miss partially occluded people or distant shoppers.
- For production, I would evaluate YOLOv8s/m or a fine-tuned person detector on store CCTV frames.

ByteTrack is used for tracking because stable track IDs matter more here than detecting every single frame perfectly. Dwell time, re-entry, and funnel sessions all depend on identity continuity.

## SQLite Choice

SQLite was chosen because the challenge explicitly required it and because it makes local Docker deployment easy.

Tradeoff:

- SQLite is not ideal for high-concurrency writes from many cameras.
- File-backed SQLite can be sensitive on synced folders such as OneDrive.

Production improvement:

- Move to PostgreSQL.
- Keep SQLAlchemy models and repository boundaries.
- Add Alembic migrations.

## FastAPI Choice

FastAPI fits this challenge because:

- request validation is first-class through Pydantic
- OpenAPI docs come for free
- testing with `TestClient` is straightforward
- the service is small and endpoint-oriented

The API is intentionally direct. There is no message broker or async worker yet because the challenge asks for a working production-oriented baseline, not a distributed system.

## API Design Rationale

`POST /events/ingest` accepts batches because frame-level event pipelines naturally produce bursts. The limit of 500 events keeps payloads bounded and failure responses understandable.

The read APIs are separated by business question:

- `/metrics` for store KPIs
- `/funnel` for session progression
- `/heatmap` for zone attention
- `/anomalies` for operational alerts
- `/health` for feed freshness

That separation makes each endpoint easier to test and reason about.

## Deduplication and Idempotency

The ingestion service accepts `event_id` as a producer-supplied idempotency key. If absent, it fingerprints stable event fields.

This is important because video processing jobs are often restarted or retried. Retrying a batch should not double the visitor counts.

Tradeoff:

- Fingerprinting can treat a corrected event as a duplicate if the stable fields match.
- In production I would require producer-side event IDs and keep correction events explicit.

## Funnel Tradeoffs

The funnel engine supports explicit `session_id` and inferred sessions.

Inference rule:

- first event starts a session
- `REENTRY` starts a new session
- `EXIT` closes the current session

This is practical for CCTV-derived data where a separate sessionization worker may not exist yet.

Tradeoff:

- Inference is only as good as entry/exit/re-entry events.
- If a track ID changes mid-visit, the system may split sessions.

Future improvement:

- Add a dedicated sessionization service with inactivity windows and camera handoff logic.

## Staff Exclusion

Staff are excluded using metadata:

```text
is_staff: true
person_type: "staff"
role: "staff"
```

This is intentionally metadata-driven because the detector currently sees people, not job roles.

Future improvement:

- integrate staff schedules, staff device tags, uniform classifier, or manual staff zone rules.

## Heatmap Scoring

The heatmap score combines visit count and dwell:

```text
raw_score = visit_count * (1 + avg_dwell_seconds)
normalized_score = raw_score / max_zone_raw_score
```

This favors zones that are both visited and held attention.

Tradeoff:

- A very long dwell can dominate a low-traffic zone.

Future improvement:

- cap dwell contribution or use percentile-normalized dwell.

## Anomaly Thresholds

Thresholds are configurable because stores are different. A queue depth of 5 may be normal in one store and severe in another.

Current anomalies are intentionally simple and explainable:

- queue too deep
- conversion too low
- zone not visited

Future improvement:

- learn baselines per store, weekday, hour, and campaign period.

## Dockerization

One image is used for both API and dashboard. Compose runs different commands for each service.

Tradeoff:

- The dashboard image includes API dependencies and vice versa.

For this challenge, one image keeps deployment simple. For production, I would split API, dashboard, and pipeline worker images.

## Known Limitations

- Default zones are placeholders and should be replaced with calibrated store polygons.
- SQLite is not the long-term database for high-volume multi-camera production.
- The current pipeline is file-oriented; a production camera system would need stream workers.
- The dashboard assumes the API is already reachable.
- Event metadata is flexible JSON, which is useful now but should be formalized as volume grows.

## Future Improvements

- Alembic migrations.
- PostgreSQL deployment profile.
- Camera and zone management endpoints.
- Background video worker API.
- Store-specific polygon configuration files.
- Session timeout service.
- Per-hour and per-day trend APIs.
- Model accuracy evaluation on labeled CCTV frames.
- Role-aware staff exclusion.
- Authentication and API keys.
- Structured logging and request IDs.
- Prometheus metrics for API and pipeline health.

