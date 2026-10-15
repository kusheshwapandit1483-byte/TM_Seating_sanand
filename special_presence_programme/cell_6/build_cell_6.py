"""Apply narrowly scoped presence additions to the exact supplied production source."""
from pathlib import Path
import difflib
import re

ROOT = Path(__file__).resolve().parent
original = (ROOT / 'reference_original.txt').read_text(encoding='utf-8-sig')
code = original

def replace(old, new):
    global code
    if code.count(old) != 1:
        raise ValueError(f'Expected one source anchor, got {code.count(old)}: {old[:100]}')
    code = code.replace(old, new, 1)

def edit_function(name, old, new):
    global code
    signature = re.search(r'(?m)^(?:void|int|bool)\s+' + re.escape(name) + r'\(', code)
    if not signature:
        raise ValueError('Missing function definition: ' + name)
    start = signature.start()
    # Anchors are restricted to this function, avoiding unrelated reset paths.
    opening = code.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (code[end] == '{') - (code[end] == '}')
        end += 1
    body = code[start:end]
    if body.count(old) != 1:
        raise ValueError(f'Expected one anchor in {name}: {old[:80]}')
    code = code[:start] + body.replace(old, new, 1) + code[end:]

replace(' * Waveshare ESP32-S3-ETH variant with onboard W5500 Ethernet.',
        ' * CELL 6 — full supplied production programme with independent interior presence.\n'
        ' * Waveshare ESP32-S3-ETH variant with onboard W5500 Ethernet.')
replace(' *   RST  GPIO9\n *   INT  GPIO10',
        ' *   RST/INT disabled in this programme (ETH_SPI_RST/IRQ = -1)')
replace(' *   RED + Buzzer : GPIO6\n *   GREEN        : GPIO43\n *   YELLOW       : GPIO4\n *   HEALTHY/MAIN : GPIO1',
        ' *   RED + Buzzer : GPIO21\n *   GREEN        : GPIO18\n *   YELLOW       : GPIO17\n *   HEALTHY/MAIN : GPIO16')
replace(' *   GPIO3 ADC input through optocoupler workaround', ' *   GPIO47 digital NC green button input')
replace('#define FW_VERSION "1.6.0-max-exit-nc-camera-fail-continue"',
        '#define FW_VERSION "1.7.2-cell-6-post-exit-8s"')
replace('#include <SPIFFS.h>', '#include <SPIFFS.h>\n#include "cell_6_presence_window.h"')
replace('const char DEVICE_ID[] = "composite_hub";', '''// ============== CELL 6 CAMERA CONFIGURATION ==============
// SET THESE TO YOUR PHYSICAL CAMERA IDs BEFORE UPLOADING.
// Both cameras must use door E1 in their reCamera Device Identity configuration.
// Blank defaults intentionally prevent RUN until the inside/outside roles are assigned.
#define CELL_6_INSIDE_CAMERA_ID  ""
#define CELL_6_OUTSIDE_CAMERA_ID ""
#define PRESENCE_CONFIRM_MS 2000UL          // >2s camera AND post-EXIT ESP confirmation
#define PRESENCE_SOURCE_MAX_AGE_MS 1000UL   // Camera's reported source-frame age
#define PRESENCE_MESSAGE_TIMEOUT_MS 1500UL // No fresh presence message = unknown

const char DEVICE_ID[] = "composite_hub";''')
replace('     { "TM_R1_01", "E1" },\n    { "TM_R1_02", "E1" },',
        '    { CELL_6_INSIDE_CAMERA_ID, "E1" },\n    { CELL_6_OUTSIDE_CAMERA_ID, "E1" },')
replace('  unsigned long disconnectedSince;\n};', '''  unsigned long disconnectedSince;

  // Independent presence state. Never substitutes for camera ENTRY/EXIT counters.
  bool isInsideCamera;
  bool presenceSeen;
  bool presenceValid;
  bool presenceLatched;
  bool presenceClear;
  bool presenceIgnoredLate;
  Cell6PresenceWindow presenceWindow;
  unsigned long lastPresence;
  long presenceFrameAge;
  char presenceState[12];
};''')
replace('int currentOccupancy = 0;', '''int currentOccupancy = 0;       // Effective occupancy used by relays/dashboard
int countedOccupancy = 0;       // Unmodified MAX ENTRY - MAX EXIT result
bool presenceOverride = false;
bool cameraRolesValid = false;''')
replace('unsigned long msgCountHeartbeat = 0;',
        'unsigned long msgCountHeartbeat = 0;\nunsigned long msgCountPresence = 0;\nunsigned long errPresence = 0;')
