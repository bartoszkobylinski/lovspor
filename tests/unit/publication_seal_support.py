"""A real corpus repository, attestation notes and throwaway SSH seal keys.

Every key is generated inside the test's temporary directory and dies with it;
none stands for the project's publication-seal key. Git runs with an empty
global configuration so a developer's signing settings cannot leak in.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    TemporalAttestation,
    write_attestation,
)

PRINCIPAL = "test-seal@example.invalid"

requires_ssh_keygen = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="SSH tag signing needs ssh-keygen"
)


@dataclass(frozen=True)
class Signer:
    key: Path
    signers: Path


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "lovspor-sync[bot]")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "sync@example.invalid")


def new_repo(directory: Path) -> Path:
    directory.mkdir()
    git(directory, "init", "-q", "-b", "main")
    commit(directory, "first")
    return directory


def commit(repo: Path, message: str) -> str:
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def attest(repo: Path, corpus_commit: str, parser_version: int = 3) -> None:
    write_attestation(
        repo,
        TemporalAttestation(
            corpus_commit=corpus_commit,
            parser_version=parser_version,
            documents_reconciled=2,
            notes_total=5,
            events_total=4,
            attested_at=datetime(2026, 11, 18, 4, 0, tzinfo=UTC),
        ),
    )


def raw_note(repo: Path, target: str, entries: object) -> None:
    """A note written around the registry: the shapes it would refuse to write."""
    payload = json.dumps(entries)
    git(repo, "notes", f"--ref={ATTESTATION_NOTES_REF}", "add", "-f", "-m", payload, target)


def note_bytes(repo: Path, target: str) -> bytes:
    blob = git(repo, "notes", f"--ref={ATTESTATION_NOTES_REF}", "list", target)
    return subprocess.run(
        ["git", "cat-file", "blob", blob], cwd=repo, capture_output=True, check=True
    ).stdout


def new_signer(directory: Path, name: str = "seal") -> Signer:
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)],
        capture_output=True,
        check=True,
    )
    public = key.with_name(f"{name}.pub").read_text(encoding="utf-8").split()[:2]
    signers = directory / f"{name}.allowed_signers"
    signers.write_text(
        f'# a comment line\n\n{PRINCIPAL} namespaces="git" {" ".join(public)}\n',
        encoding="utf-8",
    )
    return Signer(key, signers)


def tag(repo: Path, name: str, message: bytes, target: str, key: Path | None) -> None:
    """An annotated tag with an exact message; SSH-signed when ``key`` is given."""
    path = repo.parent / f"{name.replace('/', '_')}.msg"
    path.write_bytes(message)
    sign = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}"] if key else []
    mode = ["-s"] if key else ["-a"]
    git(repo, *sign, "tag", *mode, "--cleanup=verbatim", "-F", str(path), name, target)
