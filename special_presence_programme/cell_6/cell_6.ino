/*
 * CELL 6 — full supplied production programme with independent interior presence.
 * Waveshare ESP32-S3-ETH variant with onboard W5500 Ethernet.
 * Uses GPIO numbers directly.
 *
 * Ethernet W5500 SPI:
 *   SCK  GPIO13
 *   MISO GPIO12
 *   MOSI GPIO11
 *   CS   GPIO14
 *   RST/INT disabled in this programme (ETH_SPI_RST/IRQ = -1)
 *
 * Relay outputs, current installation mapping:
 *   RED + Buzzer : GPIO21
 *   GREEN        : GPIO18
 *   YELLOW       : GPIO17
 *   HEALTHY/MAIN : GPIO16
 *
 * Reset/start input:
 *   GPIO47 digital NC green button input
 */

#include <SPI.h>
#include <ETH.h>
#include <PicoMQTT.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <SPIFFS.h>
#include "cell_6_presence_window.h"

// ============== FIRMWARE VERSION ==============
#define FW_VERSION "1.7.2-cell-6-post-exit-8s"

// ============== W5500 SPI PINS ==============
#define ETH_SPI_SCK   13
#define ETH_SPI_MISO  12
#define ETH_SPI_MOSI  11
#define ETH_SPI_CS    14
#define ETH_SPI_IRQ   -1
#define ETH_SPI_RST   -1


// ============== STATIC IP ==============
IPAddress staticIP(192, 168, 10, 1);
IPAddress gatewayIP(192, 168, 10, 1);
IPAddress subnetMask(255, 255, 255, 0);
IPAddress dnsIP(0, 0, 0, 0);

// ============== RELAY OUTPUTS (active HIGH) ==============
#define PIN_RELAY_RED_BUZZER  21 // Relay 1: RED lamp + Buzzer
#define PIN_RELAY_GREEN       18 // Relay 3: GREEN lamp (cabin empty)
#define PIN_RELAY_YELLOW      17   // Relay 2: YELLOW lamp (startup / await start)
#define PIN_RELAY_OCCUPANCY   16  // Relay 4: HEALTHY output (main)
#define ROBOT_DISABLE_LEVEL    HIGH // Energized relay opens NC contact: robot disabled
#define ROBOT_ENABLE_LEVEL     LOW  // De-energized relay closes NC contact: robot enabled

// ============== BUTTON INPUTS ==============
#define PIN_BTN_GREEN         47    // Green button input, NC contact
#define BTN_GREEN_PRESSED_LEVEL HIGH // NC: normal/closed=LOW, pressed/open=HIGH

// ============== TIMING CONSTANTS ==============
#define FAULT_TIMEOUT_MS      8000     // 8s missed heartbeat = camera fault (hb interval is 2s)
#define ETH_DOWN_FAULT_MS     3000     // 3s own-ETH link down = immediate FAULT
#define STARTUP_SETTLE_MS     120000    // 120s boot settling
#define BTN_DEBOUNCE_MS       50       // Button debounce
#define CLEAR_COUNT_HOLD_MS   5000     // Hold GREEN 5s, release before 10s to clear counts
#define MAINT_HOLD_MS         10000    // Hold GREEN 10s to toggle maintenance mode
#define DIAG_INTERVAL_MS      60000    // Diagnostics publish every 60s
#define STATUS_INTERVAL_MS    1000     // Status publish every 1s
#define WATCHDOG_MS           2000     // Watchdog check interval
#define TABLE_INTERVAL_MS     2000     // Serial status table refresh
#define ETH_RECOVERY_MS       30000    // Ethernet re-init after this long down
#define HW_WDT_TIMEOUT_S      8        // Hardware watchdog timeout
#define MAX_PAYLOAD_BYTES     512      // Max MQTT payload to parse
#define MAX_OCCUPANCY         50       // Sanity clamp on counters
#define RAW_MQTT_DEBUG        1        // Debug: print incoming camera MQTT messages
#define RAW_STATUS_DEBUG      1        // Debug: print outgoing status/diag MQTT payloads
#define SERIAL_MONITOR_ENABLED 1       // Debug: print full live system monitor frames
#define SERIAL_MONITOR_INTERVAL_MS 2000 // Full serial monitor refresh interval
#define BYPASS_AWAIT_START    0        // Require GREEN push button after startup/fault recovery
#define CAMERA_FAILURE_TRIGGERS_FAULT 0 // Camera offline is diagnostic only; keep operating

// Buzzer/alarm patterns
#define FAULT_PULSE_ON_MS     1000     // Fault: 1s on
#define FAULT_PULSE_OFF_MS    1000     // Fault: 1s off
#define RUN_RED_BLINK_MS      500      // Red blink half-period during vacancy clear delay
#define VACANCY_CLEAR_MS      5000     // After occupancy reaches 0, wait 5s before clearing output
// Persistence
#define COUNT_PERSISTENCE_ENABLED 0    // Production: live counts only; no flash writes
#define COUNTS_PATH           "/counts.json"
#define COUNTS_SAVE_MS        5000     // Used only when count persistence is enabled

#define AWAIT_BLINK_MS        500      // Yellow blink half-period in AWAIT_START

// Logging
#define FILE_LOGGING_ENABLED  0        // Production: no /log files on SPIFFS
#define LOG_MAX_BYTES         1048576  // 1MB log rotation
#define LOG_PATH              "/log/events.csv"
#define LOG_PREV_PATH         "/log/events_prev.csv"

// ============== CELL 6 CAMERA CONFIGURATION ==============
// SET THESE TO YOUR PHYSICAL CAMERA IDs BEFORE UPLOADING.
// Both cameras must use door E1 in their reCamera Device Identity configuration.
// Blank defaults intentionally prevent RUN until the inside/outside roles are assigned.
#define CELL_6_INSIDE_CAMERA_ID  ""
#define CELL_6_OUTSIDE_CAMERA_ID ""
#define PRESENCE_CONFIRM_MS 2000UL          // >2s camera AND post-EXIT ESP confirmation
#define PRESENCE_SOURCE_MAX_AGE_MS 1000UL   // Camera's reported source-frame age
#define PRESENCE_MESSAGE_TIMEOUT_MS 1500UL // No fresh presence message = unknown

const char DEVICE_ID[] = "composite_hub";

// ============== EXPECTED CAMERA ROSTER ==============
// Edit at deployment. System FAULTs on boot if any of these are not reporting.
struct ExpectedCamera { const char* id; const char* doorLabel; };
const ExpectedCamera EXPECTED_CAMERAS[] = {
    { CELL_6_INSIDE_CAMERA_ID, "E1" },
    { CELL_6_OUTSIDE_CAMERA_ID, "E1" },
};
#define EXPECTED_CAMERA_COUNT (sizeof(EXPECTED_CAMERAS) / sizeof(EXPECTED_CAMERAS[0]))

// ============== SYSTEM STATES ==============
enum SystemState {
  STATE_STARTUP,      // Boot settling, all relays OFF
  STATE_AWAIT_START,  // Settled - waiting for operator reset/start press before counting begins
  STATE_RUN,          // Normal operation, counting active
  STATE_FAULT,        // Camera or ETH lost, RED+BUZZER pulsing
  STATE_MAINTENANCE   // Maintenance bypass: robot enabled, GREEN+YELLOW steady
};

// ============== GLOBALS ==============

PicoMQTT::Server mqttBroker;

// Ethernet
bool ethConnected = false;
unsigned long ethDownSince = 0;
bool ethDownTiming = false;

// Camera tracking
struct CameraState {
  char doorLabel[8];          // Cameras with the same label are one physical door
  unsigned long entries;      // Total ENTRY events from this camera
  unsigned long exits;        // Total EXIT events from this camera
  unsigned long lastEvent;    // Timestamp of last event
  unsigned long lastHb;       // Timestamp of last heartbeat
  bool hasSeen;               // Have we ever heard from this camera?
  bool disconnected;          // Currently considered offline?
  unsigned long disconnectedSince;

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
};