replace('char pubBuf[1024];', 'char pubBuf[4096]; // Extra presence diagnostics; checked before serializing')
edit_function('addCamera', '  memset(&cameras[idx], 0, sizeof(CameraState));', '''  memset(&cameras[idx], 0, sizeof(CameraState));
  cameras[idx].isInsideCamera = strcmp(id, CELL_6_INSIDE_CAMERA_ID) == 0;
  cameras[idx].presenceWindow = Cell6PresenceWindow{};
  cameras[idx].presenceFrameAge = -1;
  strcpy(cameras[idx].presenceState, "unknown");''')

helpers = r'''// ============== CELL 6 INDEPENDENT INTERIOR PRESENCE ==============
// The companion reCamera flow observes fresh, unmasked detections outside the
// door ROI (within its optional interior polygon), confirms >2s presence and
// confirms clear after 2s without a person in either interior or door ROI.
// Only the configured INSIDE camera is authoritative here. NEW corrections can
// begin only during the 8s window after a counted EXIT leaves normal occupancy 0.

bool insidePresenceFresh(const CameraState& cam, unsigned long now) {
  return cam.presenceSeen && cam.presenceValid &&
         now - cam.lastPresence <= PRESENCE_MESSAGE_TIMEOUT_MS;
}

bool anyInsidePresenceLatched() {
  for (int i = 0; i < cameraCount; i++) {
    if (cameras[i].isInsideCamera && cameras[i].presenceLatched) return true;
  }
  return false;
}

bool insidePresenceReadyForEmpty(unsigned long now) {
  if (!cameraRolesValid) return false;
  bool foundInside = false;
  for (int i = 0; i < cameraCount; i++) {
    CameraState& cam = cameras[i];
    if (!cam.isInsideCamera) continue;
    foundInside = true;
    cam.presenceWindow.expire((uint32_t)now);
    // Late/unknown detections do not create an all-day red interlock. An already
    // accepted correction remains occupied until fresh clear; the 8s check must finish.
    if (cam.presenceLatched || cam.presenceWindow.open(now)) return false;
  }
  return foundInside;
}

void armInsidePostExitWindow(unsigned long now) {
  for (int i = 0; i < cameraCount; i++) {
    if (!cameras[i].isInsideCamera) continue;
    cameras[i].presenceWindow.arm((uint32_t)now);
    cameras[i].presenceIgnoredLate = false;
    logEvent("POST_EXIT_WINDOW", cameraIds[i]);
  }
}

bool validateCell6CameraRoles() {
  const char* inside = CELL_6_INSIDE_CAMERA_ID;
  const char* outside = CELL_6_OUTSIDE_CAMERA_ID;
  if (!inside[0] || !outside[0] || strlen(inside) >= sizeof(cameraIds[0]) ||
      strlen(outside) >= sizeof(cameraIds[0]) || strcmp(inside, outside) == 0) return false;
  if (strchr(inside, '/') || strchr(inside, '+') || strchr(inside, '#') ||
      strchr(outside, '/') || strchr(outside, '+') || strchr(outside, '#')) return false;
  if (strcmp(inside, "unknown") == 0 || strcmp(inside, "system") == 0 ||
      strcmp(inside, "controller") == 0 || strcmp(inside, DEVICE_ID) == 0 ||
      strcmp(outside, "unknown") == 0 || strcmp(outside, "system") == 0 ||
      strcmp(outside, "controller") == 0 || strcmp(outside, DEVICE_ID) == 0) return false;
  if (EXPECTED_CAMERA_COUNT != 2 || cameraCount != 2) return false;
  int inIdx = findCamera(inside), outIdx = findCamera(outside);
  return inIdx >= 0 && outIdx >= 0 && inIdx != outIdx &&
         strcmp(cameras[inIdx].doorLabel, cameras[outIdx].doorLabel) == 0;
}

void invalidateInsidePresence(int idx) {
  CameraState& cam = cameras[idx];
  cam.presenceValid = false;
  cam.presenceClear = false;
  cam.presenceWindow.breakContinuity();
  strcpy(cam.presenceState, "unknown");
  // Unknown/invalid data must not erase an already-confirmed person.
  refreshRunOutputs();
}

void handleInsidePresence(int idx, const char* payload, unsigned long now) {
  CameraState& cam = cameras[idx];
  if (!cam.isInsideCamera) return; // Outside camera presence cannot alter occupancy.
  msgCountPresence++;
  if (strlen(payload) > MAX_PAYLOAD_BYTES) {
    errOversizedPayload++;
    invalidateInsidePresence(idx);
    return;
  }
  JsonDocument doc;
  if (deserializeJson(doc, payload)) {
    errJsonParse++;
    invalidateInsidePresence(idx);
    return;
  }
  if (!doc["valid"].is<bool>() || !doc["present"].is<bool>() ||
      !doc["state"].is<const char*>() || !doc["frame_age_ms"].is<long>() ||
      !doc["candidates"].is<unsigned int>()) {
    errPresence++;
    invalidateInsidePresence(idx);
    return;
  }
  const char* state = doc["state"];
  bool present = doc["present"].as<bool>();
  bool isPresent = strcmp(state, "present") == 0;
  bool isClear = strcmp(state, "clear") == 0;
  bool isPending = strcmp(state, "pending") == 0;
  bool isUnknown = strcmp(state, "unknown") == 0;
  bool candidate = doc["candidates"].as<unsigned int>() > 0;
  if ((!isPresent && !isClear && !isPending && !isUnknown) ||
      (isPresent != present && !isUnknown) || (isClear && candidate)) {
    errPresence++;
    invalidateInsidePresence(idx);
    return;
  }
  long frameAge = doc["frame_age_ms"].as<long>();
  bool valid = doc["valid"].as<bool>() && !isUnknown && frameAge >= 0 &&
               (unsigned long)frameAge <= PRESENCE_SOURCE_MAX_AGE_MS;
  // Retained duration proves >2s even if the person has since moved into door ROI.
  unsigned long confirmedMs = doc["confirmed_ms"] | 0UL;
  if (valid && isPresent &&
      (!doc["confirmed_ms"].is<unsigned long>() || confirmedMs <= PRESENCE_CONFIRM_MS)) {
    errPresence++;
    invalidateInsidePresence(idx);
    return;
  }
  bool wasLatched = cam.presenceLatched;
  cam.presenceSeen = true;
  cam.presenceValid = valid;
  cam.presenceClear = valid && isClear && !present;
  cam.lastPresence = now;
  cam.presenceFrameAge = frameAge;
  strncpy(cam.presenceState, valid ? state : "unknown", sizeof(cam.presenceState) - 1);
  cam.presenceState[sizeof(cam.presenceState) - 1] = '\0';
  // Camera's retained confirmation alone is insufficient: the ESP observes a
  // NEW >2s candidate streak after EXIT, wholly inside the 8s acceptance window.
  cam.presenceWindow.observe((uint32_t)now, valid, candidate,
                             isPresent && present && confirmedMs > PRESENCE_CONFIRM_MS,
                             cam.presenceClear);
  cam.presenceLatched = cam.presenceWindow.latched;
  bool ignoredLate = valid && candidate && !cam.presenceWindow.open((uint32_t)now) &&
                     !cam.presenceLatched;
  if (ignoredLate && !cam.presenceIgnoredLate) logEvent("PRESENCE_IGNORED_OUTSIDE_WINDOW", cameraIds[idx]);
  cam.presenceIgnoredLate = ignoredLate;
  if (wasLatched != cam.presenceLatched) {
    logEvent(cam.presenceLatched ? "PRESENCE_ON" : "PRESENCE_CLEAR", cameraIds[idx]);
  }
  recomputeOccupancyFromCameraTotals();
  refreshRunOutputs();
}

'''
replace('// ============== COUNTER PERSISTENCE ==============', helpers + '// ============== COUNTER PERSISTENCE ==============')
edit_function('recomputeOccupancyFromCameraTotals', '  currentOccupancy = (int)net;', '''  countedOccupancy = (int)net;
  presenceOverride = countedOccupancy == 0 && anyInsidePresenceLatched();
  currentOccupancy = presenceOverride ? 1 : countedOccupancy;''')
