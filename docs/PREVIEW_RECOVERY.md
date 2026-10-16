# Automatic live preview recovery for the earlier 35 GB version

Repository: TM_Seating_sanand. Branch: fix/occupancy-preview-recovery.
This is separate from TM_cell5/raspberrypibase and its recovery branch.
feature/occupancy-recording-35gb and CELL5 remain unchanged.

## Apply to the existing installation

Run as the normal administrator, from the installed application folder:

```bash
cd ~/TM_Seating_sanand
git fetch origin fix/occupancy-preview-recovery
TASK_UPGRADE=$(mktemp /tmp/tm-preview-upgrade.XXXXXX.py)
git show FETCH_HEAD:scripts/upgrade-preview.py > "$TASK_UPGRADE"
python3 "$TASK_UPGRADE"
```

Run each line only after the preceding command succeeds. The upgrade:
- Fetches and switches to the fix branch, including on a single-branch clone.
- Recognises only the exact one-line local default change from webrtc to mjpeg.
  That edit is saved in Git stash and migrated to ignored preview-settings.json.
  Any other tracked local edits block the upgrade without being discarded.
- Saves the selected mode in site settings, so future source updates and reboots
  do not require editing app.js again. Existing valid site settings are reused.
- Requests sudo to restart the verified installed monitor and apply settings.
  There is a brief one-time recording interruption while the monitor restarts.
- If tm-camera-kiosk.service exists, updates only its dashboard URL and restarts
  it. The existing kiosk user/launcher is retained. Configuration is backed up.
- If the device uses Firefox/Openbox without that service, leaves its desktop
  startup method alone. Press Ctrl+Shift+R once in Firefox after the update.

To explicitly choose MJPEG, use python3 "$TASK_UPGRADE" --mode mjpeg on the last
line instead. For WebRTC, use --mode webrtc. The default auto option preserves
the known local MJPEG edit or existing saved mode; otherwise it selects WebRTC.

If the installed service uses another project directory, apply stops instead of
changing its program path or installing an alternate kiosk. Stop on an error and
review it. No reboot, Chromium installation or full kiosk reinstall is required.
The archive and camera configuration are retained. If the browser's startup URL
contains an explicit ?live= choice, that choice takes priority over site settings.

## What changes

WebRTC is played directly through a local MediaMTX reader. Decoded-frame progress
is monitored: a 20-second startup with no picture, or a 10-second stall after
playback begins, closes the old session and retries. Retry intervals range from
2 to 30 seconds. Signaling requests and response bodies have an 8-second timeout.

MJPEG uses a streaming reader that reconstructs JPEGs across network chunks,
displays the newest complete frame, bounds buffering at 4 MiB, and releases Blob
URLs after decoding. It detects dropped/stalled streams and retries with the
same limits. The backend preview decoder selects an 8-second RTSP I/O timeout
when supported by the installed FFmpeg (including older stimeout builds), and a
20-second no-JPEG watchdog, plus bounded slow-client writes and subprocess cleanup.
These limits apply only to the live MJPEG decoder, not the independent recorder.

Decoded frames are used rather than motion detection, so an empty motionless
cell does not trigger recovery. Leaving Live closes its preview; returning opens
a fresh connection. HLS retains its existing playback path and error handling.

Occupancy, entry/exit processing, camera/ESP32 firmware and the 35 GB archive
policy are unchanged. The Python changes are confined to the MJPEG live handler
and its preview-only timeout helper and process watchdog. No periodic device reboot is added.

This fixes missing recovery paths; it does not establish the root cause of a
past outage. JavaScript reconnection requires a responsive browser process and
cannot repair a physically offline camera or a power failure. Check the picture
once after installing; you do not need to wait on site for another outage first.

## Validation and source

```bash
python3 -m unittest discover -s tests -v
node tests/test_preview_recovery.cjs
node tests/test_mjpeg_recovery.cjs
```

Node is needed only for developer tests. WebRTC tests simulate 72 hours and 200
retries. MJPEG tests cover fragmented JPEGs, stopped streams, 100 disconnects,
buffer limits and Blob/timer cleanup. Python tests cover decoder timeouts and
preserve the existing recorder/storage/real-FFmpeg tests. Physical-device soak
testing is still required to confirm behavior on that particular Pi/Radxa.

mediamtx-reader.js is based on official MediaMTX v1.18.2 under the included MIT
license. It has a small optional fetchRequest adaptation for timeout/cancellation
and handled DELETE failures. Live WebRTC assets are served locally. The existing
Hls.js recording fallback still loads from its original CDN.
