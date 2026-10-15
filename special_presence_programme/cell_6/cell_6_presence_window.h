#pragma once
#include <stdint.h>

// Shared by the actual firmware and compiler-evaluated scenario tests.
// Unsigned subtraction also works across millis() wraparound.
struct Cell6PresenceWindow {
  static constexpr uint32_t WINDOW_MS = 8000;
  static constexpr uint32_t CONFIRM_MS = 2000;
  static constexpr uint32_t MAX_SAMPLE_GAP_MS = 750;
  bool armed = false;
  uint32_t exitAt = 0;
  bool candidateActive = false;
  uint32_t candidateAt = 0;
  uint32_t lastSampleAt = 0;
  bool latched = false;

  constexpr bool open(uint32_t now) const {
    return armed && uint32_t(now - exitAt) <= WINDOW_MS;
  }

  constexpr void arm(uint32_t now) {
    armed = true;
    exitAt = now;
    candidateActive = false;
    // A new EXIT must not erase an already-confirmed person.
  }

  constexpr void breakContinuity() { candidateActive = false; }

  constexpr void resetByOperator() {
    armed = false;
    exitAt = 0;
    candidateActive = false;
    candidateAt = 0;
    lastSampleAt = 0;
    latched = false;
  }

  constexpr void expire(uint32_t now) {
    if (armed && uint32_t(now - exitAt) > WINDOW_MS) {
      armed = false;
      candidateActive = false;
    }
  }

  constexpr void observe(uint32_t now, bool valid, bool candidate,
                         bool cameraConfirmed, bool clear) {
    expire(now);
    if (valid && clear) {
      latched = false;
      candidateActive = false;
      return;
    }
    if (!valid || !candidate || !open(now)) {
      candidateActive = false;
      return;
    }
    if (!candidateActive || uint32_t(now - lastSampleAt) > MAX_SAMPLE_GAP_MS) {
      candidateActive = true;
      candidateAt = now;
    }
    lastSampleAt = now;
    if (uint32_t(now - candidateAt) > CONFIRM_MS && cameraConfirmed) latched = true;
  }
};