edit_function('resetSessionCountsForVacancy', '  for (int i = 0; i < cameraCount; i++) {', '''  // Do not erase a session while the independent interior check is occupied/unknown.
  if (currentOccupancy != 0 || !insidePresenceReadyForEmpty(millis())) return;
  for (int i = 0; i < cameraCount; i++) {''')
edit_function('resetSessionCountsForVacancy', '  currentOccupancy = 0;', '  recomputeOccupancyFromCameraTotals();')
edit_function('applyOutputs', '''      } else {
        roomWasOccupied = false;''', '''      } else if (!insidePresenceReadyForEmpty(millis())) {
        roomWasOccupied = true;
        vacancyClearActive = false;
        refreshRunOutputs(); // Blink while checking the post-EXIT window; no false steady red.
      } else {
        roomWasOccupied = false;''')
edit_function('refreshRunOutputs', '  if (roomWasOccupied && !vacancyClearActive) {', '''  if (!insidePresenceReadyForEmpty(millis()) && !roomWasOccupied) {
    roomWasOccupied = true;
  }

  if (roomWasOccupied && !vacancyClearActive) {''')
edit_function('updateRunOutputTimers', '  if (currentState != STATE_RUN || !vacancyClearActive) return;', '''  if (currentState != STATE_RUN) return;
  // Check message freshness on every loop, including when no new MQTT data arrives.
  refreshRunOutputs();
  if (!vacancyClearActive) return;''')
