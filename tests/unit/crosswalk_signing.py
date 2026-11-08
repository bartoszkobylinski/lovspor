"""Throwaway SSH signers for the approval tests (#792).

Every key here is generated inside the test's temporary directory and dies with
it. None stands for the owner's key, and none is ever written into the
repository's trusted signers file.
"""

import json
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk import approval as approvals

PRINCIPAL = "test-owner@example.invalid"


@dataclass(frozen=True)
class Signing:
    key: Path
    signers: Path


def _keygen(*args: str, stdin: bytes = b"") -> None:
    subprocess.run(["ssh-keygen", *args], input=stdin, capture_output=True, check=True)


def new_signer(directory: Path, name: str = "signer", principal: str = PRINCIPAL) -> Signing:
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / name
    _keygen("-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key))
    public = key.with_name(f"{name}.pub").read_text(encoding="utf-8").strip()
    signers = directory / f"{name}.allowed_signers"
    signers.write_text(f'{principal} namespaces="{approvals.NAMESPACE}" {public}\n', "utf-8")
    return Signing(key, signers)


def sign(signer: Signing, path: Path, namespace: str = approvals.NAMESPACE) -> Path:
    _keygen("-Y", "sign", "-q", "-f", str(signer.key), "-n", namespace, str(path))
    return approvals.signature_path(path)


def approval_body(approved_input: bytes, r2: str = "approved") -> dict[str, str]:
    return {
        "input_sha256": sha256(approved_input).hexdigest(),
        "approved_by": "synthetic test approval",
        "approved_on": "2026-11-05",
        "R1": "approved",
        "R2": r2,
    }


def approval_dir(directory: Path, approved_input: bytes, signer: Signing) -> Path:
    """An approval proof directory: approved input, approval and its signature."""
    directory.mkdir(parents=True)
    (directory / approvals.APPROVED_INPUT_FILE).write_bytes(approved_input)
    approval = directory / approvals.APPROVAL_FILE
    approval.write_text(json.dumps(approval_body(approved_input)), encoding="utf-8")
    sign(signer, approval)
    return directory


def trust(monkeypatch: pytest.MonkeyPatch, signer: Signing) -> None:
    """Make the CLI trust this throwaway signer instead of the shipped signers file."""
    monkeypatch.setattr(approvals, "TRUSTED_SIGNERS", signer.signers)


def signed_approval_file(path: Path, approved_input: bytes, signer: Signing) -> Path:
    """An approval of these input bytes at ``path``, signed beside it in ``<path>.sig``."""
    path.write_text(json.dumps(approval_body(approved_input)), encoding="utf-8")
    sign(signer, path)
    return path
