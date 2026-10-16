# HLS recovery for the AEGIX306 desktop kiosk

AEGIX306 runs Chromium from its existing desktop startup, under aegixcore, with
http://127.0.0.1:8080/?live=hls&boot=<timestamp>. It does not have
tm-camera-kiosk.service or /usr/local/bin/tm-camera-kiosk-start.

This path preserves that HLS player and desktop startup. It does not use the
WebRTC extension, create a kiosk service, install another browser, change the
browser profile or change the user. The recording server, MediaMTX and recordings
are untouched. Do not run the native WebRTC service installer for this device.

From the existing fix/occupancy-native-player-recovery checkout:

```bash
cd /home/aegixcore/Documents/TM_Seating_sanand &&
git pull --ff-only origin fix/occupancy-native-player-recovery
```

Then press Ctrl+Shift+R once in the kiosk browser. Keep its existing ?live=hls URL.
No sudo, service restart or reboot is needed. The index references versioned local
recovery assets, so a refreshed page loads the new code. The same updated code
loads after reboot through the existing startup URL.

The watchdog reads decoded/displayed frame counters once per second from the
existing live video element; media time is a fallback for older browsers. It
does not use motion detection, so a motionless empty cell remains healthy. A
20-second initial lack of frames or a 10-second stall releases the old live HLS
instance and reloads through the same loadHls function. The existing Hls.js/native
HLS player and its configuration are retained. Retries back off from 2 to 30
seconds while the stream is unavailable. Healthy playback is not periodically
interrupted. Fatal unrecoverable HLS errors use the same bounded retry path.

Leaving Live or hiding the dashboard releases its live player; returning starts
it again. The separate recording playback video is not touched. The recorder
continues independently with occupancy-triggered recording and the 35 GB limit.

JavaScript requires a responsive browser and cannot recover a completely hung
browser process or a permanently unavailable camera. This code has automated
stall/retry tests but still needs verification on this HLS device. It makes no
claim that the original hours-later failure's underlying cause is established.

Developer checks (Node is not needed on the kiosk):

```bash
node tests/test_hls_live_recovery.cjs
node tests/test_native_player.cjs
```

HLS checks include 72 simulated healthy hours without reloads, freezes, dropped
frames while media time advances, startup failures, 100 retries, hidden-page
handling, timer cleanup and actual dashboard navigation wiring.