edit_function('updateRunOutputTimers', '  if (now - vacancyClearStarted >= VACANCY_CLEAR_MS) {', '''  // The original 5s minimum remains. An open 8s post-EXIT window extends it.
  if (now - vacancyClearStarted >= VACANCY_CLEAR_MS && insidePresenceReadyForEmpty(now)) {
    unsigned long sessionIn = 0, sessionOut = 0;
    computeProcessedTotals(&sessionIn, &sessionOut);
    if (sessionIn || sessionOut) resetSessionCountsForVacancy("post_exit_check_complete");''')
edit_function('occupancyRelayActive', '          !vacancyClearActive);',
              '          !vacancyClearActive &&\n          insidePresenceReadyForEmpty(millis()));')
edit_function('onMqttMessage', '  unsigned long now = millis();', '''  unsigned long now = millis();

  // Presence ticks do not substitute for a camera heartbeat or crossing event.
  if (topicEndsWith(topic, "/presence")) {
    handleInsidePresence(idx, payload, now);
    return;
  }''')
edit_function('onMqttMessage', '      if (currentOccupancy == 0) {', '''      // Presence supplies a floor, not a synthetic ENTRY. Preserve normal zero-count EXIT behaviour.
      if (countedOccupancy == 0) {''')
edit_function('onMqttMessage', 'Ignored EXIT while occupancy is already zero from ',
              'Ignored EXIT while normal counted occupancy is already zero from ')
edit_function('onMqttMessage', '''      cameras[idx].exits++;
      recomputeOccupancyFromCameraTotals();''', '''      cameras[idx].exits++;
      recomputeOccupancyFromCameraTotals();
      // Only an accepted EXIT taking the normal count to zero opens the window.
      // Duplicate/ignored EXITs at zero do not restart the 8s deadline.
      if (countedOccupancy == 0) armInsidePostExitWindow(now);''')
edit_function('clearBootCounts', '  currentOccupancy = 0;', '''  currentOccupancy = 0;
  countedOccupancy = 0;
  presenceOverride = false;''')
edit_function('handleClearCountHold', '  currentOccupancy = 0;', '''  // Clearing counters cannot clear a confirmed person from the interior camera.
  recomputeOccupancyFromCameraTotals();''')
edit_function('runAggregation', '  bool anyFault = false;', '  bool anyFault = !cameraRolesValid;')
edit_function('runWatchdog', '    if (CAMERA_FAILURE_TRIGGERS_FAULT && missing > 0) {',
              '    if (!cameraRolesValid || (CAMERA_FAILURE_TRIGGERS_FAULT && missing > 0)) {')
edit_function('publishSystemStatus', '  pubDoc["occupancy"] = currentOccupancy;', '''  pubDoc["occupancy"] = currentOccupancy;
  pubDoc["cell"] = "Cell 6";
  pubDoc["counted_occupancy"] = countedOccupancy;
  pubDoc["presence_override"] = presenceOverride;
  pubDoc["presence_ready_for_empty"] = insidePresenceReadyForEmpty(now);
  pubDoc["vacancy_clear_active"] = vacancyClearActive;
  pubDoc["camera_roles_valid"] = cameraRolesValid;''')
edit_function('publishSystemStatus', '    cam["disconnected"] = cameras[i].disconnected;', '''    cam["disconnected"] = cameras[i].disconnected;
    cam["role"] = cameras[i].isInsideCamera ? "inside" : "outside";
    if (cameras[i].isInsideCamera) {
      cam["presence_state"] = insidePresenceFresh(cameras[i], now) ? cameras[i].presenceState : "unknown";
      cam["presence_fresh"] = insidePresenceFresh(cameras[i], now);
      cam["presence_latched"] = cameras[i].presenceLatched;
      cam["post_exit_window_open"] = cameras[i].presenceWindow.open((uint32_t)now);
      cam["presence_ignored_outside_window"] = cameras[i].presenceIgnoredLate;
    }''')
