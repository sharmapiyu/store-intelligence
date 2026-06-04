from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pipeline.tracker import BoundingBox, TrackedDetection
from pipeline.zones import Point, ZoneAssignment, ZoneDefinition, ZoneMapper, ZoneType, default_store_zones


class EventType(StrEnum):
    """Store movement events produced from zone transitions."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    REENTRY = "REENTRY"


class StoreEvent(BaseModel):
    """Validated event schema emitted by the detection-to-event engine."""

    model_config = ConfigDict(use_enum_values=True)

    event_type: EventType
    track_id: int = Field(..., ge=0)
    frame_number: int = Field(..., ge=0)
    timestamp_ms: float = Field(..., ge=0)
    occurred_at: datetime
    zone_id: str | None = None
    zone_name: str | None = None
    zone_type: ZoneType | None = None
    confidence: float = Field(..., ge=0, le=1)
    bbox: dict[str, float]
    anchor_point: Point
    dwell_time_ms: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VisitorZoneState(BaseModel):
    """Internal per-track state used for transition and dwell calculations."""

    current_zone_id: str | None = None
    current_zone_name: str | None = None
    current_zone_type: ZoneType | None = None
    zone_entered_at_ms: float | None = None
    last_seen_ms: float = 0
    has_entered_store: bool = False
    has_exited_store: bool = False


class EventEngine:
    """Converts zone assignments into movement and dwell events."""

    def __init__(
        self,
        zone_mapper: ZoneMapper,
        video_started_at: datetime | None = None,
    ) -> None:
        self.zone_mapper = zone_mapper
        self.video_started_at = video_started_at or datetime.now(tz=UTC)
        self._states: dict[int, VisitorZoneState] = {}

    def process_detection(self, detection: TrackedDetection) -> list[StoreEvent]:
        assignment = self.zone_mapper.assign_detection(detection)
        state = self._states.setdefault(detection.track_id, VisitorZoneState())
        events = self._events_for_assignment(assignment=assignment, state=state)
        self._update_state(state=state, assignment=assignment)
        return events

    def process_detections(self, detections: list[TrackedDetection]) -> list[StoreEvent]:
        events: list[StoreEvent] = []
        for detection in sorted(detections, key=lambda item: (item.frame_number, item.track_id)):
            events.extend(self.process_detection(detection))
        return events

    def _events_for_assignment(
        self,
        assignment: ZoneAssignment,
        state: VisitorZoneState,
    ) -> list[StoreEvent]:
        detection = assignment.detection
        new_zone = assignment.zone
        old_zone_id = state.current_zone_id
        new_zone_id = new_zone.zone_id if new_zone else None

        events: list[StoreEvent] = []
        if old_zone_id == new_zone_id:
            if new_zone is not None:
                events.append(
                    self._build_event(
                        event_type=EventType.ZONE_DWELL,
                        assignment=assignment,
                        dwell_time_ms=self._dwell_time_ms(state, detection.timestamp_ms),
                    )
                )
            return events

        if old_zone_id is not None:
            events.append(
                self._build_event(
                    event_type=EventType.ZONE_EXIT,
                    assignment=assignment,
                    zone_id=old_zone_id,
                    zone_name=state.current_zone_name,
                    zone_type=state.current_zone_type,
                    dwell_time_ms=self._dwell_time_ms(state, detection.timestamp_ms),
                )
            )

        if new_zone is None:
            return events

        if new_zone.zone_type == ZoneType.ENTRY and state.has_exited_store:
            events.append(self._build_event(EventType.REENTRY, assignment))

        if new_zone.zone_type == ZoneType.ENTRY and not state.has_entered_store:
            events.append(self._build_event(EventType.ENTRY, assignment))

        events.append(self._build_event(EventType.ZONE_ENTER, assignment))

        if new_zone.zone_type == ZoneType.EXIT:
            events.append(self._build_event(EventType.EXIT, assignment))

        return events

    def _update_state(
        self,
        state: VisitorZoneState,
        assignment: ZoneAssignment,
    ) -> None:
        detection = assignment.detection
        zone = assignment.zone
        previous_zone_id = state.current_zone_id
        new_zone_id = zone.zone_id if zone else None

        if previous_zone_id != new_zone_id:
            state.zone_entered_at_ms = detection.timestamp_ms if zone else None

        state.current_zone_id = new_zone_id
        state.current_zone_name = zone.name if zone else None
        state.current_zone_type = zone.zone_type if zone else None
        state.last_seen_ms = detection.timestamp_ms

        if zone and zone.zone_type == ZoneType.ENTRY:
            state.has_entered_store = True
            state.has_exited_store = False
        elif zone and zone.zone_type == ZoneType.EXIT:
            state.has_exited_store = True

    def _build_event(
        self,
        event_type: EventType,
        assignment: ZoneAssignment,
        zone_id: str | None = None,
        zone_name: str | None = None,
        zone_type: ZoneType | None = None,
        dwell_time_ms: float | None = None,
    ) -> StoreEvent:
        detection = assignment.detection
        zone = assignment.zone
        return StoreEvent(
            event_type=event_type,
            track_id=detection.track_id,
            frame_number=detection.frame_number,
            timestamp_ms=detection.timestamp_ms,
            occurred_at=self.video_started_at + timedelta(milliseconds=detection.timestamp_ms),
            zone_id=zone_id if zone_id is not None else zone.zone_id if zone else None,
            zone_name=zone_name if zone_name is not None else zone.name if zone else None,
            zone_type=zone_type if zone_type is not None else zone.zone_type if zone else None,
            confidence=detection.confidence,
            bbox=detection.bbox.to_dict(),
            anchor_point=assignment.anchor_point,
            dwell_time_ms=dwell_time_ms,
            metadata={
                "source": "detection_pipeline",
                "class_id": detection.class_id,
                "class_name": detection.class_name,
            },
        )

    @staticmethod
    def _dwell_time_ms(state: VisitorZoneState, current_timestamp_ms: float) -> float:
        if state.zone_entered_at_ms is None:
            return 0.0
        return max(0.0, current_timestamp_ms - state.zone_entered_at_ms)


def detection_from_dict(payload: dict[str, Any]) -> TrackedDetection:
    bbox_payload = payload["bbox"]
    return TrackedDetection(
        frame_number=int(payload["frame_number"]),
        timestamp_ms=float(payload["timestamp_ms"]),
        track_id=int(payload["track_id"]),
        class_id=int(payload["class_id"]),
        class_name=str(payload["class_name"]),
        confidence=float(payload["confidence"]),
        bbox=BoundingBox(
            x1=float(bbox_payload["x1"]),
            y1=float(bbox_payload["y1"]),
            x2=float(bbox_payload["x2"]),
            y2=float(bbox_payload["y2"]),
        ),
    )


def load_detections_json(path: str) -> list[TrackedDetection]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [detection_from_dict(item) for item in payload]


def load_zones_json(path: str) -> list[ZoneDefinition]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ZoneDefinition.model_validate(item) for item in payload]


def write_events_json(events: list[StoreEvent], output_path: str) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = [event.model_dump(mode="json") for event in events]
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert tracked detections into validated zone events."
    )
    parser.add_argument("--detections", required=True, help="Path to detection JSON.")
    parser.add_argument("--output", default="events.json", help="Path for event JSON output.")
    parser.add_argument(
        "--zones",
        default=None,
        help="Optional path to zone definition JSON. Defaults to built-in full-HD zones.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    detections = load_detections_json(args.detections)
    zones = load_zones_json(args.zones) if args.zones else default_store_zones()
    engine = EventEngine(zone_mapper=ZoneMapper(zones))
    events = engine.process_detections(detections)
    write_events_json(events=events, output_path=args.output)
    print(f"Wrote {len(events)} validated zone events to {args.output}")


if __name__ == "__main__":
    main()