#define MAX_CAMERAS 4
CameraState cameras[MAX_CAMERAS];
char cameraIds[MAX_CAMERAS][32];
int cameraCount = 0;

struct DoorTotals {
  char label[8];
  unsigned long entries;
  unsigned long exits;
  unsigned long maxExit;
};

// System state
SystemState currentState = STATE_STARTUP;
SystemState prevState = STATE_STARTUP;
bool startupSettling = true;
unsigned long systemBootTime = 0;
int expectedCameras = 0;

// Persistence dirty flag
bool countsDirty = false;
unsigned long lastCountsSave = 0;

// Aggregation
int currentOccupancy = 0;       // Effective occupancy used by relays/dashboard
int countedOccupancy = 0;       // Unmodified MAX ENTRY - MAX EXIT result
bool presenceOverride = false;
bool cameraRolesValid = false;
unsigned long lastStatusPublish = 0;
unsigned long lastWatchdog = 0;
unsigned long lastDiag = 0;
unsigned long lastTable = 0;
unsigned long lastSerialMonitor = 0;

// Fault pulse (RED+BUZZER pulsing)
unsigned long faultPulseTimer = 0;
bool faultPulseOn = false;

// RUN output timing
bool roomWasOccupied = false;
bool vacancyClearActive = false;
unsigned long vacancyClearStarted = 0;
unsigned long runRedBlinkTimer = 0;
bool runRedBlinkOn = false;

// Button state
bool greenPressed = false;
unsigned long greenPressStart = 0;
bool greenPressEligible = false;
bool greenPressHandled = false;

// Raw button debug state. Prints pin changes even while actions are disabled.
bool lastGreenRaw = false;

// AWAIT_START yellow-blink state
unsigned long awaitBlinkTimer = 0;
bool awaitBlinkOn = false;

// Diagnostics counters
unsigned long msgCountEvent = 0;
unsigned long msgCountHeartbeat = 0;
unsigned long msgCountPresence = 0;
unsigned long errPresence = 0;
unsigned long errJsonParse = 0;
unsigned long errOversizedPayload = 0;
unsigned long errUnknownEvent = 0;

// JSON publish buffer
JsonDocument pubDoc;
char pubBuf[4096]; // Extra presence diagnostics; checked before serializing

// State name lookup (must match SystemState enum order)
const char* stateNames[] = {
  "STARTUP", "AWAIT_START", "RUN", "FAULT", "MAINTENANCE"
};

// ============== CAMERA MANAGEMENT ==============

int findCamera(const char* id) {
  for (int i = 0; i < cameraCount; i++) {
    if (strcmp(cameraIds[i], id) == 0) return i;
  }
  return -1;
}

int addCamera(const char* id, const char* doorLabel) {
  if (cameraCount >= MAX_CAMERAS) return -1;
  int idx = cameraCount++;
  strncpy(cameraIds[idx], id, 31);
  cameraIds[idx][31] = '\0';
  memset(&cameras[idx], 0, sizeof(CameraState));
  cameras[idx].isInsideCamera = strcmp(id, CELL_6_INSIDE_CAMERA_ID) == 0;
  cameras[idx].presenceWindow = Cell6PresenceWindow{};
  cameras[idx].presenceFrameAge = -1;
  strcpy(cameras[idx].presenceState, "unknown");
  strncpy(cameras[idx].doorLabel, doorLabel, sizeof(cameras[idx].doorLabel) - 1);
  Serial.print(F("[CAM] Registered: "));
  Serial.print(id);
  Serial.print(F(" (Door "));
  Serial.print(doorLabel);
  Serial.println(F(")"));
  return idx;
}

// ============== TOPIC PARSING ==============

bool extractDeviceId(const char* topic, char* out, int maxLen) {
  out[0] = '\0';
  if (strncmp(topic, "eagle/", 6) != 0) return false;
  const char* start = topic + 6;
  const char* slash = strchr(start, '/');
  if (!slash) return false;
  int len = slash - start;
  if (len <= 0 || len >= maxLen) return false;
  memcpy(out, start, len);
  out[len] = '\0';
  return true;
}

bool topicEndsWith(const char* topic, const char* suffix) {
  int tLen = strlen(topic);
  int sLen = strlen(suffix);
  if (sLen > tLen) return false;
  return strcmp(topic + tLen - sLen, suffix) == 0;
}

// ============== EVENT LOGGING ==============

void logEvent(const char* eventType, const char* detail) {
  unsigned long now = millis();

  Serial.print(F("[LOG] "));
  Serial.print(now);
  Serial.print(F(","));
  Serial.print(eventType);
  Serial.print(F(","));
  Serial.println(detail);

#if FILE_LOGGING_ENABLED
  File f = SPIFFS.open(LOG_PATH, FILE_APPEND);
  if (f) {
    if (f.size() > LOG_MAX_BYTES) {
      f.close();
      SPIFFS.remove(LOG_PREV_PATH);
      SPIFFS.rename(LOG_PATH, LOG_PREV_PATH);
      f = SPIFFS.open(LOG_PATH, FILE_WRITE);
      if (!f) return;
    }
    char line[128];
    snprintf(line, sizeof(line), "%lu,%s,%s\n", now, eventType, detail);
    f.print(line);
    f.close();
  }
#endif
}

void exportLog() {
  Serial.println(F("=== EVENT LOG START ==="));
#if FILE_LOGGING_ENABLED
  Serial.println(F("timestamp,event_type,detail"));
  File f = SPIFFS.open(LOG_PATH, FILE_READ);
  if (f) {
    while (f.available()) {
      Serial.write(f.read());
    }
    f.close();
  } else {
    Serial.println(F("(no log file)"));
  }
#else
  Serial.println(F("File logging disabled in production firmware."));
#endif
  Serial.println(F("=== EVENT LOG END ==="));
}

// ============== CELL 6 INDEPENDENT INTERIOR PRESENCE ==============
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

// ============== COUNTER PERSISTENCE ==============

// Merge cameras on one physical door: ENTRY and EXIT both use the maximum camera count.
int buildDoorTotals(DoorTotals* doors, int maxDoors) {
  int doorCount = 0;

  for (int i = 0; i < cameraCount; i++) {
    int doorIdx = -1;
    for (int d = 0; d < doorCount; d++) {
      if (strcmp(doors[d].label, cameras[i].doorLabel) == 0) {
        doorIdx = d;
        break;
      }
    }

    if (doorIdx < 0) {
      if (doorCount >= maxDoors) continue;
      doorIdx = doorCount++;
      strncpy(doors[doorIdx].label, cameras[i].doorLabel, sizeof(doors[doorIdx].label) - 1);
      doors[doorIdx].label[sizeof(doors[doorIdx].label) - 1] = '\0';
      doors[doorIdx].entries = 0;
      doors[doorIdx].exits = cameras[i].exits;
      doors[doorIdx].maxExit = cameras[i].exits;
    }

    if (cameras[i].entries > doors[doorIdx].entries) {
      doors[doorIdx].entries = cameras[i].entries;
    }
    if (cameras[i].exits > doors[doorIdx].maxExit) {
      doors[doorIdx].maxExit = cameras[i].exits;
      doors[doorIdx].exits = cameras[i].exits;
    }
  }


  return doorCount;
}

void computeProcessedTotals(unsigned long* totalIn, unsigned long* totalOut) {
  DoorTotals doors[MAX_CAMERAS];
  int doorCount = buildDoorTotals(doors, MAX_CAMERAS);

  *totalIn = 0;
  *totalOut = 0;
  for (int i = 0; i < doorCount; i++) {
    *totalIn += doors[i].entries;
    *totalOut += doors[i].exits;
  }
}

