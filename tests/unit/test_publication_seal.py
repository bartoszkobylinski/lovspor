"""The publication seal: composing the message and the consumer's verdict.

Real git, real annotated tags and real SSH signatures from throwaway keys.
"""

from pathlib import Path

import pytest

from lovspor.publication_seal import (
    AlreadySealed,
    PublicationSealError,
    SealCompositionError,
    SealVerdict,
    SealVerificationError,
    compose_seal,
    seal_message,
    verify_seal,
)
from lovspor.temporal_attestation import AttestationError, attestation_note_blob
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
    raw_note,
    requires_ssh_keygen,
    tag,
)

SEAL = "sync/seal-1-1"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hermetic_git(monkeypatch, tmp_path)
    corpus = new_repo(tmp_path / "corpus")
    attest(corpus, git(corpus, "rev-parse", "HEAD"))
    return corpus


@pytest.fixture
def signer(tmp_path: Path) -> Signer:
    return new_signer(tmp_path / "keys")


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def _composed(repo: Path) -> bytes:
    message = compose_seal(repo)
    assert isinstance(message, bytes)
    return message


def _seal_head(repo: Path, signer: Signer, name: str = SEAL) -> None:
    tag(repo, name, _composed(repo), "HEAD", signer.key)


# ------------------------------------------------------------------ compose


def test_compose_writes_the_exact_message_bytes(repo: Path) -> None:
    head = _head(repo)
    blob = attestation_note_blob(repo, head)
    note = note_bytes(repo, head)

    message = _composed(repo)

    expected_header = (
        "lovspor-publication-seal v1\n"
        f"corpus_commit {head}\n"
        "attestation_ref refs/notes/temporal-attestations\n"
        f"attestation_blob {blob}\n"
        "\n"
    ).encode()
    assert message == expected_header + note
    assert message.endswith(b"\n")
    assert b"\r" not in message
    assert message == seal_message(head, str(blob), note)


def test_compose_refuses_a_head_without_an_attestation_note(repo: Path) -> None:
    commit(repo, "unattested")

    with pytest.raises(SealCompositionError, match="has no note"):
        compose_seal(repo)


def test_compose_refuses_a_note_naming_another_commit(repo: Path) -> None:
    other = _head(repo)
    commit(repo, "second")
    raw_note(repo, "HEAD", [{"corpus_commit": other}])

    with pytest.raises(SealCompositionError, match="no entry naming it"):
        compose_seal(repo)


def test_compose_refuses_an_unparseable_note(repo: Path) -> None:
    commit(repo, "second")
    git(repo, "notes", "--ref=refs/notes/temporal-attestations", "add", "-m", "not json")

    with pytest.raises(SealCompositionError):
        compose_seal(repo)


def test_compose_refuses_a_path_that_is_not_a_repository(tmp_path: Path) -> None:
    with pytest.raises(SealCompositionError, match="not a commit"):
        compose_seal(tmp_path)


def test_compose_reports_git_that_cannot_run(tmp_path: Path) -> None:
    with pytest.raises(PublicationSealError, match="could not run"):
        compose_seal(tmp_path / "missing")


@requires_ssh_keygen
def test_compose_reports_already_sealed_for_the_same_note(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)

    assert compose_seal(repo) == AlreadySealed(tag=f"refs/tags/{SEAL}")
    assert compose_seal(repo, signer.signers) == AlreadySealed(tag=f"refs/tags/{SEAL}")


