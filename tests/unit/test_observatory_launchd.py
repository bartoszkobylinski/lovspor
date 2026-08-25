"""The nightly launchd job (issue #167).

The plist is parsed as a plist, not grepped: a template that stops being valid
XML fails at `launchctl load` on the one machine that runs it, months after the
change, with no test in between.
"""

import plistlib
from pathlib import Path
from typing import Any

import pytest

from lovspor.observatory.sweeps import OBSERVATION_SLA

PLIST = Path(__file__).parents[2] / "deploy" / "launchd" / "no.lovspor.observatory.nightly.plist"


@pytest.fixture
def job() -> dict[str, Any]:
    return plistlib.loads(PLIST.read_bytes())  # type: ignore[no-any-return]


def test_the_template_is_a_valid_plist(job: dict[str, Any]) -> None:
    assert job["Label"] == "no.lovspor.observatory.nightly"


def test_it_runs_the_nightly_command_and_not_capture_all(job: dict[str, Any]) -> None:
    """`capture-all` skips the preflight. Scheduling it directly would sweep on
    a half-present archive and produce records nobody can trust."""
    assert job["ProgramArguments"][1:] == ["observatory", "nightly"]


def test_it_does_not_run_at_load(job: dict[str, Any]) -> None:
    """`launchctl load` during setup must not start a sweep against two hundred
    municipal servers as a side effect."""
    assert job["RunAtLoad"] is False


def test_it_uses_a_calendar_interval_not_an_interval(job: dict[str, Any]) -> None:
    """StartCalendarInterval survives sleep — launchd runs the job on wake and
    coalesces missed triggers. StartInterval would drift with every sleep."""
    assert "StartInterval" not in job
    assert set(job["StartCalendarInterval"]) == {"Hour", "Minute"}


def test_it_fires_once_a_day_which_is_what_the_sla_asks_for(job: dict[str, Any]) -> None:
    """The plist says *when*; `OBSERVATION_SLA` says *how often*. This is the
    one place the two have to agree, so it is asserted rather than assumed."""
    assert OBSERVATION_SLA.total_seconds() == 24 * 3600
    assert 0 <= job["StartCalendarInterval"]["Hour"] <= 23


def test_the_archive_root_is_named_and_left_as_a_placeholder(job: dict[str, Any]) -> None:
    """No default anywhere in the engine: a forgotten variable must fail loudly
    rather than write observed material somewhere it does not belong."""
    assert job["EnvironmentVariables"]["LOVSPOR_OBSERVATORY_ROOT"] == "__OBSERVATORY_ROOT__"


def test_every_machine_specific_value_is_a_placeholder(job: dict[str, Any]) -> None:
    """A committed plist carrying someone's real paths is one copy-paste away
    from a sweep writing into the wrong archive."""
    machine_specific = [
        job["ProgramArguments"][0],
        job["EnvironmentVariables"]["LOVSPOR_OBSERVATORY_ROOT"],
        job["StandardOutPath"],
        job["StandardErrorPath"],
    ]

    assert all("__" in value for value in machine_specific), machine_specific


def test_it_yields_to_whatever_the_machine_is_doing(job: dict[str, Any]) -> None:
    """The sweep is already politeness-bound per host; it must not also compete
    for the machine."""
    assert job["ProcessType"] == "Background"
    assert job["LowPriorityIO"] is True
