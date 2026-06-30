# TM Camera Monitor

This is a lightweight Raspberry Pi camera monitor with live preview, always-on recording, retention cleanup, and browser playback for saved clips.

## Run the app on Raspberry Pi

From this folder:

```bash
python3 server.py
```

Recording starts automatically when the server starts. Open the website through the server, not by opening `index.html` directly:

```text
http://127.0.0.1:8080
```

From another device on the same network:

```text
http://RASPBERRY_PI_IP:8080
```

## Live preview URL

For low delay, use the MediaMTX WebRTC page:

```text
http://127.0.0.1:8889/pramacam
```

From another device:

```text
http://RASPBERRY_PI_IP:8889/pramacam
```

HLS also works but usually has delay:

```text
http://127.0.0.1:8888/pramacam/index.m3u8
```

## Recording and playback

The Python server records automatically from this default source:

```text
rtsp://127.0.0.1:8554/pramacam
```

Recording uses ffmpeg `-c copy`, so it does not reduce stream quality. By default it creates 30-minute MP4 clips in:

```text
recordings/
```

New clips are named like data_YYYY-MM-DD_HH-MM-SS.mp4.

Use the website only:

1. Open `http://RASPBERRY_PI_IP:8080`.
2. Recording starts automatically; no manual Start/Stop button is shown.
3. Open the `Recordings` tab.
4. Click a clip to prepare browser playback and play it in the webpage.

You do not need to open the recordings folder for normal viewing.
When a recording is selected, the server prepares an HLS playback cache from the 30-minute MP4. This does not reduce recording clarity; it remuxes the selected clip into browser-friendly chunks so the client still sees one playable 30-minute timeline.

## Storage estimate

Your measured size is about 47 MiB per 5 minutes.

```text
47 MiB x 12 = about 564 MiB per hour
564 MiB x 24 = about 13.2 GiB per day
2 days = about 26.4 GiB
```

So a 64 GB high-endurance card can keep around 2 days, assuming the OS and free-space margin are managed.

## Retention

Use the `Keep recordings` selector on the preview page. The allowed choices are:

```text
1 day
2 days
```

The maximum is capped at 2 days. The server deletes older MP4 clips automatically during normal use.

Optional startup settings:

```bash
RECORDING_SOURCE="rtsp://127.0.0.1:8554/pramacam" SEGMENT_SECONDS=1800 RETENTION_DAYS=2 PORT=8080 python3 server.py
```

## Requirements on Raspberry Pi

Install ffmpeg if it is not installed:

```bash
sudo apt update
sudo apt install ffmpeg
```


## ESP32-S3 room count

The dashboard reads the final processed person count from the ESP32-S3 MQTT broker. By default the server connects to:

```text
Host: 192.168.10.1
Port: 1883
Topic: eagle/system/status
```

The ESP32 status payload should include `occupancy`, `total_in`, and `total_out`. These appear in the dashboard as Persons Inside, Entries, and Exits.

Optional startup overrides:

```bash
ESP32_MQTT_HOST=192.168.10.1 ESP32_MQTT_TOPIC=eagle/system/status python3 server.py
```
## Kiosk mode

Use kiosk mode when the Raspberry Pi should boot directly into the camera webpage for the client/operator.

This setup creates a limited `operator` user with no sudo access, starts the camera server as a systemd service, and auto-opens Chromium to the camera webpage:

```bash
cd /path/to/TM_SEATING_SANAND
sudo APP_DIR=$(pwd) KIOSK_USER=operator KIOSK_URL=http://127.0.0.1:8080 bash scripts/setup-kiosk.sh
sudo systemctl start tm-camera-monitor.service
sudo systemctl start tm-camera-kiosk.service
sudo systemctl restart lightdm
sudo reboot
```

After reboot, the Pi auto-logs into `operator` and opens only the camera page. Your existing admin user remains password protected for SSH, terminal, settings, and maintenance.

