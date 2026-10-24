#include <assert.h>
#include <stdint.h>

#include "../src/303/step_probability.h"

struct FakeStorage {
  uint8_t bytes[1024];
  size_t updates = 0;

  FakeStorage() {
    for (uint8_t &value : bytes) value = 0x42;
  }

  uint8_t read(size_t address) const {
    assert(address < sizeof(bytes));
    return bytes[address];
  }
  void update(size_t address, uint8_t value) {
    assert(address < sizeof(bytes));
    if (bytes[address] != value) {
      bytes[address] = value;
      ++updates;
    }
  }
};

static void test_packed_levels() {
  uint8_t packed[StepProbability::kBytesPerPattern];
  for (uint8_t &value : packed) value = 0xff;

  for (uint8_t step = 0; step < StepProbability::kStepsPerPattern; ++step)
    assert(StepProbability::get(packed, step) == 3);

  for (uint8_t step = 0; step < StepProbability::kStepsPerPattern; ++step)
    StepProbability::set(packed, step, step & 0x03);
  for (uint8_t step = 0; step < StepProbability::kStepsPerPattern; ++step)
    assert(StepProbability::get(packed, step) == (step & 0x03));
}

static void test_exact_thresholds() {
  const uint16_t expected[] = {0, 85, 171, 256};
  for (uint8_t level = 0; level <= StepProbability::kMaxLevel; ++level) {
    uint16_t passed = 0;
    for (uint16_t roll = 0; roll < 256; ++roll)
      passed += StepProbability::passes(level, uint8_t(roll));
    assert(passed == expected[level]);
  }
}

static void test_random_is_deterministic_and_nonconstant() {
  StepProbability::Random a(0x1234);
  StepProbability::Random b(0x1234);
  uint8_t first = a.next();
  bool changed = false;
  assert(first == b.next());
  for (uint8_t i = 0; i < 64; ++i) {
    const uint8_t av = a.next();
    assert(av == b.next());
    changed |= av != first;
  }
  assert(changed);
}

static void test_storage_migration_is_scoped_and_idempotent() {
  static constexpr size_t data_offset = 16;
  static constexpr size_t marker_offset =
      data_offset + StepProbability::kStorageBytes;
  FakeStorage storage;

  assert(StepProbability::ensure_storage(storage, data_offset, marker_offset));
  for (size_t i = 0; i < StepProbability::kStorageBytes; ++i)
    assert(storage.bytes[data_offset + i] == 0xff);
  for (size_t i = 0; i < StepProbability::kFormatMarkerSize; ++i)
    assert(storage.bytes[marker_offset + i] == StepProbability::kFormatMarker[i]);
  assert(storage.bytes[data_offset - 1] == 0x42);
  assert(storage.bytes[marker_offset + StepProbability::kFormatMarkerSize] == 0x42);

  const size_t updates = storage.updates;
  assert(!StepProbability::ensure_storage(storage, data_offset, marker_offset));
  assert(storage.updates == updates);

  storage.bytes[data_offset] = 0;
  assert(StepProbability::ensure_storage(storage, data_offset, marker_offset, true));
  assert(storage.bytes[data_offset] == 0xff);
}

int main() {
  test_packed_levels();
  test_exact_thresholds();
  test_random_is_deterministic_and_nonconstant();
  test_storage_migration_is_scoped_and_idempotent();
  return 0;
}
