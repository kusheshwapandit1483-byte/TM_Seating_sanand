CELL 6 — FULL PRODUCTION SOURCE WITH INTERIOR PRESENCE

Open cell_6.ino in Arduino IDE. The sketch folder and sketch filename match.
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
Source size: original 1,505 lines; Cell 6 1,729 lines.
Original functions: 42. All retained. 26 unchanged, 16 extended. 6 new helpers.
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
The independent presence latch then clears only on fresh confirmed-clear data.
Count-clear cannot erase a confirmed inside person or bypass stale presence.

The reCamera confirms presence after MORE THAN 2 seconds of qualifying fresh
detections, and confirms clear after 2 seconds without accepted detections in
the interior or door ROI. ESP32 checks the retained confirmed_ms duration and
accepts presence only from CELL_6_INSIDE_CAMERA_ID. Outside presence is ignored.
The original 5-second vacancy timer starts only at effective zero AND confirmed
interior clear. Occupancy, pending detection, or stale data cancels that timer.
Pending/unknown/stale interior data keeps RUN outputs robot-disabled. Existing
presence remains latched through a communication/inference loss.
This new release interlock applies even though CAMERA_FAILURE_TRIGGERS_FAULT=0
still keeps other camera-offline reporting diagnostic-only, as originally set.
The existing maintenance mode bypass remains unchanged.

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
per-camera presence state. total_in/total_out/total_net remain normal totals.
The occupancy-triggered kiosk uses effective occupancy and clear-delay fields.
Outgoing JSON buffer is enlarged to 4096 and checked to avoid truncation.
Inside messages above 512 bytes or invalid JSON/schema are rejected; invalid
data cannot clear an existing presence latch or establish a clear cell.

REVIEW AND VALIDATION
python build_cell_6.py regenerates this sketch and changes.patch from the retained
source. It resets the delivered role fields to blank: edit IDs AFTER rebuilding.
python test_cell_6.py verifies preservation of original functions/settings and
compatibility with the companion presence message fields.
See VALIDATION.txt for compiler results and the limits of local verification.
No device has been flashed by this task. Before enabling the robot, commission
the configured roles, zones, occupied/clear transitions, manual reset, maintenance,
camera/network loss, relay wiring and recovery on the actual installation.
Clear camera images cannot establish that an occluded person physically exited.
