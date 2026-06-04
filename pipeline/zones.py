from __future__ import annotations

from enum import StrEnum
from typing import Iterable

import cv2
import numpy as np
from pydantic import BaseModel, Field, field_validator

from pipeline.tracker import BoundingBox, TrackedDetection


class ZoneType(StrEnum):
    """Supported semantic zone categories for event generation."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    GENERAL = "GENERAL"


class Point(BaseModel):
    """Pixel coordinate in the video frame."""

    x: float = Field(..., ge=0)
    y: float = Field(..., ge=0)


class ZoneDefinition(BaseModel):
    """Polygon-backed store zone definition."""

    zone_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    zone_type: ZoneType = ZoneType.GENERAL
    polygon: list[Point] = Field(..., min_length=3)

    @field_validator("polygon")
    @classmethod
    def polygon_must_have_area(cls, polygon: list[Point]) -> list[Point]:
        coordinates = np.array([(point.x, point.y) for point in polygon], dtype=np.float32)
        if abs(cv2.contourArea(coordinates)) <= 0:
            raise ValueError("Zone polygon must have a non-zero area.")
        return polygon

    def contains(self, point: Point) -> bool:
        contour = np.array([(p.x, p.y) for p in self.polygon], dtype=np.float32)
        return cv2.pointPolygonTest(contour, (float(point.x), float(point.y)), False) >= 0


class ZoneAssignment(BaseModel):
    """Result of assigning a detection to a configured zone."""

    detection: TrackedDetection
    anchor_point: Point
    zone: ZoneDefinition | None


def bbox_bottom_center(bbox: BoundingBox) -> Point:
    """Use the bottom-center of a person box as the floor-position proxy."""

    return Point(
        x=(bbox.x1 + bbox.x2) / 2,
        y=bbox.y2,
    )


class ZoneMapper:
    """Assigns tracked detections to the first polygon containing their anchor point."""

    def __init__(self, zones: Iterable[ZoneDefinition]) -> None:
        self.zones = list(zones)
        if not self.zones:
            raise ValueError("At least one zone must be configured.")

    def assign_detection(self, detection: TrackedDetection) -> ZoneAssignment:
        anchor_point = bbox_bottom_center(detection.bbox)
        assigned_zone = next(
            (zone for zone in self.zones if zone.contains(anchor_point)),
            None,
        )
        return ZoneAssignment(
            detection=detection,
            anchor_point=anchor_point,
            zone=assigned_zone,
        )

    def assign_detections(
        self,
        detections: Iterable[TrackedDetection],
    ) -> list[ZoneAssignment]:
        return [self.assign_detection(detection) for detection in detections]


def default_store_zones(frame_width: int = 1920, frame_height: int = 1080) -> list[ZoneDefinition]:
    """Provide conservative default zones for quick validation on full-HD CCTV footage."""

    return [
        ZoneDefinition(
            zone_id="entry",
            name="Entry",
            zone_type=ZoneType.ENTRY,
            polygon=[
                Point(x=0, y=0),
                Point(x=frame_width * 0.22, y=0),
                Point(x=frame_width * 0.22, y=frame_height),
                Point(x=0, y=frame_height),
            ],
        ),
        ZoneDefinition(
            zone_id="shopping_area",
            name="Shopping Area",
            zone_type=ZoneType.GENERAL,
            polygon=[
                Point(x=frame_width * 0.22, y=0),
                Point(x=frame_width * 0.78, y=0),
                Point(x=frame_width * 0.78, y=frame_height),
                Point(x=frame_width * 0.22, y=frame_height),
            ],
        ),
        ZoneDefinition(
            zone_id="exit",
            name="Exit",
            zone_type=ZoneType.EXIT,
            polygon=[
                Point(x=frame_width * 0.78, y=0),
                Point(x=frame_width, y=0),
                Point(x=frame_width, y=frame_height),
                Point(x=frame_width * 0.78, y=frame_height),
            ],
        ),
    ]