int recomputeOccupancyFromCameraTotals() {
  unsigned long totalIn = 0;
  unsigned long totalOut = 0;
  computeProcessedTotals(&totalIn, &totalOut);

  long net = (long)totalIn - (long)totalOut;
  if (net < 0) net = 0;
  if (net > MAX_OCCUPANCY) net = MAX_OCCUPANCY;
  countedOccupancy = (int)net;
  presenceOverride = countedOccupancy == 0 && anyInsidePresenceLatched();
  currentOccupancy = presenceOverride ? 1 : countedOccupancy;
  return currentOccupancy;
}

void resetSessionCountsForVacancy(const char* reason) {
  // Do not erase a session while the independent interior check is occupied/unknown.
  if (currentOccupancy != 0 || !insidePresenceReadyForEmpty(millis())) return;
  for (int i = 0; i < cameraCount; i++) {
    cameras[i].entries = 0;
    cameras[i].exits = 0;
  }

  recomputeOccupancyFromCameraTotals();
  countsDirty = true;

  char detail[80];
  snprintf(detail, sizeof(detail), "reason=%s", reason);
  logEvent("SESSION_RESET", detail);

  Serial.print(F("[SESSION] Counts reset after vacancy: "));
  Serial.println(reason);
}

void saveCounts() {
  recomputeOccupancyFromCameraTotals();

#if COUNT_PERSISTENCE_ENABLED
  File f = SPIFFS.open(COUNTS_PATH, FILE_WRITE);
  if (!f) {
    Serial.println(F("[PERSIST] Open for write failed"));
    return;
  }
  JsonDocument doc;
  JsonArray arr = doc["cameras"].to<JsonArray>();
  for (int i = 0; i < cameraCount; i++) {
    JsonObject c = arr.add<JsonObject>();
    c["id"] = cameraIds[i];
    c["entries"] = cameras[i].entries;
    c["exits"] = cameras[i].exits;
  }
  doc["occupancy"] = currentOccupancy;
  serializeJson(doc, f);
  f.close();
#endif

  countsDirty = false;
  lastCountsSave = millis();
}

void loadCounts() {
#if COUNT_PERSISTENCE_ENABLED
  if (!SPIFFS.exists(COUNTS_PATH)) {
    Serial.println(F("[PERSIST] No saved counts - fresh start"));
    return;
  }
  File f = SPIFFS.open(COUNTS_PATH, FILE_READ);
  if (!f) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, f);
  f.close();
  if (err) {
    Serial.print(F("[PERSIST] Parse error: "));
    Serial.println(err.c_str());
    return;
  }

  JsonArray arr = doc["cameras"].as<JsonArray>();
  for (JsonObject c : arr) {
    const char* id = c["id"];
    if (!id) continue;
    int idx = findCamera(id);
    if (idx < 0) continue;
    cameras[idx].entries = c["entries"] | 0UL;
    cameras[idx].exits = c["exits"] | 0UL;
  }
  recomputeOccupancyFromCameraTotals();

  Serial.print(F("[PERSIST] Loaded - occupancy="));
  Serial.println(currentOccupancy);
#else
  Serial.println(F("[PERSIST] Count persistence disabled - fresh start"));
#endif
}

// ============== STATE TRANSITIONS ==============

void changeState(SystemState newState) {
  if (newState == currentState) return;
  prevState = currentState;
  currentState = newState;

  if (newState != STATE_RUN && newState != STATE_AWAIT_START) {
    vacancyClearActive = false;
  }

  char detail[64];
  snprintf(detail, sizeof(detail), "from=%s,occ=%d", stateNames[prevState], currentOccupancy);
  logEvent(stateNames[newState], detail);

  Serial.print(F("[STATE] "));
  Serial.print(stateNames[prevState]);
  Serial.print(F(" -> "));
  Serial.println(stateNames[newState]);

  applyOutputs();
}

// ============== RELAY OUTPUT CONTROL ==============

void setRunOccupiedOutputs() {
  digitalWrite(PIN_RELAY_RED_BUZZER, HIGH);  // Occupied: red + buzzer ON
  digitalWrite(PIN_RELAY_YELLOW, LOW);
  digitalWrite(PIN_RELAY_GREEN, LOW);
  digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // Open NC contact while occupied
}

void setRunEmptyOutputs() {
  digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
  digitalWrite(PIN_RELAY_YELLOW, LOW);
  digitalWrite(PIN_RELAY_GREEN, HIGH);
  digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_ENABLE_LEVEL);  // Close NC contact only when healthy/clear
}

void setRunClearingOutputs(bool redOn) {
  digitalWrite(PIN_RELAY_RED_BUZZER, redOn ? HIGH : LOW);
  digitalWrite(PIN_RELAY_YELLOW, LOW);
  digitalWrite(PIN_RELAY_GREEN, LOW);
  digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // Keep NC contact open during clear delay
}

void setMaintenanceOutputs() {
  digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
  digitalWrite(PIN_RELAY_YELLOW, HIGH);
  digitalWrite(PIN_RELAY_GREEN, HIGH);
  digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_ENABLE_LEVEL); // Robot enabled in maintenance mode
}

void applyOutputs() {
  faultPulseOn = false;
  faultPulseTimer = millis();

  switch (currentState) {
    case STATE_STARTUP:
      digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
      digitalWrite(PIN_RELAY_YELLOW, HIGH);       // Solid yellow during startup
      digitalWrite(PIN_RELAY_GREEN, LOW);
      digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // Open NC contact during startup
      break;

    case STATE_RUN:
      if (currentOccupancy > 0) {
        roomWasOccupied = true;
        vacancyClearActive = false;
        setRunOccupiedOutputs();
      } else if (!insidePresenceReadyForEmpty(millis())) {
        roomWasOccupied = true;
        vacancyClearActive = false;
        refreshRunOutputs(); // Blink while checking the post-EXIT window; no false steady red.
      } else {
        roomWasOccupied = false;
        vacancyClearActive = false;
        setRunEmptyOutputs();
      }
      break;

    case STATE_FAULT:
      digitalWrite(PIN_RELAY_RED_BUZZER, HIGH);   // Will pulse
      digitalWrite(PIN_RELAY_YELLOW, LOW);
      digitalWrite(PIN_RELAY_GREEN, LOW);
      digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // Open NC contact on fault
      faultPulseOn = true;
      faultPulseTimer = millis();
      break;

    case STATE_AWAIT_START:
      // Waiting for operator to press reset/start before counting begins.
      // Yellow blinks via updateAwaitBlink(); other lamps off; relay OFF.
      digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
      digitalWrite(PIN_RELAY_GREEN, LOW);
      digitalWrite(PIN_RELAY_YELLOW, HIGH);       // Initial on; blink will toggle.
      digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // ROBOT DISABLED until acked
      awaitBlinkOn = true;
      awaitBlinkTimer = millis();
      break;

    case STATE_MAINTENANCE:
      vacancyClearActive = false;
      roomWasOccupied = false;
      setMaintenanceOutputs();
      break;

  }
}

// Called on every occupancy change while in RUN — refresh just the lamps/relay
// without changing state.
void refreshRunOutputs() {
  if (currentState != STATE_RUN) return;

  if (currentOccupancy > 0) {
    roomWasOccupied = true;
    vacancyClearActive = false;
    setRunOccupiedOutputs();
    return;
  }

  if (!insidePresenceReadyForEmpty(millis()) && !roomWasOccupied) {
    roomWasOccupied = true;
  }

  if (roomWasOccupied && !vacancyClearActive) {
    vacancyClearActive = true;
    vacancyClearStarted = millis();
    runRedBlinkTimer = vacancyClearStarted;
    runRedBlinkOn = true;
    resetSessionCountsForVacancy("vacancy_timer");
    setRunClearingOutputs(true);
    return;
  }

  if (!vacancyClearActive) setRunEmptyOutputs();
}

