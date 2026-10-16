# Native player recovery for the earlier occupancy/35 GB variant

Repository: TM_Seating_sanand.
Branch: fix/occupancy-native-player-recovery.
Base: 9c17518848538afefb12e107f3432de1b39b6222.

This ports the native MediaMTX frame observer that was verified on the separate
raspberrypibase Pi. It keeps this variant's original player and recording code;
server.py, firmware, MQTT/counting and the archive policy are unchanged from the
35 GB base. feature/occupancy-recording-35gb and CELL5 remain unchanged.

## Apply on the device running this earlier variant

Use the normal administrator account in the installed application directory:

```bash
cd ~/TM_Seating_sanand &&
git fetch https://github.com/kusheshwapandit1483-byte/TM_Seating_sanand.git fix/occupancy-native-player-recovery &&
TASK_UPGRADE=$(mktemp /tmp/tm-35gb-native-preview.XXXXXX.py) &&
git show FETCH_HEAD:scripts/upgrade-native-preview.py > "$TASK_UPGRADE" &&
python3 "$TASK_UPGRADE"
```

The initial fetch uses the exact repository URL, so an origin pointing at TM_cell5
does not cause a missing-branch error. The updater automatically finds a remote
for TM_Seating_sanand, including tm-updates, or uses the repository URL directly.
It does not replace origin or any other remote. --remote can override selection.

Before updating, it checks that this is the installed service's working directory
and the checkout descends from the earlier 35 GB release. It refuses the separate
raspberrypibase installation. The exact app.js/index.html rollback to this base
can be migrated and saved in Git stash; unrelated tracked edits are retained and
block the update. Existing non-WebRTC site settings also stop the upgrade.

The installer preserves the existing kiosk service's User, HOME and graphical
session settings. For the known original shell launcher, it adds only the local
observer extension flag to its Chromium command; the script's display discovery,
profile, logging and other flags remain. For the known session-aware Python
launcher, it uses the same small wrapper as raspberrypibase. Unrecognised launchers,
custom extension flags, Firefox/Openbox-only setups and MJPEG/HLS configurations
are not converted to another browser or player.

A kiosk pointing directly at the native MediaMTX page on port 8889 is pointed at
the local dashboard on port 8080, which embeds that same native page. The previous
kiosk configuration is backed up. No browser is installed and no login method is
replaced. Only the existing browser service is restarted. The monitor and MediaMTX
services are not restarted or reconfigured, and recordings are retained.

## Verification and recovery

Allow up to 40 seconds. Installation starts a temporary loopback HTTP receiver
with an OS-assigned port and a fresh nonce. The dashboard and frame observer report
progress directly; Chromium console forwarding is not used and verbose GPU logging
is not enabled. Success requires dashboard-ready, parent-ready and parent-frames.
The last two require origin/source/generation-validated messages from the observer
in the actual native iframe. Optional observer-ready and observer-frames reports
help diagnose where a failed check stopped. Temporary verification parameters are
removed from the persistent kiosk URL and the receiver exits after verification.

If verification fails, the prior extension, launcher and kiosk configuration are
restored and only the browser restarts. Diagnostics are printed and saved as
verification.json in /var/backups/tm-camera/native-preview-<timestamp>. Send the
installation output instead of repeatedly reinstalling or rebooting.

The original MediaMTX page remains on port 8889. An isolated Chromium content script
reads its existing video element's decoded-frame callbacks/counters and posts
progress to the dashboard. It does not create a peer connection or alter the
player's negotiation, srcObject or tracks. Once the observer connects, a 20-second
startup without frames or a 10-second playback stall unloads and reloads only the
live iframe. Failed retries back off from 2 to 30 seconds. Healthy playback is not
periodically interrupted. Live closes when hidden or when Recordings is opened;
returning starts the same native player again. Occupancy recording is independent.

The observer activates only for the loopback pramacam iframe on port 8889 with a
generation token. It has no background worker or storage, camera, microphone or
filesystem permissions. If the observer is missing, the original player is retained
instead of being repeatedly reloaded; installation does not report success.
JavaScript cannot repair a hung browser process, power failure or permanently
unreachable camera. The ported variant still needs verification on its own device.

## Tests

```bash
python3 -m unittest discover -s tests -v
node tests/test_native_player.cjs
```

Node is needed only for developer tests. The common native-player suite covers 72
simulated healthy hours without reloads, freezes, 100 retries, stale/foreign message
filtering, frame-counter fallback, direct verification reports and dashboard
navigation. Python tests cover original recorder/storage/real-FFmpeg behavior,
verification nonce and frame requirements, shell/Python launchers, idempotency,
rollback, preserving the kiosk user and selecting the correct repository remote.

To reapply an already-updated checkout:

```bash
sudo python3 scripts/apply-native-preview.py
```
