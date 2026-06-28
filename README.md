# TM Camera Monitor

This is a lightweight Raspberry Pi camera monitor with live preview, always-on recording, retention cleanup, and VLC playback for saved clips.

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

## Recording and VLC playback

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
4. Click a clip to open it in VLC on the Raspberry Pi.

You do not need to open the recordings folder for normal viewing. The webpage stays open in the background; when VLC is closed, the user returns to the same page.

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

Install ffmpeg and VLC if they are not installed:

```bash
sudo apt update
sudo apt install ffmpeg vlc
```

For VLC launching, run `python3 server.py` from the Raspberry Pi desktop session. If you run it as a systemd service, the service must have access to the desktop display, for example `DISPLAY=:0` and the correct `XAUTHORITY`. Do not run VLC as root.