void updateFaultPulse(unsigned long now) {
  if (currentState != STATE_FAULT) return;

  if (faultPulseOn && (now - faultPulseTimer >= FAULT_PULSE_ON_MS)) {
    digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
    faultPulseOn = false;
    faultPulseTimer = now;
  } else if (!faultPulseOn && (now - faultPulseTimer >= FAULT_PULSE_OFF_MS)) {
    digitalWrite(PIN_RELAY_RED_BUZZER, HIGH);
    faultPulseOn = true;
    faultPulseTimer = now;
  }
}

void updateAwaitBlink(unsigned long now) {
  if (currentState != STATE_AWAIT_START) return;
  if (now - awaitBlinkTimer >= AWAIT_BLINK_MS) {
    awaitBlinkOn = !awaitBlinkOn;
    digitalWrite(PIN_RELAY_YELLOW, awaitBlinkOn ? HIGH : LOW);
    awaitBlinkTimer = now;
  }
}

void updateRunOutputTimers(unsigned long now) {
  if (currentState != STATE_RUN) return;
  // Check message freshness on every loop, including when no new MQTT data arrives.
  refreshRunOutputs();
  if (!vacancyClearActive) return;

  if (currentOccupancy > 0) {
    vacancyClearActive = false;
    roomWasOccupied = true;
    setRunOccupiedOutputs();
    return;
  }

  // The original 5s minimum remains. An open 8s post-EXIT window extends it.
  if (now - vacancyClearStarted >= VACANCY_CLEAR_MS && insidePresenceReadyForEmpty(now)) {
    unsigned long sessionIn = 0, sessionOut = 0;
    computeProcessedTotals(&sessionIn, &sessionOut);
    if (sessionIn || sessionOut) resetSessionCountsForVacancy("post_exit_check_complete");
    vacancyClearActive = false;
    roomWasOccupied = false;
    setRunEmptyOutputs();
    return;
  }

  if (now - runRedBlinkTimer >= RUN_RED_BLINK_MS) {
    runRedBlinkOn = !runRedBlinkOn;
    setRunClearingOutputs(runRedBlinkOn);
    runRedBlinkTimer = now;
  }
}

bool occupancyRelayActive() {
  return currentState == STATE_MAINTENANCE ||
         (currentState == STATE_RUN &&
          currentOccupancy == 0 &&
          !vacancyClearActive &&
          insidePresenceReadyForEmpty(millis()));
}

// ============== MQTT MESSAGE HANDLER ==============

void onMqttMessage(const char* topic, const char* payload) {
#if RAW_MQTT_DEBUG
  Serial.print(F("[RAW_IN] topic="));
  Serial.print(topic);
  Serial.print(F(" payload="));
  Serial.println(payload);
#endif

  char devId[32];
  if (!extractDeviceId(topic, devId, sizeof(devId))) return;

  // Filter non-camera devices
  if (strcmp(devId, "system") == 0 || strcmp(devId, "controller") == 0 ||
      strcmp(devId, DEVICE_ID) == 0 || strcmp(devId, "unknown") == 0) return;

  int idx = findCamera(devId);
  if (idx < 0) {
    Serial.print(F("[MQTT] Ignored unknown camera id: "));
    Serial.println(devId);
    return;  // Not in roster
  }

  unsigned long now = millis();

  // Presence ticks do not substitute for a camera heartbeat or crossing event.
  if (topicEndsWith(topic, "/presence")) {
    handleInsidePresence(idx, payload, now);
    return;
  }

  if (cameras[idx].disconnected) {
    cameras[idx].disconnected = false;
    cameras[idx].disconnectedSince = 0;
    Serial.print(F("[CAM] Reconnected: "));
    Serial.println(devId);
  }

  bool isEvent     = topicEndsWith(topic, "/event");
  bool isHeartbeat = topicEndsWith(topic, "/heartbeat");

  if (isEvent) {
    msgCountEvent++;
    cameras[idx].hasSeen = true;
    cameras[idx].lastHb = now;     // Event also serves as proof-of-life
    cameras[idx].lastEvent = now;

    Serial.print(F("[MQTT] Event topic="));
    Serial.print(topic);
    Serial.print(F(" payload="));
    Serial.println(payload);

    // Count received camera events in every state so the serial table reflects
    // the actual data coming from each camera. Relay output remains state-gated.
    int payloadLen = strlen(payload);
    if (payloadLen > MAX_PAYLOAD_BYTES) { errOversizedPayload++; return; }

    JsonDocument doc;
    if (deserializeJson(doc, payload)) { errJsonParse++; return; }

    const char* type = doc["type"] | "";
    long trackId = doc["track_id"] | -1L;

    if (strcmp(type, "ENTRY") == 0) {
      cameras[idx].entries++;
      recomputeOccupancyFromCameraTotals();

      Serial.print(F("[COUNT] "));
      Serial.print(devId);
      Serial.print(F(" ENTRY -> occupancy="));
      Serial.print(currentOccupancy);
      Serial.print(F(" state="));
      Serial.println(stateNames[currentState]);

      char detail[64];
      snprintf(detail, sizeof(detail), "door=%s,trk=%ld,occ=%d",
               cameras[idx].doorLabel, trackId, currentOccupancy);
      logEvent("ENTRY", detail);
      countsDirty = true;
      refreshRunOutputs();

    } else if (strcmp(type, "EXIT") == 0) {
      // Presence supplies a floor, not a synthetic ENTRY. Preserve normal zero-count EXIT behaviour.
      if (countedOccupancy == 0) {
        Serial.print(F("[COUNT] Ignored EXIT while normal counted occupancy is already zero from "));
        Serial.println(devId);

        char detail[64];
        snprintf(detail, sizeof(detail), "door=%s,trk=%ld,occ=%d",
                 cameras[idx].doorLabel, trackId, currentOccupancy);
        logEvent("EXIT_IGNORED", detail);
        refreshRunOutputs();
        return;
      }

      cameras[idx].exits++;
      recomputeOccupancyFromCameraTotals();
      // Only an accepted EXIT taking the normal count to zero opens the window.
      // Duplicate/ignored EXITs at zero do not restart the 8s deadline.
      if (countedOccupancy == 0) armInsidePostExitWindow(now);

      Serial.print(F("[COUNT] "));
      Serial.print(devId);
      Serial.print(F(" EXIT -> occupancy="));
      Serial.print(currentOccupancy);
      Serial.print(F(" state="));
      Serial.println(stateNames[currentState]);

      char detail[64];
      snprintf(detail, sizeof(detail), "door=%s,trk=%ld,occ=%d",
               cameras[idx].doorLabel, trackId, currentOccupancy);
      logEvent("EXIT", detail);
      countsDirty = true;
      refreshRunOutputs();

    } else {
      errUnknownEvent++;
    }

  } else if (isHeartbeat) {
    msgCountHeartbeat++;
    cameras[idx].lastHb = now;
    cameras[idx].hasSeen = true;
  }
}

void clearBootCounts() {
  for (int i = 0; i < cameraCount; i++) {
    cameras[i].entries = 0;
    cameras[i].exits = 0;
  }
  currentOccupancy = 0;
  countedOccupancy = 0;
  presenceOverride = false;
  roomWasOccupied = false;
  vacancyClearActive = false;
  runRedBlinkOn = false;
  countsDirty = true;
  saveCounts();
}

