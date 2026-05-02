#!/usr/bin/env python3
"""
calibrate.py — One-shot helper to find the correct ROI coordinates.

Usage:
    python calibrate.py [--config config.yaml] [--camera 0]

Outputs:
    calibration_frame.jpg  — raw captured frame
    calibration_grid.jpg   — same frame with a 50 px grid and ROI overlay

Read the pixel coordinates from the images and paste them into config.yaml.
"""

import argparse
import sys
import os

try:
    import cv2
except ImportError:
    sys.exit("opencv-python is not installed. Run: pip install opencv-python")

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is not installed. Run: pip install PyYAML")


def capture_frame(camera_index: int, width: int, height: int) -> "cv2.Mat":
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        sys.exit(f"Cannot open camera index {camera_index}. "
                 "Check 'camera.index' in config.yaml or use --camera.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    # Discard a few frames so the sensor has time to adjust
    for _ in range(5):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        sys.exit("Failed to capture a frame from the camera.")

    return frame


def draw_grid(img, step: int = 50) -> "cv2.Mat":
    out = img.copy()
    h, w = out.shape[:2]
    color = (0, 255, 0)
    thickness = 1

    for x in range(0, w, step):
        cv2.line(out, (x, 0), (x, h), color, thickness)
        if x > 0:
            cv2.putText(out, str(x), (x + 2, 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    for y in range(0, h, step):
        cv2.line(out, (0, y), (w, y), color, thickness)
        if y > 0:
            cv2.putText(out, str(y), (2, y + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    return out


def draw_roi(img, roi: dict) -> "cv2.Mat":
    out = img.copy()
    x, y, w, h = roi["x"], roi["y"], roi["width"], roi["height"]
    cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 2)
    cv2.putText(out, f"ROI ({x},{y}) {w}x{h}", (x, max(y - 6, 12)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Water meter calibration helper")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to config.yaml (default: config.yaml)")
    parser.add_argument("--camera", type=int, default=None,
                        help="Override camera index")
    args = parser.parse_args()

    # Load config if it exists
    width, height = 1280, 720
    roi = None
    camera_index = 0

    if os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cam = cfg.get("camera", {})
        camera_index = cam.get("index", 0)
        width = cam.get("width", 1280)
        height = cam.get("height", 720)
        roi = cfg.get("roi")
    else:
        print(f"Config file '{args.config}' not found — using defaults.")

    if args.camera is not None:
        camera_index = args.camera

    print(f"Capturing frame from camera index {camera_index} at {width}x{height} …")
    frame = capture_frame(camera_index, width, height)

    actual_h, actual_w = frame.shape[:2]
    print(f"Actual resolution: {actual_w} x {actual_h}")

    cv2.imwrite("calibration_frame.jpg", frame)
    print("Saved: calibration_frame.jpg")

    grid_img = draw_grid(frame)
    if roi:
        grid_img = draw_roi(grid_img, roi)
        print(f"Current ROI overlay: x={roi['x']} y={roi['y']} "
              f"width={roi['width']} height={roi['height']}")
    else:
        print("No ROI defined yet in config — no overlay drawn.")

    cv2.imwrite("calibration_grid.jpg", grid_img)
    print("Saved: calibration_grid.jpg")

    print("\nNext steps:")
    print("  1. Open calibration_grid.jpg and identify the pixel rect around the digit strip.")
    print("  2. Update the 'roi' section in config.yaml with those values.")
    print("  3. Re-run this script to verify the red ROI rectangle covers the digits.")
    print("  4. Then test OCR with: python water_meter_reader.py --once")


if __name__ == "__main__":
    main()
