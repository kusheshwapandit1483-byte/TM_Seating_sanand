#include "cell_6_presence_window.h"

// These execute in the C++ compiler, against the SAME gate used by the firmware.
// No device I/O or runtime test routines are included in the firmware image.
constexpr bool noExitNoCorrection() {
  Cell6PresenceWindow gate{};
  for (uint32_t t = 0; t <= 10000; t += 500) gate.observe(t, true, true, true, false);
  return !gate.latched;
}
constexpr bool confirmationBoundary() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 100; t <= 2100; t += 500) gate.observe(t, true, true, true, false);
  if (gate.latched) return false; // Exactly 2s is insufficient.
  gate.observe(2200, true, true, true, false);
  return gate.latched;
}
constexpr bool lateDetectionIgnored() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 8101; t <= 12000; t += 500) gate.observe(t, true, true, true, false);
  return !gate.latched && !gate.open(8101);
}
constexpr bool confirmationMustFinishWithinWindow() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 6500; t <= 10000; t += 500) gate.observe(t, true, true, true, false);
  return !gate.latched;
}
constexpr bool expiresAtCorrectBoundary() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  return gate.open(8100) && !gate.open(8101);
}
constexpr bool existingPresenceSurvivesDeadlineAndLoss() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 100; t <= 2600; t += 500) gate.observe(t, true, true, true, false);
  gate.observe(9000, false, false, false, false);
  if (!gate.latched) return false;
  gate.observe(9500, true, false, false, true);
  return !gate.latched;
}
constexpr bool gapBreaksConfirmation() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  gate.observe(100, true, true, true, false);
  gate.observe(600, true, true, true, false);
  gate.observe(1100, true, true, true, false);
  gate.observe(2100, true, true, true, false);
  gate.observe(2600, true, true, true, false);
  return !gate.latched;
}
constexpr bool robotCannotRelatchAfterClearOutsideWindow() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 100; t <= 2600; t += 500) gate.observe(t, true, true, true, false);
  gate.observe(9000, true, false, false, true);
  for (uint32_t t = 9500; t <= 13000; t += 500) gate.observe(t, true, true, true, false);
  return !gate.latched;
}
constexpr bool millisWraparound() {
  Cell6PresenceWindow gate{};
  gate.arm(0xFFFFFF00u);
  return gate.open(0x00000F00u) && !gate.open(0x00002000u);
}
constexpr bool invalidDataAndMissingCameraConfirmation() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 100; t <= 3100; t += 500) gate.observe(t, true, true, false, false);
  if (gate.latched) return false;
  gate.observe(3200, false, true, true, false);
  gate.observe(3300, true, true, true, false);
  return !gate.latched;
}
constexpr bool expiredWindowCannotReappearAfterLongUptime() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  gate.expire(8101);
  return !gate.armed && !gate.open(100);
}
static_assert(noExitNoCorrection(), "No EXIT: no correction");
static_assert(confirmationBoundary(), "Confirmation must exceed 2s");
static_assert(lateDetectionIgnored(), "Late robot-like detections must be ignored");
static_assert(confirmationMustFinishWithinWindow(), "Confirmation cannot finish after deadline");
static_assert(expiresAtCorrectBoundary(), "8s deadline boundary");
static_assert(existingPresenceSurvivesDeadlineAndLoss(), "Existing presence persists until clear");
static_assert(gapBreaksConfirmation(), "Sample gaps break continuity");
static_assert(robotCannotRelatchAfterClearOutsideWindow(), "No late relatch");
static_assert(millisWraparound(), "millis rollover is supported");
static_assert(invalidDataAndMissingCameraConfirmation(), "Invalid or unconfirmed evidence cannot latch");
static_assert(expiredWindowCannotReappearAfterLongUptime(), "Expired windows remain disarmed");

constexpr bool operatorResetClearsAndPreventsImmediateRelatch() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  for (uint32_t t = 100; t <= 2600; t += 500) gate.observe(t, true, true, true, false);
  if (!gate.latched) return false;
  gate.resetByOperator();
  if (gate.latched || gate.armed || gate.candidateActive) return false;
  // The old eight-second window would still be open without disarming it.
  for (uint32_t t = 3000; t <= 7000; t += 500) gate.observe(t, true, true, true, false);
  return !gate.latched;
}
constexpr bool newExitAfterResetAllowsNewCorrection() {
  Cell6PresenceWindow gate{};
  gate.arm(100);
  gate.resetByOperator();
  gate.arm(10000);
  for (uint32_t t = 10000; t <= 12500; t += 500) gate.observe(t, true, true, true, false);
  return gate.latched;
}
static_assert(operatorResetClearsAndPreventsImmediateRelatch(), "Operator reset clears and disarms old correction");
static_assert(newExitAfterResetAllowsNewCorrection(), "A new counted EXIT can open a new check after reset");