void handleClearCountHold(const char* source) {
  if (startupSettling || currentState == STATE_STARTUP) {
    Serial.println(F("[CLEAR] Count clear ignored: still booting"));
    return;
  }

  char detail[64];
  snprintf(detail, sizeof(detail), "src=%s,from=%s,occ=%d", source, stateNames[currentState], currentOccupancy);
  logEvent("COUNT_CLEAR", detail);

  for (int i = 0; i < cameraCount; i++) {
    cameras[i].entries = 0;
    cameras[i].exits = 0;
  }
  // Clearing counters cannot clear a confirmed person from the interior camera.
  recomputeOccupancyFromCameraTotals();
  roomWasOccupied = (currentState == STATE_RUN);
  vacancyClearActive = false;
  runRedBlinkOn = false;
  countsDirty = true;
  saveCounts();

  Serial.print(F("[CLEAR] Counts cleared by "));
  Serial.println(source);

  if (currentState == STATE_RUN) {
    refreshRunOutputs();
  } else {
    applyOutputs();
  }
}
// Reset/start button begins operation after startup/fault recovery.
// Holding GREEN clears false counts or toggles maintenance mode.
void handleStartPress(const char* source) {
  if (currentState == STATE_FAULT) {
    Serial.println(F("[BTN] Reset/start ignored: in FAULT (cameras offline)"));
    return;
  }
  if (currentState == STATE_STARTUP) {
    Serial.println(F("[BTN] Reset/start ignored: still booting"));
    return;
  }
  if (currentState != STATE_AWAIT_START) {
    Serial.println(F("[BTN] Reset/start ignored: already running"));
    return;
  }

  char detail[64];
  snprintf(detail, sizeof(detail), "src=%s,from=%s", source, stateNames[currentState]);
  logEvent("START_ACK", detail);
  Serial.print(F("[START] Reset/start ack by "));
  Serial.print(source);
  Serial.println(F(" -> RUN"));
  changeState(STATE_RUN);
}

void handleMaintenanceHold(const char* source) {
  if (startupSettling || currentState == STATE_STARTUP) {
    Serial.println(F("[MAINT] Hold ignored: still booting"));
    return;
  }

  char detail[64];
  snprintf(detail, sizeof(detail), "src=%s,from=%s", source, stateNames[currentState]);

  if (currentState == STATE_MAINTENANCE) {
    logEvent("MAINT_OFF", detail);
    Serial.print(F("[MAINT] Exit by "));
    Serial.print(source);
    Serial.println(F(" -> AWAIT_START"));
    changeState(STATE_AWAIT_START);
    return;
  }

  logEvent("MAINT_ON", detail);
  Serial.print(F("[MAINT] Enter by "));
  Serial.print(source);
  Serial.println(F(" -> MAINTENANCE"));
  changeState(STATE_MAINTENANCE);
}
// ============== AGGREGATION / FAULT ENGINE ==============

void runAggregation(unsigned long now) {
  if (cameraCount == 0) return;
  if (startupSettling) return;
  // Step 1: Detect camera timeouts
  bool anyFault = !cameraRolesValid;
  for (int i = 0; i < cameraCount; i++) {
    if (cameras[i].hasSeen && !cameras[i].disconnected) {
      unsigned long lastSeen = cameras[i].lastHb;
      if (cameras[i].lastEvent > lastSeen) lastSeen = cameras[i].lastEvent;
      if (now - lastSeen > FAULT_TIMEOUT_MS) {
        cameras[i].disconnected = true;
        cameras[i].disconnectedSince = now;
        Serial.print(F("[CAM] Offline, continuing operation: "));
        Serial.println(cameraIds[i]);
        char detail[64];
        snprintf(detail, sizeof(detail), "camera=%s,door=%s",
                 cameraIds[i], cameras[i].doorLabel);
        logEvent("CAMERA_OFFLINE", detail);
      }
    }
    if (CAMERA_FAILURE_TRIGGERS_FAULT && (cameras[i].disconnected || !cameras[i].hasSeen)) anyFault = true;
  }

  // Step 2: Camera timeouts are diagnostic-only when the setting is 0.
  // STATE_FAULT remains available for the ESP32 Ethernet-link failure.
  if (anyFault && (currentState == STATE_RUN || currentState == STATE_AWAIT_START)) {
    changeState(STATE_FAULT);
    return;
  }
  if (!anyFault && ethConnected && currentState == STATE_FAULT) {
    logEvent("FAULT_OFF", "controller_fault_cleared");
#if BYPASS_AWAIT_START
    Serial.println(F("[BYPASS] Fault cleared -> RUN"));
    changeState(STATE_RUN);
#else
    // Require operator reset/start ack before counting resumes after a fault.
    changeState(STATE_AWAIT_START);
#endif
    return;
  }

  // Step 3: Periodic status publish
  if (now - lastStatusPublish >= STATUS_INTERVAL_MS) {
    lastStatusPublish = now;
    publishSystemStatus(now);
  }

  // Step 4: Lazy persistence
  if (countsDirty && (now - lastCountsSave >= COUNTS_SAVE_MS)) {
    saveCounts();
  }
}

void publishSystemStatus(unsigned long now) {
  pubDoc.clear();
  pubDoc["state"] = stateNames[currentState];
  pubDoc["occupancy"] = currentOccupancy;
  pubDoc["cell"] = "Cell 6";
  pubDoc["counted_occupancy"] = countedOccupancy;
  pubDoc["presence_override"] = presenceOverride;
  pubDoc["presence_ready_for_empty"] = insidePresenceReadyForEmpty(now);
  pubDoc["vacancy_clear_active"] = vacancyClearActive;
  pubDoc["camera_roles_valid"] = cameraRolesValid;
  pubDoc["relay"] = occupancyRelayActive();

  unsigned long rawTotalIn = 0, rawTotalOut = 0;
  DoorTotals doors[MAX_CAMERAS];
  int doorCount = buildDoorTotals(doors, MAX_CAMERAS);

  JsonObject cams = pubDoc["cameras"].to<JsonObject>();
  for (int i = 0; i < cameraCount; i++) {
    JsonObject cam = cams[cameraIds[i]].to<JsonObject>();
    cam["door"] = cameras[i].doorLabel;
    cam["entries"] = cameras[i].entries;
    cam["exits"] = cameras[i].exits;
    cam["net"] = (long)cameras[i].entries - (long)cameras[i].exits;
    cam["disconnected"] = cameras[i].disconnected;
    cam["role"] = cameras[i].isInsideCamera ? "inside" : "outside";
    if (cameras[i].isInsideCamera) {
      cam["presence_state"] = insidePresenceFresh(cameras[i], now) ? cameras[i].presenceState : "unknown";
      cam["presence_fresh"] = insidePresenceFresh(cameras[i], now);
      cam["presence_latched"] = cameras[i].presenceLatched;
      cam["post_exit_window_open"] = cameras[i].presenceWindow.open((uint32_t)now);
      cam["presence_ignored_outside_window"] = cameras[i].presenceIgnoredLate;
    }
    rawTotalIn += cameras[i].entries;
    rawTotalOut += cameras[i].exits;
  }

  unsigned long totalIn = 0, totalOut = 0;
  JsonObject doorJson = pubDoc["doors"].to<JsonObject>();
  for (int i = 0; i < doorCount; i++) {
    JsonObject door = doorJson[doors[i].label].to<JsonObject>();
    door["entries"] = doors[i].entries;
    door["exits"] = doors[i].exits;
    door["net"] = (long)doors[i].entries - (long)doors[i].exits;
    totalIn += doors[i].entries;
    totalOut += doors[i].exits;
  }

  pubDoc["total_in"] = totalIn;
  pubDoc["total_out"] = totalOut;
  pubDoc["total_net"] = (long)totalIn - (long)totalOut;
  pubDoc["raw_total_in"] = rawTotalIn;
  pubDoc["raw_total_out"] = rawTotalOut;
  pubDoc["raw_total_net"] = (long)rawTotalIn - (long)rawTotalOut;
  pubDoc["ts"] = now;

  if (measureJson(pubDoc) >= sizeof(pubBuf)) {
    logEvent("PUBLISH_ERROR", "JSON output buffer too small");
    return;
  }
  serializeJson(pubDoc, pubBuf, sizeof(pubBuf));
#if RAW_STATUS_DEBUG
  Serial.print(F("[RAW_OUT] topic=eagle/system/status payload="));
  Serial.println(pubBuf);
#endif
  mqttBroker.publish("eagle/system/status", pubBuf);
}

