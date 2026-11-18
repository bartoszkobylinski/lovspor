"""``lovspor publication-seal``: exit codes and the one stderr line."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from tests.unit.publication_seal_support import (
    PRINCIPAL,
    Signer,
    attest,
    commit,
    git,
    hermetic_git,
    new_repo,
    new_signer,
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


def _compose(repo: Path, out: Path, *extra: str) -> tuple[int, str, str]:
    return _invoke("compose", "--corpus-path", str(repo), "--out", str(out), *extra)


def _verify(repo: Path, signers: Path, *selector: str) -> tuple[int, str, str]:
    return _invoke(
        "verify", "--corpus-path", str(repo), "--allowed-signers", str(signers), *selector
    )


def test_compose_writes_the_message_and_exits_zero(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "seal.txt"

    code, stdout, _ = _compose(repo, out)

    assert code == 0
    assert stdout == f"composed {out}\n"
    assert out.read_bytes().startswith(b"lovspor-publication-seal v1\n")


def test_compose_fails_with_one_stderr_line_without_a_note(repo: Path, tmp_path: Path) -> None:
    commit(repo, "unattested")
    out = tmp_path / "seal.txt"

    code, _, stderr = _compose(repo, out)

    assert code == 1
    assert stderr.startswith("error: HEAD ")
    assert stderr.count("\n") == 1
    assert not out.exists()


@pytest.fixture
def sealed(repo: Path, tmp_path: Path) -> Signer:
    signer = new_signer(tmp_path / "keys")
    message = tmp_path / "message"
    assert _compose(repo, message)[0] == 0
    tag(repo, SEAL, message.read_bytes(), "HEAD", signer.key)
    return signer


@requires_ssh_keygen
def test_compose_reports_already_sealed_and_writes_no_file(
    repo: Path, tmp_path: Path, sealed: Signer
) -> None:
    out = tmp_path / "again.txt"

    code, stdout, _ = _compose(repo, out, "--allowed-signers", str(sealed.signers))

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
