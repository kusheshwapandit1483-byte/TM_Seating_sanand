SPECIAL PROGRAMME: MAX ENTRY / MAX EXIT + INSIDE CAMERA PRESENCE
Version: ESP firmware 1.7.0-interior-presence

CELL 6 FULL-SOURCE VERSION
For the version preserving the complete user-supplied production programme, use
cell_6/cell_6.ino (firmware 1.7.3-cell-6-latched-count-reset). Its inside/outside IDs
must be set by the operator before uploading. See cell_6/README.txt and its exact
reference_original.txt / changes.patch comparison. The earlier condensed sketch
is retained below for history; Cell 6 is the full-source revision. Its presence
fallback is now restricted to an 8s post-EXIT window; see cell_6/README.txt.

FILES TO USE
  production_flow.json              Import on BOTH reCameras for normal operation.
  maintainance_flow.json            Import on each reCamera to configure its door/interior zones.
  esp32_presence/esp32_presence.ino Open this sketch in Arduino IDE and upload to the ESP32-S3.

Original flows in the parent folder have not been changed.
The updated occupancy-triggered kiosk is in the Git repository root (../server.py).
See KIOSK_UPDATE.txt to update an existing installation without deleting its recordings.
The ESP sketch is a complete programme based on the Arduino code supplied in chat.
It keeps its operational settings: 120-second startup, start acknowledgement,
5-second vacancy delay, 5-to-under-10-second count clear, 10-second maintenance
toggle, Ethernet fault/recovery, watchdog, and no flash count persistence/file logs.
Serial monitoring has been consolidated and status diagnostics extended.

CAMERA IDENTITIES AND ROLES
Inside/outside authority is defined ONLY in the ESP programme, near the top:

  { "TM_R1_01", "E1", CAMERA_INSIDE  },
  { "TM_R1_02", "E1", CAMERA_OUTSIDE }

These are DEFAULT assignments; swap the roles if your physical installation is
the opposite. Both door labels must remain E1 for this single-door installation.
Set each reCamera's Device Identity to its matching ID and door E1.
There must be exactly one CAMERA_INSIDE and unique IDs. An invalid roster blocks RUN.
Both cameras send presence messages; the ESP ignores the outside camera's presence.

RECAMERA SETUP
1. Import the special maintenance flow. Disable the Production tab while configuring.
2. In Device, save the camera's correct ID and door E1.
3. In ROI Setup, draw the existing door ROI, choose its count edge, select entry
   direction, and keep your existing reflection masks.
4. On the INSIDE camera, select Interior Zone and click at least three points
   outlining the visible robotics-cell interior. Click Use Interior Zone.
   The saved cyan polygon must cover only the physical interior, not the corridor
   seen through the doorway. Presence candidates are inside this cyan polygon
   AND outside the door ROI. A bottom-centre point is used, as in existing counting.
   Any accepted detection in the door ROI also prevents clearing an existing latch.
5. Click Save ROI Config to write all geometry to /userdata/config/roi_config.json.
   The OUTSIDE camera may leave Interior Zone empty because its presence is ignored.
6. Import the special production flow. Disable the Maintenance tab before running
   Production, so its separate camera/model nodes do not compete for the device.
7. Deploy/restart Production after saving device/ROI configuration. Production
   loads device_config.json and roi_config.json at startup, not on every frame.

An empty interior polygon uses the whole unmasked view outside the door ROI.
Only use that option on the inside camera if the whole remaining view represents
the cell interior. An explicit interior polygon is recommended for the inside camera.
Existing ROI files work without the new presence_polygon field.

MODEL NOTE
The original model settings are preserved: Production uses the bundled YOLO11n
model; Maintenance uses /userdata/models/person_only.cvimodel. If that custom
model is not installed, change the maintenance model node to an installed model
before configuring. If you choose to use the custom model for production too,
set the production model URI and class list accordingly and verify its boxes.
The existing camera counting function is unchanged, including its whole-polygon
transition behaviour; this package does not repair the selected-edge limitation.

ESP32 SETUP
Open esp32_presence.ino (the folder and sketch names match).
Select your ESP32-S3 board and the flash/USB settings used by your existing installation.
Dependencies: ESP32 Arduino core 3.x, PicoMQTT, ArduinoJson 7.x.
The separate local ZIP includes a generic ESP32-S3 binary build with the default role assignments
above, 4MB flash partitions, PSRAM disabled, and USB CDC on boot disabled. Rebuild
the sketch in Arduino IDE if you change camera roles, GPIOs, or board settings.
Ethernet broker: 192.168.10.1:1883. Static ESP address and gateway: 192.168.10.1.
GPIOs match the actual definitions in the supplied programme:
  W5500 SCK13, MISO12, MOSI11, CS14; IRQ/RST disabled (-1).
  RED+BUZZER21, GREEN18, YELLOW17, ROBOT RELAY16, NC GREEN BUTTON47.
  Robot disabled=HIGH, enabled=LOW on the relay output.