// ============== BUTTON HANDLING ==============

bool isGreenButtonPressed() {
  return digitalRead(PIN_BTN_GREEN) == BTN_GREEN_PRESSED_LEVEL;
}

void monitorButtonInputs(unsigned long now) {
  bool greenRaw = isGreenButtonPressed();
  int greenLevel = digitalRead(PIN_BTN_GREEN);

  if (greenRaw != lastGreenRaw) {
    lastGreenRaw = greenRaw;
    Serial.print(F("[BTN_RAW] GREEN GPIO"));
    Serial.print(PIN_BTN_GREEN);
    Serial.print(F(" level="));
    Serial.print(greenLevel == HIGH ? F("HIGH") : F("LOW"));
    Serial.print(F(" state="));
    Serial.print(greenRaw ? F("PRESSED") : F("RELEASED"));
    Serial.print(F(" at "));
    Serial.print(now);
    Serial.println(F("ms"));
  }
}
void handleButtons(unsigned long now) {
  bool greenState = isGreenButtonPressed();

  if (greenState && !greenPressed) {
    greenPressed = true;
    greenPressStart = now;
    greenPressEligible = (currentState == STATE_AWAIT_START);
    greenPressHandled = false;
    return;
  }

  if (greenState && greenPressed) {
    unsigned long held = now - greenPressStart;
    if (!greenPressHandled && held >= MAINT_HOLD_MS) {
      greenPressHandled = true;
      handleMaintenanceHold("button");
    }
    return;
  }

  if (!greenState && greenPressed) {
    unsigned long held = now - greenPressStart;
    if (!greenPressHandled && held >= CLEAR_COUNT_HOLD_MS) {
      handleClearCountHold("button");
    } else if (!greenPressHandled && greenPressEligible && held >= BTN_DEBOUNCE_MS) {
      handleStartPress("button");
    }
    greenPressed = false;
    greenPressEligible = false;
    greenPressHandled = false;
  }
}

// ============== WATCHDOG ==============

void runWatchdog(unsigned long now) {
  if (startupSettling && (now - systemBootTime >= STARTUP_SETTLE_MS)) {
    startupSettling = false;
    expectedCameras = cameraCount;

    int missing = 0;
    for (int i = 0; i < cameraCount; i++) {
      if (!cameras[i].hasSeen) {
        cameras[i].disconnected = true;
        cameras[i].disconnectedSince = now;
        missing++;
      }
    }

    Serial.println();
    Serial.println(F("============================================"));
    Serial.print(F("  Settling complete - "));
    Serial.print(expectedCameras);
    Serial.print(F(" expected, "));
    Serial.print(expectedCameras - missing);
    Serial.println(F(" online:"));
    for (int i = 0; i < cameraCount; i++) {
      Serial.print(F("    "));
      Serial.print(cameraIds[i]);
      Serial.print(F("  Door "));
      Serial.print(cameras[i].doorLabel);
      Serial.println(cameras[i].hasSeen ? F("  [OK]") : F("  [MISSING]"));
    }
    Serial.println(F("============================================"));
    Serial.println();

    char detail[64];
    snprintf(detail, sizeof(detail), "expected=%d,missing=%d", expectedCameras, missing);
    logEvent("SETTLING_DONE", detail);

    if (!cameraRolesValid || (CAMERA_FAILURE_TRIGGERS_FAULT && missing > 0)) {
      changeState(STATE_FAULT);
    } else {
#if BYPASS_AWAIT_START
      Serial.println(F("[BYPASS] Startup complete -> RUN"));
      changeState(STATE_RUN);
#else
      // Wait for operator reset/start ack before entering RUN.
      changeState(STATE_AWAIT_START);
#endif
    }
    return;
  }

  if (startupSettling) return;

  runAggregation(now);
}

// ============== ETHERNET ==============

void checkEthernetRecovery(unsigned long now) {
  if (!ethConnected) {
    if (!ethDownTiming) {
      ethDownTiming = true;
      ethDownSince = now;
    } else {
      unsigned long downFor = now - ethDownSince;
      if (downFor > ETH_DOWN_FAULT_MS && !startupSettling &&
          currentState != STATE_FAULT && currentState != STATE_MAINTENANCE) {
        Serial.println(F("[ETH] Link down > 3s — forcing FAULT"));
        logEvent("FAULT_ON", "eth_link_down");
        changeState(STATE_FAULT);
      }
      if (downFor > ETH_RECOVERY_MS) {
        Serial.println(F("[ETH] Link down too long — re-init"));
        ETH.begin(ETH_PHY_W5500, 1, ETH_SPI_CS, ETH_SPI_IRQ, ETH_SPI_RST, SPI);
        ethDownTiming = false;
      }
    }
  } else if (ethDownTiming) {
    ethDownTiming = false;
    Serial.println(F("[ETH] Link recovered"));
  }
}

void onEthEvent(arduino_event_id_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START:
      Serial.println(F("[ETH] Started"));
      ETH.config(staticIP, gatewayIP, subnetMask, dnsIP);
      break;
    case ARDUINO_EVENT_ETH_CONNECTED:
      Serial.println(F("[ETH] Link up"));
      break;
    case ARDUINO_EVENT_ETH_GOT_IP:
      Serial.print(F("[ETH] IP: "));
      Serial.println(ETH.localIP());
      ethConnected = true;
      break;
    case ARDUINO_EVENT_ETH_DISCONNECTED:
      Serial.println(F("[ETH] Link down"));
      ethConnected = false;
      break;
    default:
      break;
  }
}

// ============== DIAGNOSTICS ==============

void publishDiagnostics(unsigned long now) {
  pubDoc.clear();
  pubDoc["fw"] = FW_VERSION;
  pubDoc["uptime_s"] = now / 1000;
  pubDoc["heap"] = ESP.getFreeHeap();
  pubDoc["cameras"] = cameraCount;
  pubDoc["state"] = stateNames[currentState];
  pubDoc["occupancy"] = currentOccupancy;
  pubDoc["cell"] = "Cell 6";
  pubDoc["counted_occupancy"] = countedOccupancy;
  pubDoc["presence_override"] = presenceOverride;
  pubDoc["presence_ready_for_empty"] = insidePresenceReadyForEmpty(now);
  pubDoc["camera_roles_valid"] = cameraRolesValid;

  JsonObject msgs = pubDoc["msgs"].to<JsonObject>();
  msgs["event"] = msgCountEvent;
  msgs["hb"] = msgCountHeartbeat;
  msgs["presence"] = msgCountPresence;

  JsonObject errs = pubDoc["errs"].to<JsonObject>();
  errs["json"] = errJsonParse;
  errs["oversize"] = errOversizedPayload;
  errs["unknown"] = errUnknownEvent;
  errs["presence"] = errPresence;

  if (measureJson(pubDoc) >= sizeof(pubBuf)) {
    logEvent("PUBLISH_ERROR", "JSON output buffer too small");
    return;
  }
  serializeJson(pubDoc, pubBuf, sizeof(pubBuf));
#if RAW_STATUS_DEBUG
  Serial.print(F("[RAW_OUT] topic=eagle/system/diag payload="));
  Serial.println(pubBuf);
#endif
  mqttBroker.publish("eagle/system/diag", pubBuf);
}

// ============== SERIAL STATUS TABLE ==============

