"""The assumptions the crosswalk approval equivalents stand on (#792, issue #132).

Each test pins a behaviour of a dependency that an entry under
`mutation-equivalents/crosswalk.approval/` argues from, so a change
in that dependency turns the waiver into a red test instead of a silent gap.
"""

import subprocess
import sys
from pathlib import Path

from tests.unit.crosswalk_signing import PRINCIPAL, new_signer, sign

_FIND_PRINCIPALS_WAIT = 20


def test_assumption_find_principals_never_reads_stdin(tmp_path: Path) -> None:
    """Pins the `verify_signature` entries waiving `b""` -> `None` and
    `b""` -> `b"XXXX"` as the stdin of `ssh-keygen -Y find-principals`: it reads
    the signature from `-s` and the signers from `-f`, never from stdin. A stdin
    pipe that is never written nor closed would block a reader forever; here it
    returns the one principal anyway, and so does a stdin full of junk."""
    signer = new_signer(tmp_path)
    message = tmp_path / "approval.json"
    message.write_bytes(b"{}")
    argv = ["ssh-keygen", "-Y", "find-principals", "-s", str(sign(signer, message))]
    argv += ["-f", str(signer.signers)]
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as held:
        assert held.stdout is not None
        assert held.wait(timeout=_FIND_PRINCIPALS_WAIT) == 0
        assert held.stdout.read().split() == [PRINCIPAL.encode()]
        assert held.stdin is not None
        held.stdin.close()
    junk = subprocess.run(argv, input=b"XXXX", capture_output=True, check=True)
    assert junk.stdout.split() == [PRINCIPAL.encode()]


def test_assumption_subprocess_run_raises_only_for_a_truthy_check() -> None:
    """Pins the `_ssh_keygen` entries waiving `check=False` -> `check=None` and
    the dropped argument: `subprocess.run` raises on a nonzero exit only when
    `check` is truthy, and `check` defaults to false."""
    failing = [sys.executable, "-c", "raise SystemExit(3)"]
    for kwargs in ({"check": False}, {"check": None}, {}):
        done = subprocess.run(failing, capture_output=True, **kwargs)  # noqa: PLW1510
        assert done.returncode == 3, kwargs