for function in ('publishSystemStatus', 'publishDiagnostics'):
    edit_function(function, '  serializeJson(pubDoc, pubBuf, sizeof(pubBuf));', '''  if (measureJson(pubDoc) >= sizeof(pubBuf)) {
    logEvent("PUBLISH_ERROR", "JSON output buffer too small");
    return;
  }
  serializeJson(pubDoc, pubBuf, sizeof(pubBuf));''')
edit_function('publishDiagnostics', '  pubDoc["occupancy"] = currentOccupancy;', '''  pubDoc["occupancy"] = currentOccupancy;
  pubDoc["cell"] = "Cell 6";
  pubDoc["counted_occupancy"] = countedOccupancy;
  pubDoc["presence_override"] = presenceOverride;
  pubDoc["presence_ready_for_empty"] = insidePresenceReadyForEmpty(now);
  pubDoc["camera_roles_valid"] = cameraRolesValid;''')
edit_function('publishDiagnostics', '  msgs["hb"] = msgCountHeartbeat;',
              '  msgs["hb"] = msgCountHeartbeat;\n  msgs["presence"] = msgCountPresence;')
edit_function('publishDiagnostics', '  errs["unknown"] = errUnknownEvent;',
              '  errs["unknown"] = errUnknownEvent;\n  errs["presence"] = errPresence;')
edit_function('printSerialMonitor', '  Serial.println(F("[CAMERAS] raw per-camera data"));', '''  Serial.printf("[CELL 6] counted=%d effective=%d presence_override=%s clear_ready=%s roles_valid=%s presence_msgs=%lu errors=%lu\\n",
                countedOccupancy, currentOccupancy, presenceOverride ? "YES" : "NO",
                insidePresenceReadyForEmpty(now) ? "YES" : "NO", cameraRolesValid ? "YES" : "NO",
                msgCountPresence, errPresence);
  for (int i = 0; i < cameraCount; i++) {
    if (!cameras[i].isInsideCamera) continue;
    Serial.printf("[INTERIOR] id=%s state=%s latched=%s age_ms=%ld\\n", cameraIds[i],
                  insidePresenceFresh(cameras[i], now) ? cameras[i].presenceState : "unknown",
                  cameras[i].presenceLatched ? "YES" : "NO",
                  cameras[i].presenceSeen ? (long)(now - cameras[i].lastPresence) : -1L);
  }
  Serial.println(F("[CAMERAS] raw per-camera data"));''')
edit_function('printSerialMonitor', 'subscribed=eagle/+/event,eagle/+/heartbeat events=',
              'subscribed=eagle/+/event,eagle/+/heartbeat,eagle/+/presence events=')
edit_function('setup', '  Serial.begin(115200);', '  Serial.begin(115200); // Cell 6 — full original programme retained')
edit_function('setup', '  Serial.println(F("[MQTT] Subscribed: event + heartbeat"));', '''  mqttBroker.subscribe("eagle/+/presence", [](const char* topic, const char* payload) {
    onMqttMessage(topic, payload);
  });
  Serial.println(F("[MQTT] Subscribed: event + heartbeat + presence"));''')
edit_function('setup', '  // Start every boot/demo from zero counts.', '''  cameraRolesValid = validateCell6CameraRoles();
  if (!cameraRolesValid) {
    logEvent("CONFIG_ERROR", "Cell 6: set distinct inside/outside camera IDs before use");
    Serial.println(F("[CELL 6] CAMERA ROLES INVALID — robot will remain disabled outside maintenance mode"));
  } else {
    Serial.print(F("[CELL 6] INSIDE=")); Serial.println(CELL_6_INSIDE_CAMERA_ID);
    Serial.print(F("[CELL 6] OUTSIDE=")); Serial.println(CELL_6_OUTSIDE_CAMERA_ID);
  }

  // Start every boot/demo from zero counts.''')

(ROOT / 'cell_6.ino').write_text(code, encoding='utf-8')
diff = ''.join(difflib.unified_diff(original.splitlines(True), code.splitlines(True),
                                  fromfile='reference_original.txt', tofile='cell_6.ino'))
(ROOT / 'changes.patch').write_text(diff, encoding='utf-8')
print(f'Cell 6: original {len(original.splitlines())} lines -> updated {len(code.splitlines())} lines')
