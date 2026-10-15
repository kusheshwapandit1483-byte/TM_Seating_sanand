# TM Camera Monitor

This is a lightweight Raspberry Pi camera monitor with live preview, occupancy-triggered recording, a 35 GB rolling archive, and browser playback for saved clips.

## Run the app on Raspberry Pi

From this folder:

```bash
python3 server.py
```

Recording waits for ESP32 occupancy and starts automatically when someone is inside the cell. Open the website through the server, not by opening `index.html` directly:

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

The server records from `rtsp://127.0.0.1:8554/pramacam` only while the ESP32 reports effective `occupancy > 0` on `eagle/system/status`. This includes the inside-camera presence correction when normal door counting is zero. Live preview continues when the cell is empty.

The first positive status starts recording. A fresh zero count stops it only after interior presence is confirmed clear and the ESP32 vacancy-clear delay has finished. With the special presence firmware, this preserves the two-second clear confirmation and five-second vacancy delay at the end of a visit. Missing, stale (over five seconds), or malformed MQTT data holds the previous recording decision: an active recording continues, and an idle recorder waits. Retained historical MQTT messages are ignored.

Recording uses ffmpeg `-c copy` to preserve stream quality. Each occupied visit creates an MP4, split into at most 30-minute segments for long visits. Files use unique session prefixes so rapid restarts cannot overwrite a previous clip. The recorder exits gracefully when the cell clears, finalizing the last MP4. Only completed clips appear in the Recordings list; active clips are protected from deletion and playback preparation.

Open the app through `python3 server.py`, open Recordings, and choose a completed clip. HLS playback preparation remuxes the clip without changing image quality.

## Recording storage limit

The fixed archive budget is **35 GB (35,000,000,000 bytes)**, including MP4 recordings, HLS playback cache, and recorder support files. There is no one-day/two-day expiry: older clips remain until the size limit requires space. Old `retentionDays` settings and `RETENTION_DAYS` environment values no longer expire history.

Cleanup checks every ten seconds and when recording stops or playback cache is prepared. It removes the oldest completed MP4 first and its associated HLS cache. It removes orphan cache directories too. Active MP4s are never unlinked while ffmpeg writes them. If an open segment is the only reason storage remains over budget, it is finalized, storage is reclaimed, and recording resumes if the cell is still occupied. Growth between checks can briefly exceed the budget. HLS preparation checks storage while it runs.

Recording status and storage usage are available at `/api/recording/status`. Settings report the fixed policy at `/api/settings`.

Optional source/segment settings:

```bash
RECORDING_SOURCE="rtsp://127.0.0.1:8554/pramacam" SEGMENT_SECONDS=1800 PORT=8080 python3 server.py
```

## Update an existing kiosk

The occupancy/presence version is on the `feature/occupancy-recording-35gb` branch. From the existing repository on your Raspberry Pi/Radxa:

```bash
git fetch origin
git switch feature/occupancy-recording-35gb
git pull --ff-only origin feature/occupancy-recording-35gb
sudo systemctl restart tm-camera-monitor.service
```

Refresh the browser after restarting. ESP32 firmware and reCamera flow sources are in `special_presence_programme/`; define camera roles in its Arduino sketch before uploading.

Replace `server.py`, `app.js`, and `index.html` in the installed `TM_Seating_sanand` app folder with these updated versions. Keep the existing `recordings/` folder and camera/service configuration. Then restart the monitor and refresh the browser:

```bash
sudo systemctl restart tm-camera-monitor.service
```

No kiosk reinstall is required. The new storage cleanup takes effect when the updated server starts. Use the special ESP32 firmware if you want interior-presence corrections to trigger recording.

## Validation

Run `python3 -m unittest discover -s tests -v`. The tests cover presence-triggered recording, clear-delay stop, stale/malformed status, oldest-first storage reclamation, cache accounting, active-file protection, oversized-segment handling, and legacy-setting migration. If ffmpeg and ffprobe are installed, they also generate a synthetic camera source and verify playable MP4s after real segmentation and graceful stop. Camera/ESP32 integration still needs checking on the kiosk hardware.

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

The ESP32 status payload should include `occupancy`. This appears in the dashboard as Persons Inside.

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
AI_DETECTION_HOLD_SECONDS=1.0
```

The model must use COCO class IDs where `person` is class `0`. The backend filters every output so only `person` detections are sent to the website.

If boxes flicker because the model misses a frame, increase `AI_DETECTION_HOLD_SECONDS` to keep the last person box visible briefly. If people are detected only when very close or centered, lower `AI_DETECTION_CONFIDENCE` to `0.25` or `0.20`.

For high CCTV angles or cluttered rooms, `yolov8n` may miss standing people. Try a larger input size first:

```bash
yolo export model=yolov8n.pt format=onnx imgsz=960
```

Then run with:

```bash
AI_DETECTION_INPUT_SIZE=960
AI_DETECTION_CONFIDENCE=0.20
AI_DETECTION_HOLD_SECONDS=2.0
```

If the Radxa can handle the extra CPU load, `yolov8s.onnx` is more reliable than `yolov8n.onnx` for this camera angle.

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
