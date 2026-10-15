/*
 * ESP32-S3 + W5500: same MAX ENTRY / MAX EXIT counting, plus interior presence.
 * Import the accompanying production flow on BOTH reCameras. Define roles below.
 * Presence is a separate minimum occupancy of one; it never changes raw counters.
 */
#include <SPI.h>
#include <ETH.h>
#include <PicoMQTT.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>

#define FW_VERSION "1.7.0-interior-presence"
#define ETH_SPI_SCK 13
#define ETH_SPI_MISO 12
#define ETH_SPI_MOSI 11
#define ETH_SPI_CS 14
#define ETH_SPI_IRQ -1
#define ETH_SPI_RST -1

IPAddress staticIP(192, 168, 10, 1);
IPAddress gatewayIP(192, 168, 10, 1);
IPAddress subnetMask(255, 255, 255, 0);
IPAddress dnsIP(0, 0, 0, 0);

// Actual GPIO definitions from the supplied programme (not its old header).
#define PIN_RELAY_RED_BUZZER 21
#define PIN_RELAY_GREEN 18
#define PIN_RELAY_YELLOW 17
#define PIN_RELAY_OCCUPANCY 16
#define ROBOT_DISABLE_LEVEL HIGH
#define ROBOT_ENABLE_LEVEL LOW
#define PIN_BTN_GREEN 47
#define BTN_GREEN_PRESSED_LEVEL HIGH

#define FAULT_TIMEOUT_MS 8000
#define ETH_DOWN_FAULT_MS 3000
#define STARTUP_SETTLE_MS 120000
#define BTN_DEBOUNCE_MS 50
#define CLEAR_COUNT_HOLD_MS 5000
#define MAINT_HOLD_MS 10000
#define DIAG_INTERVAL_MS 60000
#define STATUS_INTERVAL_MS 1000
#define WATCHDOG_MS 2000
#define ETH_RECOVERY_MS 30000
#define HW_WDT_TIMEOUT_S 8
#define MAX_PAYLOAD_BYTES 512
#define MAX_OCCUPANCY 50
#define MAX_CAMERAS 4
#define RAW_MQTT_DEBUG 1
#define RAW_STATUS_DEBUG 1
#define SERIAL_MONITOR_ENABLED 1
#define SERIAL_MONITOR_INTERVAL_MS 2000
#define BYPASS_AWAIT_START 0
#define CAMERA_FAILURE_TRIGGERS_FAULT 0
#define FAULT_PULSE_ON_MS 1000
#define FAULT_PULSE_OFF_MS 1000
#define RUN_RED_BLINK_MS 500
#define VACANCY_CLEAR_MS 5000
#define AWAIT_BLINK_MS 500

// Camera-side >2s confirmation is checked again before accepting a NEW presence.
#define PRESENCE_CONFIRM_MS 2000
#define PRESENCE_SOURCE_MAX_AGE_MS 1000
#define PRESENCE_MESSAGE_TIMEOUT_MS 1500
// Unknown/stopped inside detection must not silently mean an empty cell.
// Maintenance mode remains an explicit operator bypass, as in the original.
#define REQUIRE_INSIDE_PRESENCE_CLEAR 1

const char DEVICE_ID[] = "composite_hub";
enum CameraRole { CAMERA_OUTSIDE, CAMERA_INSIDE };
struct ExpectedCamera { const char* id; const char* doorLabel; CameraRole role; };
// EDIT THESE TWO ROLES TO MATCH YOUR INSTALLATION. Both observe the SAME door E1.
const ExpectedCamera EXPECTED_CAMERAS[] = {
  { "TM_R1_01", "E1", CAMERA_INSIDE },
  { "TM_R1_02", "E1", CAMERA_OUTSIDE }
};
const size_t EXPECTED_CAMERA_COUNT = sizeof(EXPECTED_CAMERAS) / sizeof(EXPECTED_CAMERAS[0]);

