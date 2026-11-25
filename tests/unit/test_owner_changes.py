"""The owner-change pin file: grammar, refusals, and the committed file."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.owner_changes import (
    MAX_BYTES,
    MAX_PINS,
    PIN_FILE,
    Pin,
    PinFileError,
    load_pin_file,
    parse_pin_file,
)

ROOT = Path(__file__).resolve().parents[2]
SHA_A = "a" * 40
SHA_B = "0123456789abcdef0123456789abcdef01234567"


def _problems(data: bytes) -> list[str]:
    with pytest.raises(PinFileError) as caught:
        parse_pin_file(data)
    return caught.value.problems


def test_committed_pin_file_is_valid_and_holds_no_pin() -> None:
    assert load_pin_file(ROOT / PIN_FILE) == ()


def test_sync_workflow_does_not_read_the_pin_file() -> None:
    """E1 adds the file only: the sync behaves exactly as before."""
    sync = (ROOT / ".github/workflows/sync.yml").read_text(encoding="utf-8")
    assert "lovverk-owner-changes" not in sync
    assert "owner-changes" not in sync


@pytest.mark.parametrize("data", [b"", b"\n", b"# only a comment\n", b"#\n\n#x\n"])
def test_empty_or_comment_only_file_has_no_pins(data: bytes) -> None:
    assert parse_pin_file(data) == ()


def test_pin_line_is_parsed() -> None:
    data = f"# header\n\n{SHA_B} 42 1 Roros 5025 repealed\n".encode()
    assert parse_pin_file(data) == (Pin(3, SHA_B, 42, 1, "Roros 5025 repealed"),)


def test_bounds_are_accepted() -> None:
    data = f"{SHA_A} 9999999 99 {'x' * 200}\n{SHA_B} 1 1 !\n".encode()
    pins = parse_pin_file(data)
    assert [(p.pull_request, p.attempt, len(p.reason)) for p in pins] == [
        (9999999, 99, 200),
        (1, 1, 1),
    ]


@pytest.mark.parametrize(
    "line",
    [
        f"{SHA_A[:39]} 1 1 short sha",
        f"{SHA_A}a 1 1 long sha",
        f"{SHA_A.upper()} 1 1 uppercase sha",
        f"{'g' * 40} 1 1 not hex",
        f"{SHA_A} 0 1 pr zero",
        f"{SHA_A} 01 1 pr leading zero",
        f"{SHA_A} 12345678 1 pr too long",
        f"{SHA_A} 1 0 attempt zero",
        f"{SHA_A} 1 100 attempt too big",
        f"{SHA_A} 1 01 attempt leading zero",
        f"{SHA_A} 1 1",
        f"{SHA_A} 1 1 ",
        f"{SHA_A} 1 1  leading space in reason",
        f"{SHA_A}  1 1 double space",
        f" {SHA_A} 1 1 leading space",
        f"{SHA_A} 1 1 {'x' * 201}",
        f"{SHA_A} x 1 pr not a number",
        " ",
        " # indented comment",
        "pin",
    ],
)
def test_malformed_line_refuses_the_file(line: str) -> None:
    problems = _problems(f"# ok\n{line}\n".encode())
    assert len(problems) == 1
    assert problems[0].startswith("line 2: ")


@pytest.mark.parametrize(
    "data",
    [
        f"{SHA_A} 1 1 crlf\r\n".encode(),
        f"{SHA_A} 1 1 tab\there\n".encode(),
        f"{SHA_A} 1 1 bidi \u202e\n".encode(),
        f"{SHA_A} 1 1 Røros\n".encode(),
        b"# nul \x00\n",
        b"# del \x7f\n",
        "# kommentar på norsk\n".encode(),
    ],
)
def test_invisible_or_non_ascii_bytes_refuse_the_file(data: bytes) -> None:
    problems = _problems(data)
    assert problems == ["line 1: non-printable or non-ASCII byte (only LF line endings)"]


def test_missing_final_newline_is_refused_and_last_line_still_checked() -> None:
    problems = _problems(f"{SHA_A} 1 1 ok\nbroken".encode())
    assert problems[0] == "file does not end with a newline"
    assert problems[1].startswith("line 2: not a pin")


def test_uppercase_sha_problem_names_the_rule() -> None:
    (problem,) = _problems(f"{SHA_A.upper()} 1 1 x\n".encode())
    assert "40 lowercase hex digits" in problem


def test_duplicate_commit_is_refused() -> None:
    problems = _problems(f"{SHA_A} 1 1 first\n{SHA_A} 2 1 second\n".encode())
    assert problems == [f"line 2: commit {SHA_A} already pinned on line 1"]


def test_duplicate_pull_request_is_refused() -> None:
    problems = _problems(f"{SHA_A} 7 1 old head\n{SHA_B} 7 1 new head\n".encode())
    assert problems == ["line 2: pull request 7 already pinned on line 1; replace the line instead"]


def test_same_commit_and_pull_request_twice_reports_both() -> None:
    problems = _problems(f"{SHA_A} 7 1 a\n{SHA_A} 7 2 retry added\n".encode())
    assert len(problems) == 2


def test_too_many_pins_is_refused() -> None:
    lines = [f"{index:040x} {index} 1 pin {index}" for index in range(1, MAX_PINS + 2)]
    problems = _problems(("\n".join(lines) + "\n").encode())
    assert problems == [f"{MAX_PINS + 1} pins, over the limit of {MAX_PINS}"]


def test_twenty_pins_are_accepted() -> None:
    lines = [f"{index:040x} {index} 1 pin {index}" for index in range(1, MAX_PINS + 1)]
    assert len(parse_pin_file(("\n".join(lines) + "\n").encode())) == MAX_PINS


def test_oversized_file_is_refused_before_parsing() -> None:
    problems = _problems(b"#" * MAX_BYTES + b"\n")
    assert problems == [f"file is {MAX_BYTES + 1} bytes, over the {MAX_BYTES}-byte limit"]


def test_every_problem_is_reported_at_once() -> None:
    data = f"bad one\n{SHA_A} 1 1 ok\nbad two\n{SHA_A} 2 1 dup\n".encode()
    problems = _problems(data)
    assert [problem.split(":")[0] for problem in problems] == ["line 1", "line 3", "line 4"]


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PinFileError, match="cannot read"):
        load_pin_file(tmp_path / "absent")


def _check(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, ["ops", "owner-changes", "check", *args])
    return result.exit_code, result.stdout, result.stderr


def test_cli_accepts_the_committed_file() -> None:
    code, out, err = _check("--file", str(ROOT / PIN_FILE))
    assert (code, err) == (0, "")
    assert out.endswith(": 0 pins\n")


def test_cli_lists_pins(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins"
    pin_file.write_bytes(f"{SHA_B} 42 3 reason\n".encode())
    code, out, _ = _check("--file", str(pin_file))
    assert code == 0
    assert out.splitlines() == [f"ok {pin_file}: 1 pin", f"pin lovverk#42 {SHA_B} attempt 3"]


def test_cli_reports_each_problem_and_exits_1(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins"
    pin_file.write_bytes(f"x\n{SHA_A} 1 1 a\n{SHA_A} 1 1 a\n".encode())
    code, out, err = _check("--file", str(pin_file))
    assert code == 1
    assert out == ""
    lines = err.splitlines()
    assert len(lines) == 3
    assert all(line.startswith(f"error: {pin_file}: line ") for line in lines)


def test_cli_missing_file_exits_1(tmp_path: Path) -> None:
    code, _, err = _check("--file", str(tmp_path / "absent"))
    assert code == 1
    assert "cannot read" in err
