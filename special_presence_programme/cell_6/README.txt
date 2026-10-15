CELL 6 — FULL PRODUCTION SOURCE WITH INTERIOR PRESENCE

Open cell_6.ino in Arduino IDE. Keep cell_6_presence_window.h and
window_compile_checks.cpp beside it in the SAME sketch folder; use the complete ZIP.
This version starts from your exact attached production programme, not the
earlier condensed esp32_presence sketch. The attached source is retained as
reference_original.txt. changes.patch shows every edit against that source.

WHY THE PREVIOUS SKETCH HAD FEWER LINES
The earlier implementation consolidated relay-writing routines, serial monitor
output, and related repeated code, and omitted disabled persistence/file-logging
implementation paths. It was a rewritten implementation, which made
comparison against the supplied production source difficult. Cell 6 instead
retains all original functions and the complete status tables, logging and
persistence paths. It changes only the integration points needed for presence.
Source size: original 1,505 lines; Cell 6 1,771 lines (plus the small shared gate header and compile checks).
Original functions: 42. All retained. 26 unchanged, 16 extended. 7 new helpers.
All 49 original #define settings are unchanged except the firmware version.
MAX ENTRY/MAX EXIT aggregation and the individual relay output helpers are
textually identical to the attached source.

SET CAMERA ROLES BEFORE UPLOADING
At the top of cell_6.ino, enter the physical camera IDs yourself:

  #define CELL_6_INSIDE_CAMERA_ID  "your_actual_inside_camera_id"
  #define CELL_6_OUTSIDE_CAMERA_ID "your_actual_outside_camera_id"

The delivered fields are deliberately blank because you said you will decide
the roles in code. Do not edit the separate roster: it uses these definitions.
The names must match the Device Identity configured on each reCamera exactly.
Examples from the original attachment were TM_R1_01 and TM_R1_02. They are not
assumed to describe the actual inside/outside cameras for Cell 6.
Both cameras remain on the same door label E1, as in the original programme.
The Cell 6 name does not change camera IDs, door labels, GPIOs, or IP addressing.
Blank, duplicate, overlong or reserved camera IDs invalidate the configuration
and prevent RUN after startup. Maintenance is still the original explicit
operator bypass and can enable the robot; this behaviour was not removed.

RECAMERA FLOWS
Import recamera/maintainance_flow.json for configuration and
recamera/production_flow.json for operation on BOTH cameras. These are the same
presence-capable flows from the previously delivered programme.
Configure each Device Identity. Keep your door ROI, entry direction, and masks.
On the inside camera, draw an Interior Zone covering only the visible physical
cell interior, then Save ROI Config. Presence candidates are inside that zone
AND outside the door ROI. If no interior zone is saved, the whole unmasked view
outside the door ROI is used. Do not include an outside corridor in that view.
Door ROI detections prevent premature clearing while a person moves to the exit.
Run the Production tab only after setup; disable the Maintenance tab's competing
camera/model nodes. Restart/deploy production after saving device/ROI settings.
The original Production model is bundled YOLO11n; the original Maintenance model
path is /userdata/models/person_only.cvimodel. Verify the selected models exist.
Camera-side crossing logic is unchanged, including the existing whole-polygon
transition behaviour rather than strict selected-edge crossing enforcement.

COUNTING AND OUTPUT BEHAVIOUR
Normal countedOccupancy = clamp(MAX camera entries - MAX camera exits, 0, 50).
Processed totals and all camera counters keep that original logic.
When normal occupancy is positive, effective currentOccupancy is that count.
When normal occupancy is zero and inside presence is confirmed, effective
currentOccupancy is 1. Presence never adds a synthetic ENTRY, changes a raw
counter, or reduces a positive normal count to one.
An EXIT while normal countedOccupancy is zero remains ignored by normal counting.
The independent presence latch clears on fresh confirmed-clear data OR the usual
operator count-reset action. The displayed/MQTT occupancy is 1 while latched,
although raw MAX ENTRY/MAX EXIT counters are never changed by the correction.

POST-EXIT RULE (firmware 1.7.3-cell-6-latched-count-reset)
A NEW presence correction is accepted only in an eight-second window after an
accepted EXIT takes the NORMAL count from a positive value to ZERO. Ignored EXIT
messages at zero, duplicate reports which do not clear the normal count, boot,
and manual count-clear do not open or restart the window.

