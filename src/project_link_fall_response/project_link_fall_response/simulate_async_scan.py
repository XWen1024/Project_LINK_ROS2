#!/usr/bin/env python3
"""Replay still images through the no-motion asynchronous scan decision engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .async_scan import AsyncScanOrchestrator
from .fall_models import SpecializedFallDetector, YoloWorldPersonDetector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="one JPEG replayed at every simulated angle")
    parser.add_argument(
        "--angles-dir",
        help="directory containing 0.jpg, 30.jpg, ... 330.jpg; missing files are errors",
    )
    parser.add_argument(
        "--fall-model",
        default="/home/wte/models/project_link/human-fall-detection-yolo11.pt",
    )
    parser.add_argument("--world-model", default="/home/wte/models/yolov8s-worldv2.pt")
    parser.add_argument("--device", default="0")
    parser.add_argument("--strong", type=float, default=0.60)
    parser.add_argument("--weak", type=float, default=0.25)
    parser.add_argument(
        "--angle-delay",
        type=float,
        default=1.0,
        help="simulated travel/settle time between headings",
    )
    args = parser.parse_args()
    if bool(args.image) == bool(args.angles_dir):
        parser.error("provide exactly one of --image or --angles-dir")

    angles = tuple(range(0, 360, 30))
    common = Path(args.image).expanduser().read_bytes() if args.image else None

    def frame_for(angle: float) -> bytes:
        if common is not None:
            return common
        return (Path(args.angles_dir).expanduser() / f"{int(angle)}.jpg").read_bytes()

    fall = SpecializedFallDetector(args.fall_model, threshold=0.05, device=args.device)
    people = YoloWorldPersonDetector(args.world_model, threshold=0.50, device=args.device)
    fall.warmup()
    scan = AsyncScanOrchestrator(
        angles=angles,
        strong_threshold=args.strong,
        weak_threshold=args.weak,
        simulated_angle_delay_sec=max(0.0, args.angle_delay),
    )
    feedback_rows = []
    outcome = scan.run(
        capture=lambda angle, count, _stage, _step, _total: [frame_for(angle)] * count,
        infer_fall=fall.assess,
        infer_people=people.assess,
        cancelled=lambda: False,
        feedback=lambda stage, message, step, total, confidence: feedback_rows.append(
            {
                "stage": stage,
                "message": message,
                "step": step,
                "total": total,
                "confidence": confidence,
            }
        ),
    )
    print(
        json.dumps(
            {
                "kind": outcome.kind,
                "confidence": outcome.confidence,
                "angle_deg": outcome.angle_deg,
                "angles_completed": outcome.angles_completed,
                "vlm_image_count": len(outcome.vlm_images),
                "reason": outcome.reason,
                "feedback": feedback_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