enum SystemState { STATE_STARTUP, STATE_AWAIT_START, STATE_RUN, STATE_FAULT, STATE_MAINTENANCE };
const char* stateNames[] = { "STARTUP", "AWAIT_START", "RUN", "FAULT", "MAINTENANCE" };
struct CameraState {
  char doorLabel[8];
  unsigned long entries, exits, lastEvent, lastHb;
  bool hasSeen, disconnected;
  unsigned long disconnectedSince;
  CameraRole role;
  // Latch is retained through pending/stale data and cleared only by explicit fresh clear.
  bool presenceSeen, presenceValid, presenceLatched, presenceClear;
  unsigned long lastPresence;
  long presenceFrameAge;
  char presenceState[12];
};
struct DoorTotals { char label[8]; unsigned long entries, exits, maxExit; };
CameraState cameras[MAX_CAMERAS];
char cameraIds[MAX_CAMERAS][32];
int cameraCount = 0;
bool rosterValid = false;
PicoMQTT::Server mqttBroker;
bool ethConnected = false, ethDownTiming = false;
unsigned long ethDownSince = 0;
SystemState currentState = STATE_STARTUP;
bool startupSettling = true;
unsigned long systemBootTime = 0;
int countedOccupancy = 0, currentOccupancy = 0;
bool presenceOverride = false;
bool roomWasOccupied = false, vacancyClearActive = false, runRedBlinkOn = false;
unsigned long vacancyClearStarted = 0, runRedBlinkTimer = 0;
bool faultPulseOn = false, awaitBlinkOn = false;
unsigned long faultPulseTimer = 0, awaitBlinkTimer = 0;
bool greenPressed = false, greenPressEligible = false, greenPressHandled = false, lastGreenRaw = false;
unsigned long greenPressStart = 0;
unsigned long lastStatusPublish = 0, lastWatchdog = 0, lastDiag = 0, lastSerialMonitor = 0;
unsigned long msgCountEvent = 0, msgCountHeartbeat = 0, msgCountPresence = 0;
unsigned long errJsonParse = 0, errOversizedPayload = 0, errUnknownEvent = 0, errPresence = 0;
JsonDocument pubDoc;
char pubBuf[4096];

void applyOutputs();
void refreshRunOutputs();
void publishSystemStatus(unsigned long now);

void logEvent(const char* type, const char* detail) {
  Serial.printf("[LOG] %lu,%s,%s\n", millis(), type, detail);
}

int findCamera(const char* id) {
  for (int i = 0; i < cameraCount; i++) if (strcmp(cameraIds[i], id) == 0) return i;
  return -1;
}

int addCamera(const ExpectedCamera& expected) {
  if (cameraCount >= MAX_CAMERAS || findCamera(expected.id) >= 0) return -1;
  int idx = cameraCount++;
  memset(&cameras[idx], 0, sizeof(CameraState));
  strncpy(cameraIds[idx], expected.id, sizeof(cameraIds[idx]) - 1);
  strncpy(cameras[idx].doorLabel, expected.doorLabel, sizeof(cameras[idx].doorLabel) - 1);
  cameras[idx].role = expected.role;
  cameras[idx].presenceFrameAge = -1;
  strcpy(cameras[idx].presenceState, "unknown");
  Serial.printf("[CAM] %s door=%s role=%s\n", cameraIds[idx], cameras[idx].doorLabel,
                expected.role == CAMERA_INSIDE ? "INSIDE" : "OUTSIDE");
  return idx;
}

bool extractDeviceId(const char* topic, char* out, int maxLen) {
  out[0] = '\0';
  if (strncmp(topic, "eagle/", 6) != 0) return false;
  const char* start = topic + 6;
  const char* slash = strchr(start, '/');
  if (!slash) return false;
  int len = slash - start;
  if (len <= 0 || len >= maxLen) return false;
  memcpy(out, start, len); out[len] = '\0';
  return true;
}

bool topicEndsWith(const char* topic, const char* suffix) {
  size_t a = strlen(topic), b = strlen(suffix);
  return a >= b && strcmp(topic + a - b, suffix) == 0;
}