void printSerialMonitor(unsigned long now) {
#if SERIAL_MONITOR_ENABLED
  unsigned long rawTotalIn = 0;
  unsigned long rawTotalOut = 0;
  unsigned long totalIn = 0;
  unsigned long totalOut = 0;
  computeProcessedTotals(&totalIn, &totalOut);

  DoorTotals doors[MAX_CAMERAS];
  int doorCount = buildDoorTotals(doors, MAX_CAMERAS);

  int seen = 0;
  for (int i = 0; i < cameraCount; i++) {
    if (cameras[i].hasSeen) seen++;
    rawTotalIn += cameras[i].entries;
    rawTotalOut += cameras[i].exits;
  }

  Serial.println();
  Serial.println(F("==================== ESP32 SERIAL MONITOR ===================="));
  Serial.print(F("[TIME] uptime_s="));
  Serial.print(now / 1000);
  Serial.print(F(" heap="));
  Serial.print(ESP.getFreeHeap());
  Serial.print(F(" fw="));
  Serial.println(FW_VERSION);

  Serial.print(F("[STATE] state="));
  Serial.print(stateNames[currentState]);
  Serial.print(F(" startup_settling="));
  Serial.print(startupSettling ? F("YES") : F("NO"));
  if (startupSettling) {
    unsigned long rem = (now - systemBootTime < STARTUP_SETTLE_MS)
                        ? (STARTUP_SETTLE_MS - (now - systemBootTime)) / 1000 : 0;
    Serial.print(F(" remaining_s="));
    Serial.print(rem);
  }
  Serial.print(F(" bypass_await="));
#if BYPASS_AWAIT_START
  Serial.println(F("ON"));
#else
  Serial.println(F("OFF"));
#endif

  Serial.print(F("[ETH] link="));
  Serial.print(ethConnected ? F("UP") : F("DOWN"));
  Serial.print(F(" ip="));
  if (ethConnected) Serial.print(ETH.localIP());
  else Serial.print(F("0.0.0.0"));
  Serial.println(F(" broker=192.168.10.1:1883"));

  Serial.print(F("[MQTT IN] subscribed=eagle/+/event,eagle/+/heartbeat,eagle/+/presence events="));
  Serial.print(msgCountEvent);
  Serial.print(F(" hb="));
  Serial.print(msgCountHeartbeat);
  Serial.print(F(" json_err="));
  Serial.print(errJsonParse);
  Serial.print(F(" unknown="));
  Serial.println(errUnknownEvent);

  Serial.print(F("[MQTT OUT] status_topic=eagle/system/status interval_ms="));
  Serial.print(STATUS_INTERVAL_MS);
  Serial.print(F(" diag_topic=eagle/system/diag interval_ms="));
  Serial.println(DIAG_INTERVAL_MS);

  Serial.print(F("[RELAY] robot_relay="));
  Serial.print(occupancyRelayActive() ? F("ON") : F("OFF"));
  Serial.print(F(" occupancy="));
  Serial.println(currentOccupancy);

  Serial.print(F("[TOTAL PROCESSED] in="));
  Serial.print(totalIn);
  Serial.print(F(" out="));
  Serial.print(totalOut);
  Serial.print(F(" net="));
  Serial.println((long)totalIn - (long)totalOut);

  Serial.print(F("[TOTAL RAW] in="));
  Serial.print(rawTotalIn);
  Serial.print(F(" out="));
  Serial.print(rawTotalOut);
  Serial.print(F(" net="));
  Serial.println((long)rawTotalIn - (long)rawTotalOut);

  Serial.print(F("[CAMERA ROSTER] seen="));
  Serial.print(seen);
  Serial.print(F("/"));
  Serial.println(cameraCount);

  Serial.println(F("[DOORS] processed by max(entry), max(exit) per same door label"));
  if (doorCount == 0) {
    Serial.println(F("  none"));
  }
  for (int i = 0; i < doorCount; i++) {
    Serial.print(F("  door="));
    Serial.print(doors[i].label);
    Serial.print(F(" in="));
    Serial.print(doors[i].entries);
    Serial.print(F(" out="));
    Serial.print(doors[i].exits);
    Serial.print(F(" net="));
    Serial.println((long)doors[i].entries - (long)doors[i].exits);
  }

  Serial.printf("[CELL 6] counted=%d effective=%d presence_override=%s clear_ready=%s roles_valid=%s presence_msgs=%lu errors=%lu\n",
                countedOccupancy, currentOccupancy, presenceOverride ? "YES" : "NO",
                insidePresenceReadyForEmpty(now) ? "YES" : "NO", cameraRolesValid ? "YES" : "NO",
                msgCountPresence, errPresence);
  for (int i = 0; i < cameraCount; i++) {
    if (!cameras[i].isInsideCamera) continue;
    Serial.printf("[INTERIOR] id=%s state=%s latched=%s age_ms=%ld\n", cameraIds[i],
                  insidePresenceFresh(cameras[i], now) ? cameras[i].presenceState : "unknown",
                  cameras[i].presenceLatched ? "YES" : "NO",
                  cameras[i].presenceSeen ? (long)(now - cameras[i].lastPresence) : -1L);
  }
  Serial.println(F("[CAMERAS] raw per-camera data"));
  if (cameraCount == 0) {
    Serial.println(F("  none"));
  }
  for (int i = 0; i < cameraCount; i++) {
    unsigned long lastSeen = cameras[i].lastHb;
    if (cameras[i].lastEvent > lastSeen) lastSeen = cameras[i].lastEvent;

    Serial.print(F("  id="));
    Serial.print(cameraIds[i]);
    Serial.print(F(" door="));
    Serial.print(cameras[i].doorLabel);
    Serial.print(F(" status="));
    if (cameras[i].disconnected) Serial.print(F("OFFLINE"));
    else if (cameras[i].hasSeen) Serial.print(F("ONLINE"));
    else Serial.print(F("WAITING"));
    Serial.print(F(" in="));
    Serial.print(cameras[i].entries);
    Serial.print(F(" out="));
    Serial.print(cameras[i].exits);
    Serial.print(F(" net="));
    Serial.print((long)cameras[i].entries - (long)cameras[i].exits);
    Serial.print(F(" last_seen_ms="));
    if (cameras[i].hasSeen) Serial.println(now - lastSeen);
    else Serial.println(F("-"));
  }

  Serial.println(F("=============================================================="));
#endif
}
void printStatusTable(unsigned long now) {
  Serial.println();
  Serial.println(F("+----------------+------+----------+---------+---------+--------+"));
  Serial.println(F("| Camera         | Door | Status   | Entries | Exits   | Age    |"));
  Serial.println(F("+----------------+------+----------+---------+---------+--------+"));

  for (int i = 0; i < cameraCount; i++) {
    char idBuf[16];
    snprintf(idBuf, sizeof(idBuf), "%-14s", cameraIds[i]);
    Serial.print(F("| "));
    Serial.print(idBuf);
    Serial.print(F(" |  "));
    Serial.print(cameras[i].doorLabel);
    Serial.print(F("  | "));

    if (cameras[i].disconnected) {
      Serial.print(F("OFFLINE  "));
    } else if (cameras[i].hasSeen) {
      Serial.print(F("online   "));
    } else {
      Serial.print(F("waiting  "));
    }
    Serial.print(F("| "));

    char buf[10];
    snprintf(buf, sizeof(buf), "%-7lu", cameras[i].entries);
    Serial.print(buf);
    Serial.print(F(" | "));
    snprintf(buf, sizeof(buf), "%-7lu", cameras[i].exits);
    Serial.print(buf);
    Serial.print(F(" | "));

    if (cameras[i].disconnected || !cameras[i].hasSeen) {
      Serial.print(F("  -   "));
    } else {
      unsigned long lastSeen = cameras[i].lastHb;
      if (cameras[i].lastEvent > lastSeen) lastSeen = cameras[i].lastEvent;
      unsigned long age = now - lastSeen;
      char aBuf[7];
      if (age < 1000) snprintf(aBuf, sizeof(aBuf), "%3lums", age);
      else if (age < 60000) snprintf(aBuf, sizeof(aBuf), "%3lus ", age / 1000);
      else snprintf(aBuf, sizeof(aBuf), "%3lum ", age / 60000);
      Serial.print(aBuf);
    }
    Serial.println(F(" |"));
  }

  Serial.println(F("+----------------+------+----------+---------+---------+--------+"));
  Serial.print(F("| State: "));
  char sBuf[12];
  snprintf(sBuf, sizeof(sBuf), "%-8s", stateNames[currentState]);
  Serial.print(sBuf);
  Serial.print(F(" Occupancy: "));
  Serial.print(currentOccupancy);
  Serial.print(F("  Relay: "));
  Serial.print(occupancyRelayActive() ? "ON " : "OFF");
  Serial.println(F("                |"));
  Serial.println(F("+----------------------------------------------------------------+"));
  Serial.println();
}

