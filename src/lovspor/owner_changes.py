"""The owner-change pin file, ``ops/lovverk-owner-changes``: grammar and parser.

A pin names a reviewed lovverk commit that a later sync step may merge into
``lokale-forskrifter/`` (docs/owner-changes.md). This module only reads the
file; :mod:`lovspor.owner_changes_run` applies it. An empty file, or one
holding only comments, changes nothing.

The grammar is strict on purpose: the file is authority over published
content, so anything unexpected refuses the whole file rather than skipping a
line. Every problem is reported at once, with its line number, so one review
round fixes them all.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from lovspor.errors import LovsporError

PIN_FILE = Path("ops/lovverk-owner-changes")
MAX_PINS = 20
MAX_BYTES = 64 * 1024

_PIN = re.compile(r"([0-9a-f]{40}) ([1-9][0-9]{0,6}) ([1-9][0-9]?) ([!-~][ -~]{0,199})")
_COMMENT = re.compile(r"#[ -~]*")
_PRINTABLE = range(0x20, 0x7F)


class PinFileError(LovsporError):
    """The pin file breaks the grammar. ``problems`` lists every finding."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Pin:
    """One pin line: lovverk commit, lovverk pull request, attempt, reason."""

    line: int
    commit: str
    pull_request: int
    attempt: int
    reason: str


def parse_pin_file(data: bytes) -> tuple[Pin, ...]:
    """Parse the whole file, or raise ``PinFileError`` naming every problem.

    Blank lines and ``#`` comments are allowed. Anything else must be a pin
    line. The file must be ASCII with LF line endings (no CR, no tab, no
    control or non-ASCII byte, so nothing invisible reaches a log or an
    issue), end with a newline unless empty, hold at most ``MAX_PINS`` pins,
    and name each commit and each pull request once."""
    if len(data) > MAX_BYTES:
        raise PinFileError([f"file is {len(data)} bytes, over the {MAX_BYTES}-byte limit"])
    problems: list[str] = []
    pins: list[Pin] = []
    for number, raw in enumerate(_lines(data, problems), start=1):
        pin = _parse_line(number, raw, problems)
        if pin is not None:
            pins.append(pin)
    problems.extend(_duplicates(pins))
    if len(pins) > MAX_PINS:
        problems.append(f"{len(pins)} pins, over the limit of {MAX_PINS}")
    if problems:
        raise PinFileError(problems)
    return tuple(pins)


def load_pin_file(path: Path) -> tuple[Pin, ...]:
    """Read and parse ``path``. A missing or unreadable file is an error:
    the file is committed, so its absence is not an empty list."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PinFileError([f"cannot read {path}: {exc.strerror or exc}"]) from exc
    return parse_pin_file(data)


def _lines(data: bytes, problems: list[str]) -> list[bytes]:
    if not data:
        return []
    if data.endswith(b"\n"):
        return data[:-1].split(b"\n")
    problems.append("file does not end with a newline")
    return data.split(b"\n")


def _parse_line(number: int, raw: bytes, problems: list[str]) -> Pin | None:
    if any(byte not in _PRINTABLE for byte in raw):
        problems.append(f"line {number}: non-printable or non-ASCII byte (only LF line endings)")
        return None
    text = raw.decode("ascii")
    if text == "" or _COMMENT.fullmatch(text):
        return None
    match = _PIN.fullmatch(text)
    if match is None:
        problems.append(f"line {number}: not a pin, comment or blank line: {_describe(text)}")
        return None
    commit, pull_request, attempt, reason = match.groups()
    return Pin(number, commit, int(pull_request), int(attempt), reason)


def _describe(text: str) -> str:
    first = text.split(" ", 1)[0]
    if re.fullmatch(r"[0-9A-Fa-f]+", first) and not re.fullmatch(r"[0-9a-f]{40}", first):
        return f"commit must be 40 lowercase hex digits, got {len(first)} characters"
    return "expected '<sha40> <pull request> <attempt> <reason>'"


def _duplicates(pins: list[Pin]) -> list[str]:
    problems = []
    seen_commit: dict[str, int] = {}
    seen_pr: dict[int, int] = {}
    for pin in pins:
        if pin.commit in seen_commit:
            problems.append(
                f"line {pin.line}: commit {pin.commit} already pinned on line "
                f"{seen_commit[pin.commit]}"
            )
        else:
            seen_commit[pin.commit] = pin.line
        if pin.pull_request in seen_pr:
            problems.append(
                f"line {pin.line}: pull request {pin.pull_request} already pinned on line "
                f"{seen_pr[pin.pull_request]}; replace the line instead"
            )
        else:
            seen_pr[pin.pull_request] = pin.line
    return problems