// SAME aggregation as supplied code: independent MAX ENTRY and MAX EXIT per door.
int buildDoorTotals(DoorTotals* doors, int maxDoors) {
  int doorCount = 0;
  for (int i = 0; i < cameraCount; i++) {
    int doorIdx = -1;
    for (int d = 0; d < doorCount; d++) {
      if (strcmp(doors[d].label, cameras[i].doorLabel) == 0) { doorIdx = d; break; }
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
    if (cameras[i].entries > doors[doorIdx].entries) doors[doorIdx].entries = cameras[i].entries;
    if (cameras[i].exits > doors[doorIdx].maxExit) {
      doors[doorIdx].maxExit = cameras[i].exits;
      doors[doorIdx].exits = cameras[i].exits;
    }
  }
  return doorCount;
}

void computeProcessedTotals(unsigned long* totalIn, unsigned long* totalOut) {
  DoorTotals doors[MAX_CAMERAS];
  int n = buildDoorTotals(doors, MAX_CAMERAS);
  *totalIn = 0; *totalOut = 0;
  for (int i = 0; i < n; i++) { *totalIn += doors[i].entries; *totalOut += doors[i].exits; }
}

bool anyInsidePresenceLatched() {
  for (int i = 0; i < cameraCount; i++) {
    if (cameras[i].role == CAMERA_INSIDE && cameras[i].presenceLatched) return true;
  }
  return false;
}

bool insidePresenceFresh(const CameraState& c, unsigned long now) {
  return c.presenceSeen && c.presenceValid && now - c.lastPresence <= PRESENCE_MESSAGE_TIMEOUT_MS;
}

bool insidePresenceReadyForEmpty(unsigned long now) {
#if REQUIRE_INSIDE_PRESENCE_CLEAR
  if (!rosterValid) return false;
  for (int i = 0; i < cameraCount; i++) {
    const CameraState& c = cameras[i];
    if (c.role == CAMERA_INSIDE && (!insidePresenceFresh(c, now) || !c.presenceClear || c.presenceLatched)) return false;
  }
#endif
  return true;
}

int recomputeOccupancyFromCameraTotals() {
  unsigned long totalIn, totalOut;
  computeProcessedTotals(&totalIn, &totalOut);
  int64_t net = (int64_t)totalIn - (int64_t)totalOut;
  if (net < 0) net = 0;
  if (net > MAX_OCCUPANCY) net = MAX_OCCUPANCY;
  countedOccupancy = (int)net;
  presenceOverride = countedOccupancy == 0 && anyInsidePresenceLatched();
  currentOccupancy = presenceOverride ? 1 : countedOccupancy;
  return currentOccupancy;
}

void resetSessionCountsForVacancy(const char* reason) {
  // Presence must be clear BEFORE zero-count session resets are allowed.
  if (currentOccupancy != 0 || !insidePresenceReadyForEmpty(millis())) return;
  for (int i = 0; i < cameraCount; i++) { cameras[i].entries = 0; cameras[i].exits = 0; }
  recomputeOccupancyFromCameraTotals();
  logEvent("SESSION_RESET", reason);
}

void writeOutputs(int red, int green, int yellow, int robotLevel) {
  digitalWrite(PIN_RELAY_RED_BUZZER, red);
  digitalWrite(PIN_RELAY_GREEN, green);
  digitalWrite(PIN_RELAY_YELLOW, yellow);
  digitalWrite(PIN_RELAY_OCCUPANCY, robotLevel);
}
void setRunOccupiedOutputs() { writeOutputs(HIGH, LOW, LOW, ROBOT_DISABLE_LEVEL); }
void setRunEmptyOutputs() { writeOutputs(LOW, HIGH, LOW, ROBOT_ENABLE_LEVEL); }
void setRunClearingOutputs(bool redOn) { writeOutputs(redOn ? HIGH : LOW, LOW, LOW, ROBOT_DISABLE_LEVEL); }

void refreshRunOutputs() {
  if (currentState != STATE_RUN) return;
  if (currentOccupancy > 0) {
    roomWasOccupied = true; vacancyClearActive = false;
    setRunOccupiedOutputs(); return;
  }
  if (!insidePresenceReadyForEmpty(millis()) || !rosterValid) {
    // Hold robot disabled during pending/unknown interior detection. Restart clear delay later.
    roomWasOccupied = true; vacancyClearActive = false;
    setRunOccupiedOutputs(); return;
  }
  if (roomWasOccupied && !vacancyClearActive) {
    vacancyClearActive = true; vacancyClearStarted = millis();
    runRedBlinkTimer = vacancyClearStarted; runRedBlinkOn = true;
    resetSessionCountsForVacancy("vacancy_timer");
    setRunClearingOutputs(true); return;
  }
  if (!vacancyClearActive) setRunEmptyOutputs();
}

void applyOutputs() {
  faultPulseOn = false; faultPulseTimer = millis();
  switch (currentState) {
    case STATE_STARTUP: writeOutputs(LOW, LOW, HIGH, ROBOT_DISABLE_LEVEL); break;
    case STATE_AWAIT_START:
      writeOutputs(LOW, LOW, HIGH, ROBOT_DISABLE_LEVEL);
      awaitBlinkOn = true; awaitBlinkTimer = millis(); break;
    case STATE_FAULT:
      writeOutputs(HIGH, LOW, LOW, ROBOT_DISABLE_LEVEL);
      faultPulseOn = true; break;
    case STATE_MAINTENANCE:
      vacancyClearActive = false; roomWasOccupied = false;
      writeOutputs(LOW, HIGH, HIGH, ROBOT_ENABLE_LEVEL); break;
    case STATE_RUN:
      vacancyClearActive = false;
      roomWasOccupied = currentOccupancy > 0;
      refreshRunOutputs(); break;
  }
}

void changeState(SystemState state) {
  if (state == currentState) return;
  SystemState previous = currentState; currentState = state;
  if (state != STATE_RUN && state != STATE_AWAIT_START) vacancyClearActive = false;
  char detail[80];
  snprintf(detail, sizeof(detail), "from=%s,counted=%d,occ=%d", stateNames[previous], countedOccupancy, currentOccupancy);
  logEvent(stateNames[state], detail);
  applyOutputs();
}

bool occupancyRelayActive() {
  return currentState == STATE_MAINTENANCE ||
    (currentState == STATE_RUN && currentOccupancy == 0 && !vacancyClearActive &&
     rosterValid && insidePresenceReadyForEmpty(millis()));
}

void markCameraSeen(int idx, unsigned long now) {
  CameraState& c = cameras[idx];
  if (c.disconnected) logEvent("CAMERA_ONLINE", cameraIds[idx]);
  c.disconnected = false; c.disconnectedSince = 0;
  c.hasSeen = true; c.lastHb = now;
}

void handlePresence(int idx, JsonDocument& doc, unsigned long now) {
  CameraState& c = cameras[idx];
  // Role authority is solely in this ESP32 roster. Outside presence cannot affect occupancy.
  if (c.role != CAMERA_INSIDE) return;
  if (!doc["valid"].is<bool>() || !doc["present"].is<bool>() || !doc["state"].is<const char*>()) {
    errPresence++; return;
  }
  const char* state = doc["state"];
  bool present = doc["present"].as<bool>();
  bool knownState = strcmp(state, "present") == 0 || strcmp(state, "clear") == 0 ||
                    strcmp(state, "pending") == 0 || strcmp(state, "unknown") == 0;
  long age = doc["frame_age_ms"] | -1L;
  bool valid = doc["valid"].as<bool>() && knownState && age >= 0 && age <= PRESENCE_SOURCE_MAX_AGE_MS;
  // Retained confirmation duration survives a move into the door ROI before the
  // first published presence arrives. Current candidate continuity can then be zero.
  unsigned long confirmed = doc["confirmed_ms"] | 0UL;
  bool wasLatched = c.presenceLatched;
  c.presenceSeen = true; c.lastPresence = now;
  c.presenceFrameAge = age; c.presenceValid = valid;
  c.presenceClear = valid && !present && strcmp(state, "clear") == 0;
  strncpy(c.presenceState, valid ? state : "unknown", sizeof(c.presenceState) - 1);
  c.presenceState[sizeof(c.presenceState) - 1] = '\0';
  if (valid && present && strcmp(state, "present") == 0 &&
      (c.presenceLatched || confirmed > PRESENCE_CONFIRM_MS)) c.presenceLatched = true;
  if (c.presenceClear) c.presenceLatched = false;
  if (wasLatched != c.presenceLatched) logEvent(c.presenceLatched ? "PRESENCE_ON" : "PRESENCE_CLEAR", cameraIds[idx]);
  recomputeOccupancyFromCameraTotals();
  refreshRunOutputs();
}

void onMqttMessage(const char* topic, const char* payload) {
#if RAW_MQTT_DEBUG
  Serial.printf("[RAW_IN] topic=%s payload=%s\n", topic, payload);
#endif
  char id[32];
  if (!extractDeviceId(topic, id, sizeof(id))) return;
  int idx = findCamera(id);
  if (idx < 0) return;
  unsigned long now = millis();
  bool event = topicEndsWith(topic, "/event");
  bool heartbeat = topicEndsWith(topic, "/heartbeat");
  bool presence = topicEndsWith(topic, "/presence");
  if (!event && !heartbeat && !presence) return;
  if (heartbeat) {
    msgCountHeartbeat++; markCameraSeen(idx, now); return;
  }
  if (strlen(payload) > MAX_PAYLOAD_BYTES) { errOversizedPayload++; return; }
  JsonDocument doc;
  if (deserializeJson(doc, payload)) { errJsonParse++; return; }
  if (presence) {
    msgCountPresence++;
    // Presence ticks prove node execution, not fresh inference; validated separately.
    handlePresence(idx, doc, now); return;
  }
  msgCountEvent++; markCameraSeen(idx, now); cameras[idx].lastEvent = now;
  const char* type = doc["type"] | "";
  long trackId = doc["track_id"] | -1L;
  if (strcmp(type, "ENTRY") == 0) {
    cameras[idx].entries++;
  } else if (strcmp(type, "EXIT") == 0) {
    // Preserve the normal counter's zero-exit guard. Presence never creates a fake ENTRY.
    if (countedOccupancy == 0) {
      logEvent("EXIT_IGNORED", id); refreshRunOutputs(); return;
    }
    cameras[idx].exits++;
  } else { errUnknownEvent++; return; }
  recomputeOccupancyFromCameraTotals();
  char detail[100];
  snprintf(detail, sizeof(detail), "camera=%s,trk=%ld,counted=%d,occ=%d", id, trackId, countedOccupancy, currentOccupancy);
  logEvent(type, detail); refreshRunOutputs();
}

void handleStartPress(const char* source) {
  if (currentState != STATE_AWAIT_START) { logEvent("START_IGNORED", stateNames[currentState]); return; }
  if (!rosterValid) { logEvent("START_IGNORED", "invalid_camera_roster"); return; }
  logEvent("START_ACK", source); changeState(STATE_RUN);
}

void handleClearCountHold(const char* source) {
  if (startupSettling || currentState == STATE_STARTUP) return;
  for (int i = 0; i < cameraCount; i++) { cameras[i].entries = 0; cameras[i].exits = 0; }
  // Manual count reset must not erase the independent physical-presence latch.
  recomputeOccupancyFromCameraTotals();
  roomWasOccupied = currentState == STATE_RUN; vacancyClearActive = false;
  logEvent("COUNT_CLEAR", source);
  if (currentState == STATE_RUN) refreshRunOutputs(); else applyOutputs();
}

void handleMaintenanceHold(const char* source) {
  if (startupSettling || currentState == STATE_STARTUP) return;
  logEvent(currentState == STATE_MAINTENANCE ? "MAINT_OFF" : "MAINT_ON", source);
  changeState(currentState == STATE_MAINTENANCE ? STATE_AWAIT_START : STATE_MAINTENANCE);
}

bool isGreenButtonPressed() { return digitalRead(PIN_BTN_GREEN) == BTN_GREEN_PRESSED_LEVEL; }
void handleButtons(unsigned long now) {
  bool pressed = isGreenButtonPressed();
  if (pressed != lastGreenRaw) {
    lastGreenRaw = pressed;
    Serial.printf("[BTN_RAW] GREEN GPIO%d %s at %lums\n", PIN_BTN_GREEN, pressed ? "PRESSED" : "RELEASED", now);
  }
  if (pressed && !greenPressed) {
    greenPressed = true; greenPressStart = now;
    greenPressEligible = currentState == STATE_AWAIT_START; greenPressHandled = false; return;
  }
  if (pressed && greenPressed) {
    if (!greenPressHandled && now - greenPressStart >= MAINT_HOLD_MS) {
      greenPressHandled = true; handleMaintenanceHold("button");
    }
    return;
  }
  if (!pressed && greenPressed) {
    unsigned long held = now - greenPressStart;
    if (!greenPressHandled && held >= CLEAR_COUNT_HOLD_MS) handleClearCountHold("button");
    else if (!greenPressHandled && greenPressEligible && held >= BTN_DEBOUNCE_MS) handleStartPress("button");
    greenPressed = false; greenPressEligible = false; greenPressHandled = false;
  }
}

void updateOutputTimers(unsigned long now) {
  if (currentState == STATE_FAULT) {
    unsigned long duration = faultPulseOn ? FAULT_PULSE_ON_MS : FAULT_PULSE_OFF_MS;
    if (now - faultPulseTimer >= duration) {
      faultPulseOn = !faultPulseOn; faultPulseTimer = now;
      digitalWrite(PIN_RELAY_RED_BUZZER, faultPulseOn ? HIGH : LOW);
    }
  }
  if (currentState == STATE_AWAIT_START && now - awaitBlinkTimer >= AWAIT_BLINK_MS) {
    awaitBlinkOn = !awaitBlinkOn; awaitBlinkTimer = now;
    digitalWrite(PIN_RELAY_YELLOW, awaitBlinkOn ? HIGH : LOW);
  }
  if (currentState != STATE_RUN) return;
  refreshRunOutputs();  // Also catches stale presence without waiting for a new MQTT message.
  if (!vacancyClearActive) return;
  if (now - vacancyClearStarted >= VACANCY_CLEAR_MS) {
    vacancyClearActive = false; roomWasOccupied = false; setRunEmptyOutputs(); return;
  }
  if (now - runRedBlinkTimer >= RUN_RED_BLINK_MS) {
    runRedBlinkOn = !runRedBlinkOn; runRedBlinkTimer = now;
    setRunClearingOutputs(runRedBlinkOn);
  }
}

void runAggregation(unsigned long now) {
  if (startupSettling) return;
  bool fault = !rosterValid;
  for (int i = 0; i < cameraCount; i++) {
    CameraState& c = cameras[i];
    if (c.hasSeen && !c.disconnected && now - c.lastHb > FAULT_TIMEOUT_MS) {
      c.disconnected = true; c.disconnectedSince = now;
      logEvent("CAMERA_OFFLINE", cameraIds[i]);
    }
    if (CAMERA_FAILURE_TRIGGERS_FAULT && (c.disconnected || !c.hasSeen)) fault = true;
  }
  if (fault && (currentState == STATE_RUN || currentState == STATE_AWAIT_START)) {
    changeState(STATE_FAULT); return;
  }
  if (!fault && ethConnected && currentState == STATE_FAULT) {
#if BYPASS_AWAIT_START
    changeState(STATE_RUN);
#else
    changeState(STATE_AWAIT_START);
#endif
  }
}

void runWatchdog(unsigned long now) {
  if (startupSettling && now - systemBootTime >= STARTUP_SETTLE_MS) {
    startupSettling = false;
    bool missing = false;
    for (int i = 0; i < cameraCount; i++) {
      if (!cameras[i].hasSeen) {
        missing = true; cameras[i].disconnected = true; cameras[i].disconnectedSince = now;
      }
    }
    if (!rosterValid || (CAMERA_FAILURE_TRIGGERS_FAULT && missing)) changeState(STATE_FAULT);
    else {
#if BYPASS_AWAIT_START
      changeState(STATE_RUN);
#else
      changeState(STATE_AWAIT_START);
#endif
    }
    return;
  }
  runAggregation(now);
}

void checkEthernetRecovery(unsigned long now) {
  if (!ethConnected) {
    if (!ethDownTiming) { ethDownTiming = true; ethDownSince = now; }
    else {
      unsigned long downFor = now - ethDownSince;
      if (downFor > ETH_DOWN_FAULT_MS && !startupSettling &&
          currentState != STATE_FAULT && currentState != STATE_MAINTENANCE) {
        logEvent("FAULT_ON", "eth_link_down"); changeState(STATE_FAULT);
      }
      if (downFor > ETH_RECOVERY_MS) {
        ETH.begin(ETH_PHY_W5500, 1, ETH_SPI_CS, ETH_SPI_IRQ, ETH_SPI_RST, SPI);
        ethDownTiming = false;
      }
    }
  } else if (ethDownTiming) { ethDownTiming = false; logEvent("ETH_ONLINE", "link_recovered"); }
}

void onEthEvent(arduino_event_id_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START: ETH.config(staticIP, gatewayIP, subnetMask, dnsIP); break;
    case ARDUINO_EVENT_ETH_GOT_IP: ethConnected = true; Serial.println(ETH.localIP()); break;
    case ARDUINO_EVENT_ETH_DISCONNECTED: ethConnected = false; break;
    default: break;
  }
}