// ============== SERIAL COMMANDS ==============

void checkSerialCommands() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "EXPORT_LOG") {
      exportLog();
    } else if (cmd == "STATUS") {
      printStatusTable(millis());
    } else if (cmd == "HELP") {
      Serial.println(F("Commands: STATUS, EXPORT_LOG, HELP"));
    }
  }
}

// ============== SETUP ==============

void setup() {
  Serial.begin(115200); // Cell 6 — full original programme retained
  delay(1000);

  Serial.println(F("================================================"));
  Serial.println(F("  EAGLE-AI Tata Composite — Cabin Counter"));
  Serial.println(F("  Waveshare ESP32-S3-ETH + PicoMQTT"));
  Serial.print(  F("  Firmware v"));
  Serial.println(FW_VERSION);
#if RAW_MQTT_DEBUG || RAW_STATUS_DEBUG
  Serial.println(F("  Serial debug: RAW MQTT/status enabled"));
#endif
  Serial.println(F("================================================"));

  // GPIO setup — outputs first, ensure safe state
  pinMode(PIN_RELAY_RED_BUZZER, OUTPUT);
  pinMode(PIN_RELAY_GREEN, OUTPUT);
  pinMode(PIN_RELAY_YELLOW, OUTPUT);
  pinMode(PIN_RELAY_OCCUPANCY, OUTPUT);

  digitalWrite(PIN_RELAY_RED_BUZZER, LOW);
  digitalWrite(PIN_RELAY_GREEN, LOW);
  digitalWrite(PIN_RELAY_YELLOW, HIGH);   // Yellow = settling
  digitalWrite(PIN_RELAY_OCCUPANCY, ROBOT_DISABLE_LEVEL); // Open NC contact at boot

  pinMode(PIN_BTN_GREEN, INPUT);

  Serial.println(F("[GPIO] Initialized — relay OFF, YELLOW (settling)"));

  // SPIFFS is disabled in production unless file logging or count persistence is enabled.
#if FILE_LOGGING_ENABLED || COUNT_PERSISTENCE_ENABLED
  if (!SPIFFS.begin(true)) {
    Serial.println(F("[SPIFFS] Mount failed - file storage disabled"));
  } else {
    Serial.println(F("[SPIFFS] Mounted"));
  }
#else
  Serial.println(F("[SPIFFS] Disabled - live counts only"));
#endif
  logEvent("SYSTEM_START", FW_VERSION);
  // Hardware watchdog
  esp_task_wdt_config_t wdtConfig = {
    .timeout_ms = HW_WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic = true
  };
  esp_task_wdt_reconfigure(&wdtConfig);
  esp_task_wdt_add(NULL);
  Serial.print(F("[WDT] Hardware watchdog — "));
  Serial.print(HW_WDT_TIMEOUT_S);
  Serial.println(F("s timeout"));

  // Ethernet
  Network.onEvent(onEthEvent);
  SPI.begin(ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI);
  ETH.begin(ETH_PHY_W5500, 1, ETH_SPI_CS, ETH_SPI_IRQ, ETH_SPI_RST, SPI);

  Serial.print(F("[ETH] Waiting for link"));
  unsigned long ethStart = millis();
  while (!ethConnected && millis() - ethStart < 10000) {
    esp_task_wdt_reset();
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (!ethConnected) {
    Serial.println(F("[ETH] WARNING: No link — cameras can't connect"));
  }

  // MQTT subscriptions
  mqttBroker.subscribe("eagle/+/event", [](const char* topic, const char* payload) {
    onMqttMessage(topic, payload);
  });
  mqttBroker.subscribe("eagle/+/heartbeat", [](const char* topic, const char* payload) {
    onMqttMessage(topic, payload);
  });
  mqttBroker.subscribe("eagle/+/presence", [](const char* topic, const char* payload) {
    onMqttMessage(topic, payload);
  });
  Serial.println(F("[MQTT] Subscribed: event + heartbeat + presence"));

  mqttBroker.begin();

  // Pre-register expected cameras
  Serial.print(F("[ROSTER] Expected cameras: "));
  Serial.println(EXPECTED_CAMERA_COUNT);
  for (size_t i = 0; i < EXPECTED_CAMERA_COUNT; i++) {
    addCamera(EXPECTED_CAMERAS[i].id, EXPECTED_CAMERAS[i].doorLabel);
  }

  cameraRolesValid = validateCell6CameraRoles();
  if (!cameraRolesValid) {
    logEvent("CONFIG_ERROR", "Cell 6: set distinct inside/outside camera IDs before use");
    Serial.println(F("[CELL 6] CAMERA ROLES INVALID — robot will remain disabled outside maintenance mode"));
  } else {
    Serial.print(F("[CELL 6] INSIDE=")); Serial.println(CELL_6_INSIDE_CAMERA_ID);
    Serial.print(F("[CELL 6] OUTSIDE=")); Serial.println(CELL_6_OUTSIDE_CAMERA_ID);
  }

  // Start every boot/demo from zero counts.
  clearBootCounts();
  Serial.println(F("[PERSIST] Boot counts cleared to zero"));

  systemBootTime = millis();
  currentState = STATE_STARTUP;

  Serial.print(F("[SYS] Settling: "));
  Serial.print(STARTUP_SETTLE_MS / 1000);
  Serial.println(F("s — waiting for roster to report..."));
  Serial.println();
}

// ============== MAIN LOOP ==============

void loop() {
  esp_task_wdt_reset();
  mqttBroker.loop();

  unsigned long now = millis();

  monitorButtonInputs(now);

  handleButtons(now);
  checkSerialCommands();

  if (now - lastWatchdog >= WATCHDOG_MS) {
    lastWatchdog = now;
    runWatchdog(now);
  }

  checkEthernetRecovery(now);

  updateFaultPulse(now);
  updateAwaitBlink(now);
  updateRunOutputTimers(now);
  if (now - lastDiag >= DIAG_INTERVAL_MS) {
    lastDiag = now;
    publishDiagnostics(now);
  }

#if SERIAL_MONITOR_ENABLED
  if (now - lastSerialMonitor >= SERIAL_MONITOR_INTERVAL_MS) {
    lastSerialMonitor = now;
    printSerialMonitor(now);
  }
#else
  if (now - lastTable >= TABLE_INTERVAL_MS && cameraCount > 0 && !startupSettling) {
    lastTable = now;
    printStatusTable(now);
  }

  if (startupSettling && now - lastTable >= 10000) {
    lastTable = now;
    unsigned long rem = (now - systemBootTime < STARTUP_SETTLE_MS)
                        ? (STARTUP_SETTLE_MS - (now - systemBootTime)) / 1000 : 0;
    Serial.print(F("[SETTLING] "));
    Serial.print(rem);
    Serial.print(F("s remaining | Cameras seen: "));
    int seen = 0;
    for (int i = 0; i < cameraCount; i++) if (cameras[i].hasSeen) seen++;
    Serial.print(seen);
    Serial.print(F("/"));
    Serial.println(cameraCount);
  }
#endif
}
