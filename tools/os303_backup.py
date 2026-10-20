#!/usr/bin/env python3
"""Back up OS-303's 4 KiB EEPROM over DIN MIDI SysEx.

The firmware side is intentionally read-only. This tool saves the complete
response stream as a .syx file and also writes a verified raw .bin image.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

try:
    import mido
except ImportError as exc:  # pragma: no cover - exercised by users without deps
    raise SystemExit(
        "Missing MIDI support. Run: python3 -m pip install mido python-rtmidi"
    ) from exc


MANUFACTURER_ID = 0x7D
CMD_READ_EEPROM = 0x56
CMD_EEPROM_DATA = 0x57
EEPROM_SIZE = 4096
CHUNK_SIZE = 32


def unpack_7bit(data: tuple[int, ...] | list[int], expected: int) -> bytes:
    """Decode bitmap-prefixed groups of at most seven MIDI-safe bytes."""
    decoded = bytearray()
    pos = 0
    while len(decoded) < expected:
        if pos >= len(data):
            raise ValueError("truncated packed data")
        high_bits = data[pos]
        pos += 1
        group_len = min(7, expected - len(decoded))
        if pos + group_len > len(data):
            raise ValueError("truncated packed group")
        for bit in range(group_len):
            decoded.append(data[pos] | (((high_bits >> bit) & 1) << 7))
            pos += 1
    if pos != len(data):
        raise ValueError("unexpected trailing packed data")
    return bytes(decoded)


def parse_reply(message, expected_addr: int, expected_count: int) -> bytes | None:
    if message.type != "sysex":
        return None
    data = tuple(message.data)
    if len(data) < 5 or data[:2] != (MANUFACTURER_ID, CMD_EEPROM_DATA):
        return None
    addr = data[2] | (data[3] << 7)
    count = data[4]
    if addr != expected_addr or count != expected_count:
        return None
    return unpack_7bit(data[5:], count)


def choose_port(names: list[str], selector: str | None, kind: str) -> str:
    if selector:
        matches = [name for name in names if selector.casefold() in name.casefold()]
    else:
        matches = names
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No {kind} MIDI port matches {selector!r}. Use 'list' to see ports.")
    formatted = "\n  ".join(matches)
    raise SystemExit(
        f"More than one {kind} MIDI port matches {selector!r}:\n  {formatted}\n"
        f"Pass a more specific --{kind} value."
    )


def request_chunk(inport, outport, addr: int, timeout: float) -> tuple[bytes, bytes]:
    count = min(CHUNK_SIZE, EEPROM_SIZE - addr)
    request = mido.Message(
        "sysex",
        data=(MANUFACTURER_ID, CMD_READ_EEPROM, addr & 0x7F, (addr >> 7) & 0x7F, count),
    )

    for attempt in range(3):
        # Discard stale traffic so a retry cannot be satisfied by an old reply.
        for _ in inport.iter_pending():
            pass
        outport.send(request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in inport.iter_pending():
                decoded = parse_reply(message, addr, count)
                if decoded is not None:
                    wire = bytes((0xF0, *message.data, 0xF7))
                    return decoded, wire
            time.sleep(0.002)
        print(f"No reply at EEPROM offset {addr}; retry {attempt + 1}/3", file=sys.stderr)
    raise SystemExit(
        "The OS-303 stopped replying. Stop playback and MIDI clock, check both MIDI "
        "directions, and confirm the SysEx-backup firmware is installed."
    )


def validate_image(image: bytes) -> None:
    if len(image) != EEPROM_SIZE:
        raise ValueError(f"expected {EEPROM_SIZE} bytes, got {len(image)}")
    signature = image[:16].split(b"\0", 1)[0]
    if not signature.startswith(b"OS-303-v0.8"):
        shown = signature.decode("ascii", errors="replace")
        raise ValueError(f"unexpected EEPROM signature {shown!r}")


def backup(args) -> None:
    input_name = choose_port(mido.get_input_names(), args.input or args.port, "input")
    output_name = choose_port(mido.get_output_names(), args.output or args.port, "output")
    destination = Path(args.file).expanduser()
    if destination.suffix.lower() != ".syx":
        destination = destination.with_suffix(".syx")
    binary_destination = destination.with_suffix(".bin")

    print(f"MIDI input:  {input_name}")
    print(f"MIDI output: {output_name}")
    print("Keep the OS-303 stopped until the backup completes.")

    image = bytearray()
    sysex = bytearray()
    with mido.open_input(input_name) as inport, mido.open_output(output_name) as outport:
        for addr in range(0, EEPROM_SIZE, CHUNK_SIZE):
            chunk, wire = request_chunk(inport, outport, addr, args.timeout)
            if not args.single_read:
                check, _ = request_chunk(inport, outport, addr, args.timeout)
                if check != chunk:
                    raise SystemExit(
                        f"Two reads disagreed at EEPROM offset {addr}; no backup was saved. "
                        "Check the MIDI connection and try again."
                    )
            image.extend(chunk)
            sysex.extend(wire)
            print(f"\rRead {len(image):4d}/{EEPROM_SIZE} bytes", end="", flush=True)
    print()

    validate_image(bytes(image))
    destination.write_bytes(sysex)
    binary_destination.write_bytes(image)
    digest = hashlib.sha256(image).hexdigest()
    print(f"Saved SysEx backup: {destination}")
    print(f"Saved raw EEPROM:   {binary_destination}")
    print(f"SHA-256: {digest}")


def verify(args) -> None:
    source = Path(args.file).expanduser()
    wire = source.read_bytes()
    image = bytearray(EEPROM_SIZE)
    seen: set[int] = set()
    pos = 0
    while pos < len(wire):
        start = wire.find(b"\xF0", pos)
        if start < 0:
            break
        end = wire.find(b"\xF7", start + 1)
        if end < 0:
            raise SystemExit("Truncated SysEx message at end of file")
        data = tuple(wire[start + 1 : end])
        pos = end + 1
        if len(data) < 5 or data[:2] != (MANUFACTURER_ID, CMD_EEPROM_DATA):
            continue
        addr = data[2] | (data[3] << 7)
        count = data[4]
        if count == 0 or count > CHUNK_SIZE or addr + count > EEPROM_SIZE:
            raise SystemExit(f"Invalid EEPROM chunk at address {addr}")
        chunk = unpack_7bit(data[5:], count)
        image[addr : addr + count] = chunk
        seen.update(range(addr, addr + count))

    if len(seen) != EEPROM_SIZE:
        raise SystemExit(f"Incomplete backup: contains {len(seen)}/{EEPROM_SIZE} EEPROM bytes")
    validate_image(bytes(image))
    digest = hashlib.sha256(image).hexdigest()
    print(f"Valid OS-303 EEPROM backup ({EEPROM_SIZE} bytes)")
    print(f"SHA-256: {digest}")


def list_ports(_args) -> None:
    print("Inputs:")
    for name in mido.get_input_names():
        print(f"  {name}")
    print("Outputs:")
    for name in mido.get_output_names():
        print(f"  {name}")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="list MIDI input and output ports")

    backup_parser = commands.add_parser("backup", help="read and save the complete EEPROM")
    backup_parser.add_argument("file", help="destination .syx file")
    backup_parser.add_argument("--port", help="name fragment used for both MIDI ports")
    backup_parser.add_argument("--input", help="input port name fragment")
    backup_parser.add_argument("--output", help="output port name fragment")
    backup_parser.add_argument("--timeout", type=float, default=1.0, help="seconds per request")
    backup_parser.add_argument(
        "--single-read",
        action="store_true",
        help="read each chunk once instead of verifying it with a second read",
    )

    verify_parser = commands.add_parser("verify", help="validate a saved .syx backup")
    verify_parser.add_argument("file", help="backup .syx file")
    return parser


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()
    if args.command == "list":
        list_ports(args)
    elif args.command == "backup":
        backup(args)
    elif args.command == "verify":
        verify(args)


if __name__ == "__main__":
    main()