void publishJson(const char* topic) {
  if (measureJson(pubDoc) >= sizeof(pubBuf)) { logEvent("STATUS_ERROR", "payload_buffer_full"); return; }
  serializeJson(pubDoc, pubBuf, sizeof(pubBuf));
#if RAW_STATUS_DEBUG
  Serial.printf("[RAW_OUT] topic=%s payload=%s\n", topic, pubBuf);
#endif
  mqttBroker.publish(topic, pubBuf);
}

void publishSystemStatus(unsigned long now) {
  pubDoc.clear();
  pubDoc["state"] = stateNames[currentState];
  pubDoc["occupancy"] = currentOccupancy;
  pubDoc["counted_occupancy"] = countedOccupancy;
  pubDoc["presence_override"] = presenceOverride;
  pubDoc["presence_ready_for_empty"] = insidePresenceReadyForEmpty(now);
  pubDoc["roster_valid"] = rosterValid;
  pubDoc["vacancy_clear_active"] = vacancyClearActive;
  pubDoc["relay"] = occupancyRelayActive();
  unsigned long rawIn = 0, rawOut = 0, totalIn = 0, totalOut = 0;
  JsonObject cams = pubDoc["cameras"].to<JsonObject>();
  for (int i = 0; i < cameraCount; i++) {
    const CameraState& c = cameras[i];
    JsonObject cam = cams[cameraIds[i]].to<JsonObject>();
    cam["door"] = c.doorLabel; cam["role"] = c.role == CAMERA_INSIDE ? "inside" : "outside";
    cam["entries"] = c.entries; cam["exits"] = c.exits;
    cam["net"] = (int64_t)c.entries - (int64_t)c.exits;
    cam["disconnected"] = c.disconnected;
    if (c.role == CAMERA_INSIDE) {
      cam["presence_state"] = insidePresenceFresh(c, now) ? c.presenceState : "unknown";
      cam["presence_fresh"] = insidePresenceFresh(c, now);
      cam["presence_latched"] = c.presenceLatched;
      cam["presence_age_ms"] = c.presenceSeen ? (long)(now - c.lastPresence) : -1L;
    }
    rawIn += c.entries; rawOut += c.exits;
  }
  DoorTotals doors[MAX_CAMERAS];
  int n = buildDoorTotals(doors, MAX_CAMERAS);
  JsonObject doorJson = pubDoc["doors"].to<JsonObject>();
  for (int i = 0; i < n; i++) {
    JsonObject door = doorJson[doors[i].label].to<JsonObject>();
    door["entries"] = doors[i].entries; door["exits"] = doors[i].exits;
    door["net"] = (int64_t)doors[i].entries - (int64_t)doors[i].exits;
    totalIn += doors[i].entries; totalOut += doors[i].exits;
  }
  pubDoc["total_in"] = totalIn; pubDoc["total_out"] = totalOut;
  pubDoc["total_net"] = (int64_t)totalIn - (int64_t)totalOut;
  pubDoc["raw_total_in"] = rawIn; pubDoc["raw_total_out"] = rawOut;
  pubDoc["raw_total_net"] = (int64_t)rawIn - (int64_t)rawOut;
  pubDoc["ts"] = now;
  publishJson("eagle/system/status");
}

