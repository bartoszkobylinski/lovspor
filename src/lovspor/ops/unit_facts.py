"""What systemd knows about a failed unit: its result, exit status and journal tail.

Every read degrades instead of failing: an alert that says "journal
unavailable" still reaches the operator, one that crashed on it does not.
"""

import subprocess
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lovspor.ops.errors import UnitFactsError

JOURNAL_LINES = 20
COMMAND_TIMEOUT_SECONDS = 30
# A systemd unit name with a type suffix. The leading character excludes
# `-`, so a name can never be read by systemctl or journalctl as an option.
UNIT_PATTERN = r"^[A-Za-z0-9_.:@\\][A-Za-z0-9_.:@\\-]*\.(service|timer|socket|target|path|mount)$"

Runner = Callable[[Sequence[str]], str]


class UnitName(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: str = Field(pattern=UNIT_PATTERN, max_length=256)


class UnitFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: str
    exit_status: int | None
    journal: tuple[str, ...]


def run_command(argv: Sequence[str]) -> str:
    """Run a fixed argv (no shell) and return its stdout, or raise ``UnitFactsError``."""
    try:
        done = subprocess.run(  # noqa: S603
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UnitFactsError(f"cannot run {argv[0]}: {type(error).__name__}") from error
    if done.returncode != 0:
        raise UnitFactsError(f"{argv[0]} exited {done.returncode}")
    return done.stdout


def read_unit_facts(runner: Runner, unit: str) -> UnitFacts:
    result, status = _state(runner, unit)
    return UnitFacts(result=result, exit_status=status, journal=_journal(runner, unit))


def _state(runner: Runner, unit: str) -> tuple[str, int | None]:
    argv = ["systemctl", "show", unit, "--property=Result", "--property=ExecMainStatus"]
    try:
        text = runner(argv)
    except UnitFactsError:
        return "unknown", None
    fields = dict(line.partition("=")[::2] for line in text.splitlines())
    status = fields.get("ExecMainStatus", "").strip()
    return fields.get("Result", "").strip() or "unknown", int(status) if status.isdigit() else None


def _journal(runner: Runner, unit: str) -> tuple[str, ...]:
    argv = ["journalctl", "--unit", unit, f"--lines={JOURNAL_LINES}", "--no-pager", "--quiet"]
    try:
        text = runner([*argv, "--output=short-iso"])
    except UnitFactsError as error:
        return (f"(journal unavailable: {error})",)
    return tuple(line for line in text.splitlines() if line.strip())[-JOURNAL_LINES:]
