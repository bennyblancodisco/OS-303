// Compact, hardware-independent per-step trigger probability support.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace StepProbability {

static constexpr uint8_t kStepsPerPattern = 32;
static constexpr uint8_t kBitsPerStep = 2;
static constexpr uint8_t kBytesPerPattern =
    kStepsPerPattern * kBitsPerStep / 8;
static constexpr uint8_t kPatternCount = 64;
static constexpr size_t kStorageBytes =
    size_t(kBytesPerPattern) * kPatternCount;
static constexpr uint8_t kMaxLevel = 3;

// Existing devices read erased EEPROM (0xff) as level 3 / 100%, so adding the
// feature cannot make old patterns unexpectedly drop notes.
inline uint8_t get(const uint8_t *packed, uint8_t step) {
  const uint8_t shift = uint8_t((step & 0x03) * kBitsPerStep);
  return uint8_t((packed[step >> 2] >> shift) & 0x03);
}

inline void set(uint8_t *packed, uint8_t step, uint8_t level) {
  const uint8_t shift = uint8_t((step & 0x03) * kBitsPerStep);
  const uint8_t mask = uint8_t(0x03 << shift);
  packed[step >> 2] = uint8_t((packed[step >> 2] & ~mask) |
                              ((level & 0x03) << shift));
}

// Levels represent 0%, approximately 33%, approximately 67%, and 100%.
// Passing the random byte in makes the decision function deterministic and
// exhaustively testable without Arduino hardware.
inline bool passes(uint8_t level, uint8_t random_byte) {
  switch (level & 0x03) {
    case 0: return false;
    case 1: return random_byte < 85;
    case 2: return random_byte < 171;
    default: return true;
  }
}

class Random {
 public:
  explicit Random(uint16_t seed = 0x6d2b) { reseed(seed); }

  void reseed(uint16_t seed) { state_ = seed ? seed : 0x6d2b; }

  uint8_t next() {
    // 16-bit xorshift: small and inexpensive on AVR, with a non-zero period of
    // 65,535. Probability is musical variation, not cryptographic randomness.
    state_ ^= uint16_t(state_ << 7);
    state_ ^= uint16_t(state_ >> 9);
    state_ ^= uint16_t(state_ << 8);
    return uint8_t(state_);
  }

 private:
  uint16_t state_;
};

static constexpr uint8_t kFormatMarker[] = {'P', 'R', 'B', 1};
static constexpr size_t kFormatMarkerSize = sizeof(kFormatMarker);

// Initialize only the new, formerly-unused EEPROM region. Returns true when a
// migration/reset was performed. Storage needs read(address) and
// update(address, value), matching Arduino EEPROMClass.
template <typename Storage>
bool ensure_storage(Storage &storage, size_t data_offset, size_t marker_offset,
                    bool force_reset = false) {
  bool valid = !force_reset;
  for (size_t i = 0; i < kFormatMarkerSize && valid; ++i)
    valid = storage.read(marker_offset + i) == kFormatMarker[i];
  if (valid) return false;

  for (size_t i = 0; i < kStorageBytes; ++i)
    storage.update(data_offset + i, 0xff);
  for (size_t i = 0; i < kFormatMarkerSize; ++i)
    storage.update(marker_offset + i, kFormatMarker[i]);
  return true;
}

}  // namespace StepProbability