void publishDiagnostics(unsigned long now) {
  pubDoc.clear();
  pubDoc["fw"] = FW_VERSION; pubDoc["uptime_s"] = now / 1000;
  pubDoc["heap"] = ESP.getFreeHeap(); pubDoc["cameras"] = cameraCount;
  pubDoc["state"] = stateNames[currentState]; pubDoc["occupancy"] = currentOccupancy;
  pubDoc["counted_occupancy"] = countedOccupancy; pubDoc["presence_override"] = presenceOverride;
  JsonObject msgs = pubDoc["msgs"].to<JsonObject>();
  msgs["event"] = msgCountEvent; msgs["hb"] = msgCountHeartbeat; msgs["presence"] = msgCountPresence;
  JsonObject errs = pubDoc["errs"].to<JsonObject>();
  errs["json"] = errJsonParse; errs["oversize"] = errOversizedPayload;
  errs["unknown"] = errUnknownEvent; errs["presence"] = errPresence;
  publishJson("eagle/system/diag");
}

void printSerialMonitor(unsigned long now) {
  unsigned long totalIn, totalOut;
  computeProcessedTotals(&totalIn, &totalOut);
  Serial.printf("\n=== ESP32 %s uptime=%lus state=%s ETH=%s ===\n", FW_VERSION, now / 1000,
                stateNames[currentState], ethConnected ? "UP" : "DOWN");
  Serial.printf("MAX totals IN=%lu OUT=%lu counted=%d effective=%d override=%s robot=%s clearing=%s\n",
    totalIn, totalOut, countedOccupancy, currentOccupancy, presenceOverride ? "YES" : "NO",
    occupancyRelayActive() ? "ENABLED" : "DISABLED", vacancyClearActive ? "YES" : "NO");
  for (int i = 0; i < cameraCount; i++) {
    CameraState& c = cameras[i];
    Serial.printf("%s door=%s role=%s entries=%lu exits=%lu status=%s presence=%s latch=%s\n",
      cameraIds[i], c.doorLabel, c.role == CAMERA_INSIDE ? "INSIDE" : "OUTSIDE", c.entries, c.exits,
      c.disconnected ? "OFFLINE" : (c.hasSeen ? "ONLINE" : "WAITING"),
      c.role == CAMERA_INSIDE ? (insidePresenceFresh(c, now) ? c.presenceState : "unknown") : "ignored",
      c.presenceLatched ? "YES" : "NO");
  }
}

