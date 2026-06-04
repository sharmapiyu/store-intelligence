from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2

from pipeline.tracker import PersonTracker, TrackedDetection, validate_model_path


def iter_video_frames(video_path: str) -> Iterator[tuple[int, float, Any]]:
    """Yield frame number, timestamp in milliseconds, and OpenCV frame."""

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise ValueError(f"Unable to open video source: {video_path}")

    frame_number = 0
    try:
        while True:
            success, frame = capture.read()
            if not success:
                break

            timestamp_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
            yield frame_number, float(timestamp_ms), frame
            frame_number += 1
    finally:
        capture.release()


def detect_and_track_video(
    video_path: str,
    model_path: str = "yolov8n.pt",
    tracker_config: str = "bytetrack.yaml",
    confidence_threshold: float = 0.25,
    image_size: int = 640,
    device: str | None = None,
    max_frames: int | None = None,
) -> list[TrackedDetection]:
    """Run person detection and tracking on a video source."""

    validate_model_path(model_path)
    tracker = PersonTracker(
        model_path=model_path,
        tracker_config=tracker_config,
        confidence_threshold=confidence_threshold,
        image_size=image_size,
        device=device,
    )

    detections: list[TrackedDetection] = []
    for index, (frame_number, timestamp_ms, frame) in enumerate(iter_video_frames(video_path)):
        if max_frames is not None and index >= max_frames:
            break

        detections.extend(
            tracker.track_frame(
                frame=frame,
                frame_number=frame_number,
                timestamp_ms=timestamp_ms,
            )
        )

    return detections


def write_detections_json(
    detections: list[TrackedDetection],
    output_path: str,
) -> None:
    """Persist structured detections as JSON for inspection or later ingestion."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = [detection.to_dict() for detection in detections]
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect and track people in a video using YOLOv8n and ByteTrack."
    )
    parser.add_argument("--video", required=True, help="Path to the input video file.")
    parser.add_argument(
        "--output",
        default="detections.json",
        help="Path where structured detection JSON will be written.",
    )
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="YOLO model path or Ultralytics model name.",
    )
    parser.add_argument(
        "--tracker",
        default="bytetrack.yaml",
        help="ByteTrack tracker config path or Ultralytics tracker config name.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.25,
        help="Minimum person detection confidence.",
    )
    parser.add_argument(
        "--image-size",
        type=int,
        default=640,
        help="YOLO inference image size.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Inference device, for example 'cpu', '0', or 'cuda:0'.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional frame limit for validation runs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    detections = detect_and_track_video(
        video_path=args.video,
        model_path=args.model,
        tracker_config=args.tracker,
        confidence_threshold=args.confidence,
        image_size=args.image_size,
        device=args.device,
        max_frames=args.max_frames,
    )
    write_detections_json(detections=detections, output_path=args.output)
    print(
        f"Wrote {len(detections)} tracked person detections to {args.output}"
    )


if __name__ == "__main__":
    main()