COUNTING AND PRESENCE RULES
Normal count = clamp(MAX camera entries - MAX camera exits, 0, 50), per door.
Presence does not increment or reset camera entry/exit counters.
  Normal count > 0: effective occupancy is exactly the normal count.
  Normal count = 0, confirmed inside presence: effective occupancy is 1.
  Normal count = 0, inside presence clear: effective occupancy is 0.
Normal EXIT at a normal count of zero remains ignored even if presence supplies 1.
The presence correction disappears only on fresh confirmed clear from the inside camera.
This avoids a synthetic entry/exit baseline changing MAX ENTRY / MAX EXIT.

Presence confirms after MORE THAN 2000ms of fresh qualifying detections.
An empty frame or more than 750ms between fresh frames breaks confirmation.
It checks presence of any person in the interior zone, not continuity of a track ID.
An already-confirmed latch holds while detections move into the door ROI.
Clear confirms after 2000ms of fresh frames with no accepted person in the interior
zone OR door ROI. This is visual clear evidence, not proof of a physical exit if
someone is completely hidden; camera coverage and masks must be checked on site.
After effective occupancy becomes zero AND presence is confirmed clear, the
existing 5000ms robot-release delay runs. A new person/pending/stale result cancels it.
In the normal vacancy path, release is approximately 2s clear confirmation + 5s delay.
Session counters reset at the start of that vacancy delay, as in the original,
but only when effective occupancy is zero and interior presence is clear.

STALE DATA AND OPERATOR CONTROLS
Presence messages publish every 500ms. Source frames older than 1000ms produce
valid=false/state=unknown. No presence message for 1500ms is stale at the ESP.
Pending, unknown, or stale inside presence holds the robot disabled in RUN,
even when effective occupancy is zero. A previously latched person remains 1.
The original CAMERA_FAILURE_TRIGGERS_FAULT=0 setting remains: camera-offline
diagnostics do not change the system state. This additional interior-clear
interlock nevertheless prevents robot release without fresh inside-camera evidence.
Count-clear does not clear physical presence. Maintenance remains an explicit
operator bypass with the robot enabled, matching your original programme.

MQTT AND DASHBOARD
Inputs:
  eagle/<camera_id>/event
  eagle/<camera_id>/heartbeat
  eagle/<camera_id>/presence
Example confirmed presence:
  {"state":"present","valid":true,"present":true,"candidates":1,
   "continuous_ms":2500,"confirmed_ms":2100,"frame_age_ms":0,"door":"E1","ts":123456789}
Camera messages are not retained. Pending/unknown is not treated as clear.
confirmed_ms preserves the original >2s evidence while a latched person moves
into the door ROI; continuous_ms describes the current interior-detection streak.
Existing dashboard reads eagle/system/status occupancy, which now means effective
occupancy. New fields include counted_occupancy, presence_override,
presence_ready_for_empty, vacancy_clear_active, and inside-camera presence details.
Processed total_in/total_out/total_net continue to reflect normal door counting.
Consequently occupancy may be 1 while total_net is 0: presence_override explains why.
Serial commands: STATUS, HELP, EXPORT_LOG (file logging remains disabled).

VALIDATION AND REBUILD
  python build_flows.py
  node tests/test_flows.js
Build_flows.py rebuilds both special JSON exports from the parent originals and
sources/recamera_presence.js. It preserves the production counter byte-for-byte.
The repository contains portable source files. Build caches, compiled binaries,
and this PC's arduino-cli.yaml are excluded; use Arduino IDE to build the sketch.
See VALIDATION.txt for completed checks and the exact compiled board settings.

ON-SITE FUNCTIONAL CHECKS
With robot operation inhibited for commissioning, check:
  - Camera IDs/roles match their physical positions.
  - Normal crossings still produce max-entry/max-exit counts.
  - A person inside when normal count is zero produces occupancy 1 after >2s.
  - Normal counts of 2 or more remain unchanged by presence.
  - Walking through the door ROI does not clear occupancy prematurely.
  - Fresh clear frames produce zero, then the 5s vacancy delay.
  - Stopping inside-camera inference/communication prevents robot release.
  - Count-clear leaves a detected person at effective occupancy 1.
