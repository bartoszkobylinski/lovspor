"""The policy signature subprocess uses exact evidence and fails closed."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor.ssh_signature import TIMEOUT_SECONDS, SshVerification, ssh_verify
from tests.unit.forarbeider_authorization_support import SIGNED_AT


def test_ssh_verify_passes_exact_bytes_and_removes_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = SshVerification(b"signers\r\n", b"revoked\n", "owner", "policy", SIGNED_AT)
    paths: list[Path] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        signers, signature, revoked = (
            Path(argv[argv.index(flag) + 1]) for flag in ("-f", "-s", "-r")
        )
        paths.extend((signers, signature, revoked))
        assert signers.read_bytes() == check.allowed_signers
        assert revoked.read_bytes() == check.revoked_keys
        assert signature.read_bytes() == b"signature\r\n"
        assert argv == [
            "ssh-keygen",
            "-Y",
            "verify",
            "-f",
            str(signers),
            "-I",
            "owner",
            "-n",
            "policy",
            "-s",
            str(signature),
            "-r",
            str(revoked),
            "-O",
            "verify-time=20261114100000Z",
        ]
        assert kwargs == {
            "input": b"message\x00\r\n",
            "capture_output": True,
            "timeout": TIMEOUT_SECONDS,
            "check": False,
        }
        return subprocess.CompletedProcess(argv, 0, stdout=b"verified\n")

    monkeypatch.setattr(subprocess, "run", run)
    assert ssh_verify(check, b"message\x00\r\n", b"signature\r\n") == "verified\n"
    assert paths
    assert all(not path.parent.exists() for path in paths)


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("ssh-keygen"), subprocess.TimeoutExpired("ssh-keygen", 30)]
)
def test_ssh_verify_process_failure_is_unverifiable(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def run(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    check = SshVerification(b"", b"", "owner", "policy", SIGNED_AT)
    assert ssh_verify(check, b"message", b"signature") is None


def test_ssh_verify_nonzero_exit_discards_success_shaped_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, stdout=b'Good "policy" signature for owner')

    monkeypatch.setattr(subprocess, "run", run)
    check = SshVerification(b"", b"", "owner", "policy", SIGNED_AT)
    assert ssh_verify(check, b"message", b"signature") == ""