The inside camera must show fresh qualifying detections continuously for MORE
THAN two seconds AFTER that EXIT, and the camera must also confirm >2s presence.
The ESP's post-EXIT confirmation must finish no later than EXIT + 8000ms.
A streak beginning late and completing after the deadline is rejected.
At exactly 8000ms an already-completed confirmation may be accepted; after that,
no new correction can start. New detections outside this window are ignored for
presence correction. They are not classified as a robot by this rule; timing
cannot identify the detected object.

An already-accepted correction stays latched beyond eight seconds until fresh
camera clear (two seconds with no person in interior/door ROI) OR operator reset. It must NOT expire
just because the window closes if someone may still be inside. Missing or invalid
messages break a candidate streak and cannot erase an already-accepted presence.

The robot remains disabled during the post-EXIT check with the original blinking
clearing indication. The original five-second vacancy delay remains a MINIMUM;
release waits until the eight-second window closes too. An accepted presence
returns the output to steady occupied red. A later clear starts the usual vacancy
delay again. This avoids enabling robot motion while presence is being checked.

Outside the window, with normal count zero and no accepted presence latch,
missing/pending/late inside detection no longer creates the old indefinite steady
red interlock. CAMERA_FAILURE_TRIGGERS_FAULT=0 retains its original diagnostic-only
camera-offline policy. Invalid camera ROLE configuration or Ethernet faults still
block operation, and positive NORMAL counts still mean occupied regardless of
this timing rule. Original maintenance mode remains an explicit operator bypass.

This deliberately removes the unlimited empty-count presence correction: at boot
or without a qualifying counted EXIT there is NO presence-only correction. A real
person appearing after the eight-second deadline is ignored by this fallback too.
Normal ENTRY/EXIT counting stays active and unchanged at all times. In particular,
a model false-positive ENTRY can still increase the NORMAL count; the eight-second
rule applies to the independent presence fallback, not to normal crossing events.

OPERATOR COUNT RESET
Hold GREEN for at least 5 seconds, then release BEFORE 10 seconds, exactly as
in the original programme. This now clears BOTH the normal counters and the
presence correction. The count returns to zero; the existing five-second
vacancy delay still runs before robot release. The old post-EXIT window is
closed and candidate continuity cleared, so the same ongoing detection cannot
immediately restore occupancy to one. A later accepted counted EXIT can open a
new eight-second window normally. A 10-second hold still toggles maintenance;
a short start acknowledgement does not clear counts in RUN.

WHAT STAYS THE SAME
Actual GPIO macros: W5500 13/12/11/14, IRQ/RST -1; red21, green18, yellow17,
robot16, green NC button47. Old contradictory header comments were corrected.
Static IP 192.168.10.1, MQTT broker, 120-second startup, start acknowledgement,
button debounce/holds, maintenance toggle, 5-second vacancy delay, Ethernet fault
and recovery timing, watchdog, all states and individual relay output helpers.
File logging/persistence remain disabled by default but their original functions
and compile-time options are retained. Full serial tables and commands remain.
The original loop and state-engine scheduling remain. In particular, status is
still scheduled from the existing 2-second watchdog aggregation, even though
STATUS_INTERVAL_MS is 1000. This is preserved from your attachment.

STATUS AND KIOSK COMPATIBILITY
eagle/system/status occupancy now means effective occupancy.
Added: cell="Cell 6", counted_occupancy, presence_override,
presence_ready_for_empty, vacancy_clear_active, camera_roles_valid, and
per-camera presence state, post_exit_window_open, and
presence_ignored_outside_window. total_in/total_out/total_net remain normal totals.
The occupancy-triggered kiosk uses effective occupancy and clear-delay fields.
Outgoing JSON buffer is enlarged to 4096 and checked to avoid truncation.
Inside messages above 512 bytes or invalid JSON/schema are rejected; invalid
data cannot clear an existing presence latch or establish a clear cell.

REVIEW AND VALIDATION
python build_cell_6.py regenerates this sketch and changes.patch from the retained
source. It resets the delivered role fields to blank: edit IDs AFTER rebuilding.
python test_cell_6.py verifies preservation of original functions/settings and
compatibility with the companion presence message fields.
window_compile_checks.cpp checks 13 timing/state scenarios in the C++ compiler
using the SAME gate class as production. No runtime device I/O is performed.
See VALIDATION.txt for compiler results and the limits of local verification.
No device has been flashed by this task. Before enabling the robot, commission
the configured roles, zones, occupied/clear transitions, manual reset, maintenance,
camera/network loss, relay wiring and recovery on the actual installation.
Clear camera images cannot establish that an occluded person physically exited.
