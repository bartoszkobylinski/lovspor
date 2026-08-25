"""The outbound half of the dead-man switch (issue #167, part 3).

A sweep that fails can say so. A sweep that never *ran* cannot — there is no
process to speak, and the observation log is silent either way: two hundred
municipalities with nothing new and a machine that has been powered off for
three days produce exactly the same absence of records.

So the alarm is inverted. After every run the sweep reports out to a service
that is not on this machine, and that service alarms when the report fails to
arrive. Nobody has to detect the machine dying; it is enough that it stopped
saying it is alive. The observer has to be remote for the same reason a smoke
detector is not powered from the burning room: a watchdog on this Mac cannot
notice that this Mac is off.

Three decisions worth stating, because each one is a place this could quietly
become useless:

**Only `failed` reports failure.** A `degraded` sweep still ran, and liveness
is what this switch guards. Ten sources already refuse on a normal night, so
alarming on degradation would fire nightly, and a monitor that cries wolf gets
muted — which loses the liveness signal along with the noise. Degradation has
its own channels: the exit code, `observatory status`, and the run record. The
status still travels in the ping body, so the service's history shows what kind
of night it was.

**A heartbeat that cannot be sent is loud, and never fatal.** It must not fail
an otherwise good sweep — the sweep is the point, the telemetry is not. But it
must not be swallowed either: a switch that silently stopped reporting is
indistinguishable from a dead machine, and the operator would learn about it
from a false alarm at 3am rather than from the log.

**Never report from a record this invocation did not write.** Reading "the
latest run" and pinging success off it would, on any unexpected error, report
yesterday's healthy sweep as today's — the exact failure the switch exists to
catch, performed by the switch itself.
"""

import os

import httpx

from lovspor.observatory.sweeps import SweepRun

#: Where to report. No default: a heartbeat quietly pointing at nothing is
#: worse than none, because the dashboard would simply never have existed while
#: everyone assumed it had.
ENV_HEARTBEAT_URL = "LOVSPOR_OBSERVATORY_HEARTBEAT_URL"

#: The sweep waits out per-host rate limits for hours; it must not also wait on
#: a monitoring endpoint. Short, and failure here is never fatal.
HEARTBEAT_TIMEOUT_SECONDS = 10.0

#: Appended for a run that could not sweep. The convention is healthchecks.io's
#: and is shared by most hosted checks.
FAIL_SUFFIX = "/fail"


def heartbeat_url() -> str | None:
    """The configured endpoint, or None when no switch is armed."""
    url = os.environ.get(ENV_HEARTBEAT_URL, "").strip()
    return url or None


def ping_url(base: str, run: SweepRun) -> str:
    """Where this run reports.

    `failed` is the only status that reports failure. See the module docstring:
    a degraded sweep ran, and alarming on that would fire most nights.
    """
    return f"{base}{FAIL_SUFFIX}" if run.status == "failed" else base


def send_heartbeat(base: str, run: SweepRun, client: httpx.Client) -> bool:
    """Report this run; True when the service accepted it.

    Every transport failure is caught. The caller decides what to say about it,
    and what it must not do is turn a completed sweep into a failed command.
    """
    try:
        response = client.post(
            ping_url(base, run),
            content=run.model_dump_json(),
            headers={"content-type": "application/json"},
            timeout=HEARTBEAT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return False
    return response.is_success
