from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


PERSON_CLASS_ID = 0


@dataclass(frozen=True)
class BoundingBox:
    """Pixel-space bounding box in x1, y1, x2, y2 format."""

    x1: float
    y1: float
    x2: float
    y2: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class TrackedDetection:
    """Structured output for one tracked person in one video frame."""

    frame_number: int
    timestamp_ms: float
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    bbox: BoundingBox

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bbox"] = self.bbox.to_dict()
        return data


class PersonTracker:
    """Runs YOLOv8 person detection with ByteTrack identity assignment."""

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        tracker_config: str = "bytetrack.yaml",
        confidence_threshold: float = 0.25,
        image_size: int = 640,
        device: str | None = None,
    ) -> None:
        self.model_path = model_path
        self.tracker_config = tracker_config
        self.confidence_threshold = confidence_threshold
        self.image_size = image_size
        self.device = device
        from ultralytics import YOLO

        self.model = YOLO(model_path)

    def track_frame(
        self,
        frame: Any,
        frame_number: int,
        timestamp_ms: float,
    ) -> list[TrackedDetection]:
        """Detect and track people in a single OpenCV frame."""

        results = self.model.track(
            source=frame,
            persist=True,
            tracker=self.tracker_config,
            classes=[PERSON_CLASS_ID],
            conf=self.confidence_threshold,
            imgsz=self.image_size,
            device=self.device,
            verbose=False,
        )

        if not results:
            return []

        result = results[0]
        boxes = result.boxes
        if boxes is None or boxes.id is None:
            return []

        detections: list[TrackedDetection] = []
        xyxy_values = boxes.xyxy.cpu().tolist()
        confidence_values = boxes.conf.cpu().tolist()
        class_values = boxes.cls.cpu().tolist()
        track_id_values = boxes.id.cpu().tolist()

        for bbox_values, confidence, class_id, track_id in zip(
            xyxy_values,
            confidence_values,
            class_values,
            track_id_values,
            strict=True,
        ):
            class_id_int = int(class_id)
            if class_id_int != PERSON_CLASS_ID:
                continue

            x1, y1, x2, y2 = bbox_values
            detections.append(
                TrackedDetection(
                    frame_number=frame_number,
                    timestamp_ms=timestamp_ms,
                    track_id=int(track_id),
                    class_id=class_id_int,
                    class_name="person",
                    confidence=float(confidence),
                    bbox=BoundingBox(
                        x1=float(x1),
                        y1=float(y1),
                        x2=float(x2),
                        y2=float(y2),
                    ),
                )
            )

        return detections

    def track_frames(
        self,
        frames: Iterable[tuple[int, float, Any]],
    ) -> Iterable[list[TrackedDetection]]:
        """Track a stream of frames represented as frame number, timestamp, frame."""

        for frame_number, timestamp_ms, frame in frames:
            yield self.track_frame(
                frame=frame,
                frame_number=frame_number,
                timestamp_ms=timestamp_ms,
            )


def validate_model_path(model_path: str) -> None:
    """Validate local custom model paths while allowing Ultralytics model names."""

    looks_like_file = Path(model_path).suffix in {".pt", ".onnx", ".engine"}
    is_builtin_name = model_path == "yolov8n.pt"
    if looks_like_file and not is_builtin_name and not Path(model_path).exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
