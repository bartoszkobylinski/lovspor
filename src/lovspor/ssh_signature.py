"""``ssh-keygen -Y verify`` for the forarbeider policy trust root (#738, FC7b).

The forarbeider package starts no process (``tests/unit/test_forarbeider_boundary.py``),
so the one OpenSSH call its signature checks need lives here, as narrow as it
can be:

- one fixed argument vector, ``ssh-keygen -Y verify``, with the signers file,
  the revocation list (``-r``), the principal, the namespace and an explicit
  ``verify-time``; no other subcommand can be run;
- the signers, revocation list and signature are written to a private
  temporary directory from the exact bytes the caller verified, so no path a
  caller names is read;
- list arguments only, no shell, a timeout.

It answers with OpenSSH's own stdout; judging it is the caller's job. Only
:mod:`lovspor.forarbeider.policy_trust` imports this module.
"""

import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class SshVerification:
    """What ``ssh-keygen -Y verify`` checks a signature against."""

    allowed_signers: bytes
    revoked_keys: bytes
    principal: str
    namespace: str
    verify_time: datetime


def ssh_verify(check: SshVerification, message: bytes, signature: bytes) -> str | None:
    """stdout of a successful verification, ``""`` if refused, None if it cannot run."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        files = {"signers": check.allowed_signers, "revoked": check.revoked_keys, "sig": signature}
        for name, data in files.items():
            (directory / name).write_bytes(data)
        moment = check.verify_time.astimezone(UTC)
        argv = ["ssh-keygen", "-Y", "verify", "-f", str(directory / "signers")]
        argv += ["-I", check.principal, "-n", check.namespace, "-s", str(directory / "sig")]
        argv += ["-r", str(directory / "revoked"), "-O", f"verify-time={moment:%Y%m%d%H%M%SZ}"]
        return _run(argv, message)


def _run(argv: list[str], stdin: bytes) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, input=stdin, capture_output=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.decode(errors="replace") if done.returncode == 0 else ""