You do not need to manually create `operator`; the setup script creates it. If `id operator` does not show a user after setup, the setup script did not complete and should be rerun from the admin account.

The kiosk Chromium uses a separate clean profile with keyring prompts disabled, so the client should not see the `Unlock Keyring` popup. A dedicated `tm-camera-kiosk.service` also starts Chromium after boot, so kiosk startup does not depend only on desktop autostart behavior. The setup also updates `/etc/lightdm/lightdm.conf` so Raspberry Pi OS autologs into `operator` instead of the admin user.

If setup stopped partway or the Pi boots but does not open the kiosk page, pull the latest code and run the same setup command again. The script is safe to rerun and now installs multiple autostart methods for Raspberry Pi desktop variants.

If the Pi boots and nothing opens, press `Ctrl + Alt + F2`, login as admin, and run:

```bash
cd /path/to/TM_SEATING_SANAND
sudo bash scripts/check-kiosk.sh
```

Then rerun setup:

```bash
sudo APP_DIR=$(pwd) KIOSK_USER=operator KIOSK_URL=http://127.0.0.1:8080 bash scripts/setup-kiosk.sh
sudo systemctl start tm-camera-monitor.service
sudo systemctl start tm-camera-kiosk.service
sudo systemctl restart lightdm
sudo reboot
```

VLC can stay installed on the Pi for admin/manual checking. The kiosk user does not get sudo or normal desktop access.

Admin access from kiosk with keyboard:

1. Press `Ctrl + Alt + F2`.
2. Login with the admin username, for example `aegixcore`.
3. Enter the admin password.
4. Run maintenance commands as needed.
5. Return to the kiosk screen with `Ctrl + Alt + F7`. If that does not return, try `Ctrl + Alt + F1`.

Do not give sudo access to `operator`. Admin access should always require the admin username and password.

To disable kiosk mode:

```bash
sudo bash scripts/disable-kiosk.sh
sudo reboot
```

More notes are in `scripts/disable-kiosk.md`.
## Live AI person detection

The dashboard has a live detection overlay for the `person` class. The browser only draws the boxes; the Radxa/Raspberry Pi backend reads the same RTSP camera source and runs AI beside the recorder.

Default detection source:

```text
rtsp://127.0.0.1:8554/pramacam
```

Start with detection disabled for normal recording-only operation:

```bash
python3 server.py
```

Enable person detection with an OpenCV-compatible ONNX model:

```bash
AI_DETECTION_ENABLED=1 \
AI_DETECTION_BACKEND=opencv-onnx \
AI_DETECTION_MODEL=/path/to/yolov8n.onnx \
python3 server.py
```

Useful optional settings:

```bash
AI_DETECTION_SOURCE=rtsp://127.0.0.1:8554/pramacam
AI_DETECTION_INPUT_SIZE=640
AI_DETECTION_CONFIDENCE=0.35
AI_DETECTION_IOU=0.45
AI_DETECTION_INTERVAL=0.15
```

The model must use COCO class IDs where `person` is class `0`. The backend filters every output so only `person` detections are sent to the website.

For quick CPU-only testing without an ONNX file, OpenCV's HOG person detector can be used, but it is slower and less accurate:

```bash
AI_DETECTION_ENABLED=1 AI_DETECTION_BACKEND=hog python3 server.py
```

The website polls this endpoint every 500 ms:

```text
/api/detections/latest
```

Example response:

```json
{
  "enabled": true,
  "running": true,
  "detections": [
    {
      "class": "person",
      "confidence": 0.91,
      "xNorm": 0.21,
      "yNorm": 0.12,
      "widthNorm": 0.28,
      "heightNorm": 0.62
    }
  ]
}
```

For the final Radxa A7A build, keep the same `/api/detections/latest` response format and replace only the internals of `PersonDetectionMonitor` with the board-supported NPU runtime. That lets the website and dashboard stay unchanged while inference moves from OpenCV CPU to the A7A AI accelerator.
