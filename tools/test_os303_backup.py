#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
import zlib
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("os303_backup.py")
SPEC = importlib.util.spec_from_file_location("os303_backup", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def sample_image() -> bytes:
    image = bytearray((index * 37 + 11) & 0xFF for index in range(backup.EEPROM_SIZE))
    image[:16] = b"OS-303-v0.8\0\0\0\0\0"
    image[backup.RESTORE_MARKER_A_ADDR] = 0xFF
    image[backup.RESTORE_MARKER_B_ADDR] = 0xFF
    return bytes(image)


def backup_stream(image: bytes) -> bytes:
    stream = bytearray()
    for addr in range(0, backup.EEPROM_SIZE, backup.CHUNK_SIZE):
        chunk = image[addr : addr + backup.CHUNK_SIZE]
        stream.extend(
            backup.sysex_frame(
                (
                    backup.MANUFACTURER_ID,
                    backup.CMD_EEPROM_DATA,
                    addr & 0x7F,
                    (addr >> 7) & 0x7F,
                    backup.CHUNK_SIZE,
                    *backup.pack_7bit(chunk),
                )
            )
        )
    return bytes(stream)


class BackupToolTests(unittest.TestCase):
    def test_pack_round_trip_and_crc_reference(self):
        raw = bytes(range(256))
        self.assertEqual(backup.unpack_7bit(backup.pack_7bit(raw), len(raw)), raw)
        self.assertEqual(backup.crc16_ccitt(b"123456789"), 0x29B1)

    def test_load_backup_filters_noise_and_duplicate(self):
        image = sample_image()
        stream = backup.sysex_frame((0x01, 0x02, 0x03))
        stream += backup_stream(image)
        stream += backup_stream(image)[:44]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "capture.syx")
            path.write_bytes(stream)
            loaded, total, ignored, duplicates = backup.load_backup(path)
        self.assertEqual(loaded, image)
        self.assertEqual((total, ignored, duplicates), (130, 1, 1))

    def test_restore_stream_has_begin_data_and_end_frames(self):
        image = sample_image()
        stream = backup.make_restore_stream(image)
        messages = list(backup.iter_sysex(stream))
        self.assertEqual(len(messages), backup.CHUNK_COUNT + 2)
        self.assertEqual(messages[0][0:2], bytes((0x7D, backup.CMD_RESTORE_BEGIN)))
        self.assertTrue(all(msg[1] == backup.CMD_RESTORE_DATA for msg in messages[1:-1]))
        self.assertEqual(messages[-1][0:2], bytes((0x7D, backup.CMD_RESTORE_END)))
        self.assertEqual(len(stream), 6037)

        expected_crc = zlib.crc32(image) & 0xFFFFFFFF
        begin_crc = int.from_bytes(backup.unpack_7bit(messages[0][5:], 4), "little")
        self.assertEqual(begin_crc, expected_crc)
        restored = bytearray(backup.EEPROM_SIZE)
        for message in messages[1:-1]:
            addr = message[2] | (message[3] << 7)
            chunk = backup.unpack_7bit(message[5:42], backup.CHUNK_SIZE)
            chunk_crc = int.from_bytes(backup.unpack_7bit(message[42:45], 2), "little")
            self.assertEqual(chunk_crc, backup.crc16_ccitt(chunk))
            restored[addr : addr + backup.CHUNK_SIZE] = chunk
        end_crc = int.from_bytes(backup.unpack_7bit(messages[-1][2:], 4), "little")
        self.assertEqual(bytes(restored), image)
        self.assertEqual(end_crc, expected_crc)

    def test_prepare_rejects_recovery_marker(self):
        image = bytearray(sample_image())
        image[backup.RESTORE_MARKER_A_ADDR] = backup.RESTORE_MARKER_A
        with self.assertRaises(ValueError):
            backup.make_restore_stream(bytes(image))


if __name__ == "__main__":
    unittest.main()
