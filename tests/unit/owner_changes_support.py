"""Helpers shared by the owner-change tests."""

from pathlib import Path

from lovspor.owner_changes import PIN_FILE, parse_pin_file

ROOT = Path(__file__).resolve().parents[2]


def without_pins(data: bytes) -> bytes:
    """``data`` with every line the parser reads as a pin removed; all other lines kept as-is."""
    pinned = {pin.line for pin in parse_pin_file(data)}
    lines = data.split(b"\n")
    return b"\n".join(line for number, line in enumerate(lines, 1) if number not in pinned)


def committed_header() -> bytes:
    """The committed pin file without its pins, so a real pin keeps the empty-file tests valid."""
    return without_pins((ROOT / PIN_FILE).read_bytes())
