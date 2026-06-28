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

## Kiosk mode

Use kiosk mode when the Raspberry Pi should boot directly into the camera webpage for the client/operator.

This setup creates a limited `operator` user with no sudo access, starts the camera server as a systemd service, and auto-opens Chromium to the camera webpage:

```bash
cd /path/to/TM_SEATING_SANAND
sudo APP_DIR=$(pwd) KIOSK_USER=operator KIOSK_URL=http://127.0.0.1:8080 bash scripts/setup-kiosk.sh
sudo systemctl start tm-camera-monitor.service
sudo reboot
```

After reboot, the Pi auto-logs into `operator` and opens only the camera page. Your existing admin user remains password protected for SSH, terminal, settings, and maintenance.

If the first setup attempt stopped at `The group 'operator' already exists`, pull the latest code and run the same setup command again. The script now handles that partial state and will continue creating the service.

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
