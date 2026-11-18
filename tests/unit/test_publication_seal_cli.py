"""``lovspor publication-seal``: exit codes and the one stderr line."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.publication_seal import seal_message
from tests.unit.publication_seal_support import (
    PRINCIPAL,
    Signer,
    attest,
    commit,
    git,
    hermetic_git,
    new_repo,
    new_signer,
    note_bytes,
    requires_ssh_keygen,
    tag,
)

SEAL = "sync/seal-7-1"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hermetic_git(monkeypatch, tmp_path)
    corpus = new_repo(tmp_path / "corpus")
    attest(corpus, git(corpus, "rev-parse", "HEAD"))
    return corpus


def _invoke(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, ["publication-seal", *args])
    return result.exit_code, result.stdout, result.stderr


def _compose(repo: Path, out: Path, signers: Path, name: str = SEAL) -> tuple[int, str, str]:
    return _invoke(
        "compose",
        "--corpus-path",
        str(repo),
        "--tag-name",
        name,
        "--allowed-signers",
        str(signers),
        "--out",
        str(out),
    )


@pytest.fixture
def no_key(tmp_path: Path) -> Path:
    signers = tmp_path / "no-key-signers"
    signers.write_text("# no key yet\n", encoding="utf-8")
    return signers


def _verify(repo: Path, signers: Path, *selector: str) -> tuple[int, str, str]:
    return _invoke(
        "verify", "--corpus-path", str(repo), "--allowed-signers", str(signers), *selector
    )


def test_compose_writes_the_unsigned_tag_and_exits_zero(
    repo: Path, tmp_path: Path, no_key: Path
) -> None:
    out = tmp_path / "seal.txt"

    code, stdout, _ = _compose(repo, out, no_key)

    assert code == 0
    assert stdout == f"composed {out}\n"
    head = git(repo, "rev-parse", "HEAD")
    lines = out.read_bytes().split(b"\n")
    assert lines[:3] == [f"object {head}".encode(), b"type commit", f"tag {SEAL}".encode()]
    assert re.fullmatch(
        rb"tagger lovspor-sync\[bot\] <lovspor-sync@noreply\.github\.com> \d+ \+0000", lines[3]
    )
    assert lines[5] == b"lovspor-publication-seal v1"


def test_compose_fails_with_one_stderr_line_without_a_note(
    repo: Path, tmp_path: Path, no_key: Path
) -> None:
    commit(repo, "unattested")
    out = tmp_path / "seal.txt"

    code, _, stderr = _compose(repo, out, no_key)

    assert code == 1
    assert stderr.startswith("error: HEAD ")
    assert stderr.count("\n") == 1
    assert not out.exists()


def test_compose_refuses_a_malformed_tag_name_with_exit_two(
    repo: Path, tmp_path: Path, no_key: Path
) -> None:
    out = tmp_path / "seal.txt"

    code, _, stderr = _compose(repo, out, no_key, "release-1")

    assert code == 2
    assert stderr.count("\n") == 1
    assert not out.exists()


@pytest.fixture
def sealed(repo: Path, tmp_path: Path) -> Signer:
    signer = new_signer(tmp_path / "keys")
    head = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "notes", "--ref=refs/notes/temporal-attestations", "list", head)
    tag(repo, SEAL, seal_message(head, blob, note_bytes(repo, head)), "HEAD", signer.key)
    return signer


@requires_ssh_keygen
def test_compose_reports_already_sealed_and_writes_no_file(
    repo: Path, tmp_path: Path, sealed: Signer
) -> None:
    out = tmp_path / "again.txt"

    code, stdout, _ = _compose(repo, out, sealed.signers, "sync/seal-8-1")

    assert code == 0
    assert stdout == f"already-sealed refs/tags/{SEAL}\n"
    assert not out.exists()


@requires_ssh_keygen
def test_verify_by_tag_and_by_commit_exits_zero(repo: Path, sealed: Signer) -> None:
    head = git(repo, "rev-parse", "HEAD")

    for selector in (("--tag", SEAL), ("--commit", head), ("--tag", SEAL, "--commit", "HEAD")):
        code, stdout, _ = _verify(repo, sealed.signers, *selector)
        assert code == 0, selector
        assert stdout == f"verified refs/tags/{SEAL} signer {PRINCIPAL}\n"


@requires_ssh_keygen
def test_verify_failure_exits_one_with_one_stderr_line(
    repo: Path, tmp_path: Path, sealed: Signer
) -> None:
    stranger = new_signer(tmp_path / "stranger", "stranger")

    code, stdout, stderr = _verify(repo, stranger.signers, "--tag", SEAL)

    assert code == 1
    assert stdout == ""
    assert stderr.startswith(f"error: refs/tags/{SEAL}: signature not accepted")
    assert stderr.count("\n") == 1


def test_verify_with_an_empty_signers_file_exits_one(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "signers"
    empty.write_text("# no key yet\n", encoding="utf-8")

    code, _, stderr = _verify(repo, empty, "--commit", "HEAD")

    assert code == 1
    assert "holds no key" in stderr


def test_verify_needs_a_tag_or_a_commit(repo: Path, tmp_path: Path) -> None:
    code, _, stderr = _verify(repo, tmp_path / "signers")

    assert code == 2
    assert stderr == "error: pass --tag, --commit, or both\n"