@requires_ssh_keygen
def test_compose_seals_again_when_the_note_changed(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    attest(repo, _head(repo), parser_version=4)

    message = compose_seal(repo)

    assert isinstance(message, bytes)
    assert f"attestation_blob {attestation_note_blob(repo, _head(repo))}".encode() in message


@requires_ssh_keygen
def test_compose_ignores_seals_of_other_commits_and_foreign_tags(
    repo: Path, signer: Signer
) -> None:
    _seal_head(repo, signer)
    commit(repo, "second")
    attest(repo, _head(repo))
    git(repo, "tag", "sync/lightweight", "HEAD")
    tag(repo, "sync/other", b"release notes\n", "HEAD", None)

    assert isinstance(compose_seal(repo), bytes)


@requires_ssh_keygen
def test_compose_with_signers_reseals_over_an_unverifiable_seal(repo: Path) -> None:
    """A forged tag carrying the right message must not make the run a no-op."""
    tag(repo, "sync/forged", _composed(repo), "HEAD", None)

    assert compose_seal(repo) == AlreadySealed(tag="refs/tags/sync/forged")
    signers = new_signer(repo.parent / "keys").signers
    assert isinstance(compose_seal(repo, signers), bytes)


# ------------------------------------------------------------------ verify


@requires_ssh_keygen
def test_verify_accepts_a_good_seal_by_tag_and_by_commit(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    expected = SealVerdict(tag=f"refs/tags/{SEAL}", signer=PRINCIPAL)

    assert verify_seal(repo, _head(repo), signer.signers, SEAL) == expected
    assert verify_seal(repo, "HEAD", signer.signers, f"refs/tags/{SEAL}") == expected
    assert verify_seal(repo, _head(repo), signer.signers) == expected


@requires_ssh_keygen
def test_verify_rejects_an_unsigned_annotated_tag(repo: Path, signer: Signer) -> None:
    tag(repo, SEAL, _composed(repo), "HEAD", None)

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_rejects_a_key_not_in_the_signers_file(repo: Path, signer: Signer) -> None:
    stranger = new_signer(repo.parent / "stranger", "stranger")
    tag(repo, SEAL, _composed(repo), "HEAD", stranger.key)

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_rejects_a_seal_whose_object_is_another_commit(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    commit(repo, "second")

    with pytest.raises(SealVerificationError, match="does not tag commit"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_rejects_a_mismatching_corpus_commit(repo: Path, signer: Signer) -> None:
    first = _head(repo)
    message = _composed(repo)
    commit(repo, "second")
    tag(repo, SEAL, message, "HEAD", signer.key)  # signed, but the message names `first`

    with pytest.raises(SealVerificationError, match="corpus_commit does not name"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)
    assert first != _head(repo)


@requires_ssh_keygen
def test_verify_rejects_an_embedded_attestation_lacking_the_commit(
    repo: Path, signer: Signer
) -> None:
    head = _head(repo)
    message = seal_message(head, "0" * 40, b'[{"corpus_commit": "' + b"f" * 40 + b'"}]\n')
    tag(repo, SEAL, message, "HEAD", signer.key)

    with pytest.raises(SealVerificationError, match="embedded attestation has no entry"):
        verify_seal(repo, head, signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_accepts_an_embedded_object_naming_the_commit(repo: Path, signer: Signer) -> None:
    head = _head(repo)
    message = seal_message(head, "0" * 40, f'{{"corpus_commit": "{head}"}}\n'.encode())
    tag(repo, SEAL, message, "HEAD", signer.key)

    assert verify_seal(repo, head, signer.signers, SEAL).signer == PRINCIPAL


@requires_ssh_keygen
def test_verify_rejects_a_signed_tag_that_is_not_a_seal(repo: Path, signer: Signer) -> None:
    tag(repo, SEAL, b"release notes\n\nnothing here\n", "HEAD", signer.key)

    with pytest.raises(SealVerificationError, match="is not a"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_rejects_a_lightweight_tag(repo: Path, signer: Signer) -> None:
    git(repo, "tag", SEAL, "HEAD")

    with pytest.raises(SealVerificationError, match="lightweight"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_a_tampered_note_does_not_affect_a_valid_seal(repo: Path, signer: Signer) -> None:
    """The note ref is not trusted: the signed copy inside the tag decides."""
    _seal_head(repo, signer)
    raw_note(repo, "HEAD", [{"corpus_commit": "f" * 40}])

    assert verify_seal(repo, _head(repo), signer.signers, SEAL).signer == PRINCIPAL


@requires_ssh_keygen
def test_verify_by_commit_tries_every_seal_and_reports_each_failure(
    repo: Path, signer: Signer
) -> None:
    message = _composed(repo)
    tag(repo, "sync/a-unsigned", message, "HEAD", None)

    with pytest.raises(SealVerificationError, match="sync/a-unsigned"):
        verify_seal(repo, _head(repo), signer.signers)
    tag(repo, "sync/b-signed", message, "HEAD", signer.key)
    assert verify_seal(repo, _head(repo), signer.signers).tag == "refs/tags/sync/b-signed"


def test_verify_without_any_seal_tag_holds(repo: Path, tmp_path: Path) -> None:
    signers = tmp_path / "signers"
    signers.write_text('p namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")

    with pytest.raises(SealVerificationError, match="no refs/tags/sync/"):
        verify_seal(repo, _head(repo), signers)


def test_verify_refuses_a_tag_outside_the_sync_namespace(repo: Path, tmp_path: Path) -> None:
    signers = tmp_path / "signers"
    signers.write_text('p namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")

    with pytest.raises(SealVerificationError, match="is not a refs/tags/sync/"):
        verify_seal(repo, _head(repo), signers, "v1.0")


def test_verify_refuses_a_missing_tag(repo: Path, tmp_path: Path) -> None:
    signers = tmp_path / "signers"
    signers.write_text('p namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")

    with pytest.raises(SealVerificationError, match="exited"):
        verify_seal(repo, _head(repo), signers, SEAL)


@requires_ssh_keygen
def test_a_signers_file_with_only_comments_holds_no_key(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    shipped = Path(__file__).resolve().parents[2] / "ops" / "publication-signers"

    with pytest.raises(SealVerificationError, match="holds no key"):
        verify_seal(repo, _head(repo), shipped, SEAL)


def test_an_empty_or_missing_signers_file_is_refused(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(SealVerificationError, match="holds no key"):
        verify_seal(repo, _head(repo), empty)
    with pytest.raises(SealVerificationError, match="unreadable"):
        verify_seal(repo, _head(repo), tmp_path / "absent")


def test_verify_refuses_an_unknown_candidate(repo: Path, tmp_path: Path) -> None:
    signers = tmp_path / "signers"
    signers.write_text('p namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")

    with pytest.raises(SealVerificationError, match="not a commit"):
        verify_seal(repo, "f" * 40, signers)


# ------------------------------------------------------------------ registry read


def test_attestation_note_blob_separates_absent_from_unreadable(repo: Path, tmp_path: Path) -> None:
    head = _head(repo)
    assert attestation_note_blob(repo, head) == git(
        repo, "notes", "--ref=refs/notes/temporal-attestations", "list", head
    )
    assert attestation_note_blob(repo, commit(repo, "second")) is None
    with pytest.raises(AttestationError, match="unreadable"):
        attestation_note_blob(tmp_path, head)
