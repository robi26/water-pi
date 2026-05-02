#!/usr/bin/env python3
"""
water_meter_reader.py — Reads a mechanical water meter using a USB webcam,
extracts the digit value via Tesseract OCR, and publishes it to Home Assistant
via MQTT auto-discovery.

Usage:
    python water_meter_reader.py [--config config.yaml] [--once]

    --config   Path to the YAML configuration file (default: config.yaml)
    --once     Capture a single reading, print to stdout, and exit.
               No MQTT connection is made. Useful for testing OCR and ROI.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

try:
    import cv2
except ImportError:
    sys.exit("opencv-python is not installed. Run: pip install opencv-python")

try:
    import pytesseract
except ImportError:
    sys.exit("pytesseract is not installed. Run: pip install pytesseract")

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is not installed. Run: pip install PyYAML")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt is not installed. Run: pip install paho-mqtt")


# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── MeterReader ──────────────────────────────────────────────────────────────

class MeterReader:
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.cfg: dict = {}
        self.mqtt_client: Optional[mqtt.Client] = None
        self._last_value: Optional[float] = None
        self._mqtt_connected: bool = False

    # ── Configuration ─────────────────────────────────────────────────────────

    def load_config(self) -> None:
        if not os.path.exists(self.config_path):
            sys.exit(f"Config file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        if not self.cfg:
            sys.exit("Config file is empty or invalid YAML.")
        log.info("Config loaded from %s", self.config_path)

    def _cfg(self, *keys, default=None):
        """Safely navigate nested config keys."""
        node = self.cfg
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
            if node is default:
                return default
        return node

    # ── MQTT ──────────────────────────────────────────────────────────────────

    def setup_mqtt(self) -> None:
        device_id = self._cfg("mqtt", "device_id", default="water_meter_main")
        discovery_prefix = self._cfg("mqtt", "discovery_prefix", default="homeassistant")

        self._state_topic = f"{discovery_prefix}/sensor/{device_id}/state"
        self._discovery_topic = f"{discovery_prefix}/sensor/{device_id}/config"

        client_id = f"water-pi-{device_id}"
        self.mqtt_client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        username = self._cfg("mqtt", "username", default="")
        password = self._cfg("mqtt", "password", default="")
        if username:
            self.mqtt_client.username_pw_set(username, password or None)

        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect

        host = self._cfg("mqtt", "host", default="localhost")
        port = self._cfg("mqtt", "port", default=1883)

        log.info("Connecting to MQTT broker at %s:%s …", host, port)
        try:
            self.mqtt_client.connect(host, port, keepalive=60)
        except OSError as exc:
            sys.exit(f"Cannot connect to MQTT broker {host}:{port} — {exc}")

        self.mqtt_client.loop_start()

        # Wait up to 10 s for connection
        deadline = time.time() + 10
        while not self._mqtt_connected and time.time() < deadline:
            time.sleep(0.1)
        if not self._mqtt_connected:
            sys.exit("Timed out waiting for MQTT connection.")

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            self._mqtt_connected = True
            log.info("MQTT connected.")
            self._publish_discovery()
        else:
            log.error("MQTT connection refused — reason code %s", reason_code)

    def _on_mqtt_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._mqtt_connected = False
        log.warning("MQTT disconnected (reason code %s). Will auto-reconnect.", reason_code)

    def _publish_discovery(self) -> None:
        device_id = self._cfg("mqtt", "device_id", default="water_meter_main")
        unit = self._cfg("meter", "unit", default="m³")

        discovery_payload = {
            "name": "Water Meter",
            "unique_id": f"{device_id}_water_meter",
            "state_topic": self._state_topic,
            "value_template": "{{ value_json.value }}",
            "unit_of_measurement": unit,
            "device_class": "water",
            "state_class": "total_increasing",
            "icon": "mdi:water",
            "device": {
                "identifiers": [device_id],
                "name": "Water Meter",
                "model": "water-pi",
                "manufacturer": "DIY",
            },
        }

        self.mqtt_client.publish(
            self._discovery_topic,
            payload=json.dumps(discovery_payload),
            qos=1,
            retain=True,
        )
        log.info("Published MQTT auto-discovery config to %s", self._discovery_topic)

    def publish_value(self, value: float) -> None:
        if self.mqtt_client is None or not self._mqtt_connected:
            log.error("MQTT not connected — cannot publish value.")
            return
        payload = json.dumps({"value": value})
        result = self.mqtt_client.publish(self._state_topic, payload=payload, qos=1)
        result.wait_for_publish(timeout=5)
        log.info("Published value %.3f to %s", value, self._state_topic)

    # ── Image capture ─────────────────────────────────────────────────────────

    def capture_image(self) -> "cv2.Mat":
        cam_index = self._cfg("camera", "index", default=0)
        width = self._cfg("camera", "width", default=1280)
        height = self._cfg("camera", "height", default=720)

        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {cam_index}.")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Discard initial frames so the sensor stabilises
        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            raise RuntimeError("Failed to capture a frame from the camera.")

        return frame

    # ── Image preprocessing ───────────────────────────────────────────────────

    def preprocess_image(self, frame: "cv2.Mat") -> "cv2.Mat":
        roi = self._cfg("roi")
        if not roi:
            raise ValueError("No 'roi' defined in config.yaml. Run calibrate.py first.")

        x = int(roi.get("x", 0))
        y = int(roi.get("y", 0))
        w = int(roi.get("width", frame.shape[1]))
        h = int(roi.get("height", frame.shape[0]))

        # Clamp to frame boundaries
        fh, fw = frame.shape[:2]
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = max(1, min(w, fw - x))
        h = max(1, min(h, fh - y))

        cropped = frame[y : y + h, x : x + w]

        scale = max(1, int(self._cfg("ocr", "scale_factor", default=3)))
        resized = cv2.resize(cropped, (cropped.shape[1] * scale, cropped.shape[0] * scale),
                             interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        method = self._cfg("ocr", "threshold_method", default="otsu")
        if method == "adaptive":
            binary = cv2.adaptiveThreshold(
                denoised, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                blockSize=11,
                C=2,
            )
        else:
            # Default: Otsu
            _, binary = cv2.threshold(denoised, 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # If the background is mostly dark, invert so digits are dark on white
        if cv2.mean(binary)[0] < 127:
            binary = cv2.bitwise_not(binary)

        # Save debug image if requested
        if self._cfg("debug", "save_images", default=False):
            img_dir = self._cfg("debug", "image_dir", default="/tmp/water-pi")
            os.makedirs(img_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            cv2.imwrite(os.path.join(img_dir, f"{ts}_preprocessed.jpg"), binary)

        return binary

    # ── OCR ───────────────────────────────────────────────────────────────────

    def read_digits(self, preprocessed: "cv2.Mat") -> str:
        config = (
            "--psm 7 "        # Treat image as a single text line
            "--oem 3 "        # LSTM + legacy engine
            "-c tessedit_char_whitelist=0123456789"
        )
        raw = pytesseract.image_to_string(preprocessed, config=config)
        return raw.strip()

    # ── Value parsing ─────────────────────────────────────────────────────────

    def parse_value(self, raw: str) -> Optional[float]:
        digits_only = "".join(ch for ch in raw if ch.isdigit())

        if not digits_only:
            log.warning("OCR returned no digits (raw: %r)", raw)
            return None

        decimal_places = int(self._cfg("meter", "decimal_places", default=3))

        if len(digits_only) <= decimal_places:
            log.warning("Too few digits for decimal split (got %r)", digits_only)
            return None

        integer_part = digits_only[:-decimal_places]
        fractional_part = digits_only[-decimal_places:]
        value_str = f"{integer_part}.{fractional_part}"

        try:
            value = float(value_str)
        except ValueError:
            log.warning("Could not convert %r to float", value_str)
            return None

        # Monotonicity guard — reject reads that would go backwards
        if self._last_value is not None and value < self._last_value:
            log.warning(
                "Rejected value %.3f — less than last known value %.3f (garbled read?)",
                value, self._last_value,
            )
            return None

        return value

    # ── Main loop ─────────────────────────────────────────────────────────────

    def read_once(self, publish: bool = False) -> Optional[float]:
        """Capture one reading. If publish=True, send it to MQTT."""
        try:
            frame = self.capture_image()
        except RuntimeError as exc:
            log.error("Camera error: %s", exc)
            return None

        try:
            preprocessed = self.preprocess_image(frame)
        except (ValueError, cv2.error) as exc:
            log.error("Preprocessing error: %s", exc)
            return None

        raw = self.read_digits(preprocessed)
        log.debug("OCR raw output: %r", raw)

        value = self.parse_value(raw)
        if value is None:
            return None

        log.info("Meter reading: %.3f %s", value, self._cfg("meter", "unit", default="m³"))
        self._last_value = value

        if publish:
            self.publish_value(value)

        return value

    def run(self) -> None:
        """Main loop: read and publish at the configured interval."""
        interval = int(self._cfg("interval_seconds", default=300))
        log.info("Starting main loop — interval %d s", interval)

        while True:
            self.read_once(publish=True)
            log.debug("Sleeping %d s …", interval)
            time.sleep(interval)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Water meter reader — captures webcam image, extracts "
                    "digit value via OCR, publishes to Home Assistant via MQTT."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Capture a single reading, print to stdout, and exit (no MQTT).",
    )
    args = parser.parse_args()

    reader = MeterReader(args.config)
    reader.load_config()

    if args.once:
        value = reader.read_once(publish=False)
        if value is not None:
            unit = reader._cfg("meter", "unit", default="m³")
            print(f"Reading: {value:.3f} {unit}")
        else:
            print("Reading: FAILED (check logs above)")
            sys.exit(1)
    else:
        reader.setup_mqtt()
        try:
            reader.run()
        except KeyboardInterrupt:
            log.info("Interrupted — shutting down.")
        finally:
            if reader.mqtt_client:
                reader.mqtt_client.loop_stop()
                reader.mqtt_client.disconnect()


if __name__ == "__main__":
    main()