void checkSerialCommands() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n'); cmd.trim();
  if (cmd == "STATUS") printSerialMonitor(millis());
  else if (cmd == "EXPORT_LOG") Serial.println("File logging disabled; use serial logs.");
  else if (cmd == "HELP") Serial.println("Commands: STATUS, EXPORT_LOG, HELP");
}

void setup() {
  Serial.begin(115200); delay(1000);
  Serial.setTimeout(100); // Avoid holding safety/output refresh for a long serial read.
  pinMode(PIN_RELAY_RED_BUZZER, OUTPUT); pinMode(PIN_RELAY_GREEN, OUTPUT);
  pinMode(PIN_RELAY_YELLOW, OUTPUT); pinMode(PIN_RELAY_OCCUPANCY, OUTPUT);
  pinMode(PIN_BTN_GREEN, INPUT);
  applyOutputs();
  logEvent("SYSTEM_START", FW_VERSION);
  esp_task_wdt_config_t wdtConfig = { .timeout_ms = HW_WDT_TIMEOUT_S * 1000,
                                    .idle_core_mask = 0, .trigger_panic = true };
  esp_task_wdt_reconfigure(&wdtConfig); esp_task_wdt_add(NULL);
  Network.onEvent(onEthEvent);
  SPI.begin(ETH_SPI_SCK, ETH_SPI_MISO, ETH_SPI_MOSI);
  ETH.begin(ETH_PHY_W5500, 1, ETH_SPI_CS, ETH_SPI_IRQ, ETH_SPI_RST, SPI);
  unsigned long ethStart = millis();
  while (!ethConnected && millis() - ethStart < 10000) { esp_task_wdt_reset(); delay(500); }
  int insideCount = 0;
  bool addedAll = EXPECTED_CAMERA_COUNT <= MAX_CAMERAS;
  for (size_t i = 0; i < EXPECTED_CAMERA_COUNT; i++) {
    if (addCamera(EXPECTED_CAMERAS[i]) < 0) addedAll = false;
    if (EXPECTED_CAMERAS[i].role == CAMERA_INSIDE) insideCount++;
  }
  // This package is for ONE physical door with ONE inside-camera authority.
  rosterValid = addedAll && insideCount == 1;
  if (!rosterValid) logEvent("CONFIG_ERROR", "define_exactly_one_inside_camera_with_unique_ids");
  mqttBroker.subscribe("eagle/+/event", [](const char* t, const char* p) { onMqttMessage(t, p); });
  mqttBroker.subscribe("eagle/+/heartbeat", [](const char* t, const char* p) { onMqttMessage(t, p); });
  mqttBroker.subscribe("eagle/+/presence", [](const char* t, const char* p) { onMqttMessage(t, p); });
  mqttBroker.begin();
  recomputeOccupancyFromCameraTotals();
  systemBootTime = millis();
  Serial.println("Counts start at zero; no flash persistence or file logging. Settling 120s.");
}

void loop() {
  esp_task_wdt_reset(); mqttBroker.loop();
  unsigned long now = millis();
  handleButtons(now); checkSerialCommands();
  if (now - lastWatchdog >= WATCHDOG_MS) { lastWatchdog = now; runWatchdog(now); }
  checkEthernetRecovery(now);
  updateOutputTimers(now);
  if (now - lastStatusPublish >= STATUS_INTERVAL_MS) { lastStatusPublish = now; publishSystemStatus(now); }
  if (now - lastDiag >= DIAG_INTERVAL_MS) { lastDiag = now; publishDiagnostics(now); }
#if SERIAL_MONITOR_ENABLED
  if (now - lastSerialMonitor >= SERIAL_MONITOR_INTERVAL_MS) { lastSerialMonitor = now; printSerialMonitor(now); }
#endif
}
