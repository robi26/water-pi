# water-pi

Reads a mechanical (roller/drum) water meter using a USB webcam on a Raspberry Pi 4, extracts the digit value via Tesseract OCR, and publishes it to Home Assistant via MQTT auto-discovery.

## Requirements

**System**
```bash
sudo apt-get install tesseract-ocr
```

**Python**
```bash
pip install -r requirements.txt
```

## Setup

### 1. Find your ROI

Run the calibration helper to capture a frame and get a grid overlay:

```bash
python calibrate.py
```

Open `calibration_grid.jpg`, read off the pixel coordinates of the digit strip, and fill them into `config.yaml`:

```yaml
roi:
  x: 100
  y: 200
  width: 400
  height: 80
```

Re-run `calibrate.py` after editing — the ROI is drawn as a red rectangle so you can verify it covers the digits.

### 2. Configure

Edit `config.yaml`. The key sections:

```yaml
camera:
  index: 0          # /dev/video0

interval_seconds: 300

roi:
  x: 100            # ← from calibration step
  y: 200
  width: 400
  height: 80

meter:
  decimal_places: 3  # raw "1234567" → 1234.567
  unit: "m³"

mqtt:
  host: "192.168.1.10"
  port: 1883
  username: ""
  password: ""
  device_id: "water_meter_main"
```

### 3. Test OCR (no MQTT needed)

```bash
python water_meter_reader.py --once
```

Prints the parsed reading to stdout. Enable `debug.save_images: true` in `config.yaml` to save the preprocessed image for inspection.

### 4. Run as a systemd service

Edit the two paths in `water-meter.service`, then install:

```bash
sudo cp water-meter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now water-meter
```

Check status and live logs:

```bash
sudo systemctl status water-meter
journalctl -u water-meter -f
```

## Home Assistant

No manual HA configuration needed. The script publishes an MQTT auto-discovery payload on startup. A sensor entity named **Water Meter** will appear automatically with:

- `device_class: water`
- `state_class: total_increasing`
- `unit_of_measurement: m³`

This makes it compatible with the HA **Energy dashboard** out of the box.

## Tips

- **Lighting** matters most. Consistent, diffuse light over the digit strip dramatically improves OCR accuracy. A small LED ring mounted around the meter helps.
- If OCR is unreliable, increase `ocr.scale_factor` (try `4` or `5`) and switch `threshold_method` to `adaptive`.
- The script discards any reading lower than the previous value to protect the HA utility meter from garbled reads.

