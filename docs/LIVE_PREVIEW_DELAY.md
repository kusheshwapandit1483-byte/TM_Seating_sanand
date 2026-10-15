# Live preview: approximately two-second target

Open `http://127.0.0.1:8080/?live=webrtc` on the kiosk. The dashboard connects
directly to the existing MediaMTX `pramacam` WHEP endpoint on port 8889. There
is no change to the camera-to-MediaMTX architecture or recording source.

The player requests minimum receiver buffering where supported. Every 500 ms
it checks playback progress and recent WebRTC receiver statistics. After a
10-second connection startup allowance, a freeze longer than two seconds or
three consecutive receiver-buffer measurements above two seconds reconnects
the preview. Reconnection can temporarily interrupt the picture. Returning
from recordings or a hidden browser tab opens a fresh live connection.

Two seconds is a target, not a measured or guaranteed camera-to-screen delay.
Receiver statistics exclude delay already introduced by the camera or RTSP
source. A continuously moving stream that arrives late from the source cannot
be identified as late from these statistics alone. HLS remains an optional
fallback with a one-second live-sync target and two-second catch-up threshold;
its achievable delay depends on the server's segment and part durations.
The MJPEG fallback is unchanged and is not the recommended two-second path.

## Update the device

Run from the existing checkout (save local edits before switching branches):

```bash
cd ~/TM_Seating_sanand
git fetch origin
git switch feature/occupancy-recording-35gb
git pull --ff-only origin feature/occupancy-recording-35gb
sudo systemctl restart tm-camera-monitor
```

Open `http://127.0.0.1:8080/?live=webrtc` and hard-refresh with Ctrl+Shift+R.
If the kiosk service still opens `http://127.0.0.1:8889/pramacam`, configure its
URL to use the dashboard (the direct camera page bypasses this player):

```bash
sudo mkdir -p /etc/systemd/system/tm-camera-kiosk.service.d
printf '%s\n' '[Service]' 'Environment="KIOSK_URL=http://127.0.0.1:8080/?live=webrtc"' \
  | sudo tee /etc/systemd/system/tm-camera-kiosk.service.d/live-preview.conf
sudo systemctl daemon-reload
sudo systemctl restart tm-camera-kiosk
```

No MediaMTX reinstall is required. Keep the existing RTSP URL and credentials.
If the picture remains late, configure the camera for H.264 without B-frames,
a keyframe interval of approximately one second (e.g. 25 frames at 25 fps),
and use wired Ethernet. Check device CPU load and hardware video decoding if
the browser drops frames. Camera setting names depend on the model.

## Measure on the actual cell

Put a running stopwatch in the camera's view and photograph it alongside the
kiosk display. Subtract the displayed video stopwatch time from the physical
stopwatch time. Repeat after 10 minutes and after opening recordings, returning
to live, and reconnecting the camera. Accept the deployment only after the
observed delay meets the required approximately two seconds.

ESP32 counting, the eight-second presence window, two-second presence
confirmation, lamp/relay timing, occupancy-triggered recording and the 35 GB
archive policy are unchanged. This display setting does not alter cell control.

## Third-party reader

`mediamtx-reader.js` is the unmodified official MediaMTX v1.18.2 browser reader,
vendored locally so live WebRTC needs no internet/CDN. See `MEDIAMTX-LICENSE.txt`.
It uses the WHEP interface also provided by the project's MediaMTX v1.11.2 setup;
that server/browser combination still needs verification on the actual device.

References:
- https://mediamtx.org/docs/read/web-browsers
- https://mediamtx.org/docs/features/webrtc-specific-features
- https://www.w3.org/TR/webrtc/#dom-rtcrtpreceiver-jitterbuffertarget
- https://github.com/bluenviron/mediamtx/blob/v1.18.2/internal/servers/webrtc/reader.js
