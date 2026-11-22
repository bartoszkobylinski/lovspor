"""The publication seal: composing the message and the consumer's verdict.

Real git, real annotated tags and real SSH signatures from throwaway keys.
"""

import base64
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.publication_seal import (
    AlreadySealed,
    PublicationSealError,
    SealCompositionError,
    SealRequest,
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


def test_shipped_signer_pins_the_owner_key() -> None:
    shipped = Path(__file__).resolve().parents[2] / "ops" / "publication-signers"
    entries = [
        line.split()
        for line in shipped.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(entries) == 1
    principal, options, key_type, encoded_key = entries[0]
    assert principal == "lovverk-publication-seal"
    assert options == 'namespaces="git"'
    assert key_type == "ssh-ed25519"
    key = base64.b64decode(encoded_key, validate=True)
    assert key[:15] == b"\x00\x00\x00\x0bssh-ed25519"
    assert key[15:19] == b"\x00\x00\x00\x20"
    assert len(key) == 51
    fingerprint = base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
    assert fingerprint == "f/PzXf91YUy3WdQQVqz3tSy8HHLbXpadlxbBXXQ3wVs"


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


def _message(repo: Path) -> bytes:
    """The seal message for HEAD, built from the note the way compose does."""
    head = _head(repo)
    return seal_message(head, str(attestation_note_blob(repo, head)), note_bytes(repo, head))


def _request(signers: Path, name: str = SEAL) -> SealRequest:
    return SealRequest(tag_name=name, tagged_at=1795000000, allowed_signers=signers)


def _seal_head(repo: Path, signer: Signer, name: str = SEAL) -> None:
    tag(repo, name, _message(repo), "HEAD", signer.key)


@pytest.fixture
def no_key(tmp_path: Path) -> Path:
    signers = tmp_path / "no-key-signers"
    signers.write_text("# no key yet\n", encoding="utf-8")
    return signers


# ------------------------------------------------------------------ compose


def test_compose_writes_the_exact_unsigned_tag_object(repo: Path, no_key: Path) -> None:
    head = _head(repo)
    blob = attestation_note_blob(repo, head)
    note = note_bytes(repo, head)

    payload = compose_seal(repo, _request(no_key))

    expected = (
        f"object {head}\n"
        "type commit\n"
        f"tag {SEAL}\n"
        "tagger lovspor-sync[bot] <lovspor-sync@noreply.github.com> 1795000000 +0000\n"
        "\n"
        "lovspor-publication-seal v1\n"
        f"corpus_commit {head}\n"
        "attestation_ref refs/notes/temporal-attestations\n"
        f"attestation_blob {blob}\n"
        "\n"
    ).encode() + note
    assert payload == expected
    assert b"\r" not in payload


@requires_ssh_keygen
def test_the_composed_object_signed_by_ssh_keygen_is_a_valid_git_tag(
    repo: Path, signer: Signer
) -> None:
    """The payload plus ssh-keygen's armored signature is what git mktag accepts."""
    payload = compose_seal(repo, _request(signer.signers))
    assert isinstance(payload, bytes)
    unsigned = repo.parent / "payload"
    unsigned.write_bytes(payload)
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-q", "-n", "git", "-f", str(signer.key), str(unsigned)],
        check=True,
        capture_output=True,
    )
    signed = payload + unsigned.with_name("payload.sig").read_bytes()
    oid = (
        subprocess.run(["git", "mktag"], cwd=repo, input=signed, capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )
    git(repo, "update-ref", f"refs/tags/{SEAL}", oid)

    assert verify_seal(repo, _head(repo), signer.signers, SEAL).signer == PRINCIPAL


def test_compose_refuses_a_malformed_tag_name(no_key: Path) -> None:
    with pytest.raises(ValidationError):
        _request(no_key, "v1.0")


def test_compose_refuses_a_head_without_an_attestation_note(repo: Path, no_key: Path) -> None:
    commit(repo, "unattested")

    with pytest.raises(SealCompositionError, match="has no note"):
        compose_seal(repo, _request(no_key))


def test_compose_refuses_a_note_naming_another_commit(repo: Path, no_key: Path) -> None:
    other = _head(repo)
    commit(repo, "second")
    raw_note(repo, "HEAD", [{"corpus_commit": other}])

    with pytest.raises(SealCompositionError, match="no entry naming it"):
        compose_seal(repo, _request(no_key))


def test_compose_refuses_a_note_without_a_final_newline(repo: Path, no_key: Path) -> None:
    commit(repo, "second")
    body = f'[{{"corpus_commit": "{_head(repo)}"}}]'
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            input=body.encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    git(repo, "notes", "--ref=refs/notes/temporal-attestations", "add", "-C", blob)

    with pytest.raises(SealCompositionError, match="final newline"):
        compose_seal(repo, _request(no_key))


def test_compose_refuses_an_unparseable_note(repo: Path, no_key: Path) -> None:
    commit(repo, "second")
    git(repo, "notes", "--ref=refs/notes/temporal-attestations", "add", "-m", "not json")

    with pytest.raises(SealCompositionError):
        compose_seal(repo, _request(no_key))


def test_compose_refuses_a_path_that_is_not_a_repository(tmp_path: Path, no_key: Path) -> None:
    with pytest.raises(SealCompositionError, match="not a commit"):
        compose_seal(tmp_path, _request(no_key))


def test_compose_reports_git_that_cannot_run(tmp_path: Path, no_key: Path) -> None:
    with pytest.raises(PublicationSealError, match="could not run"):
        compose_seal(tmp_path / "missing", _request(no_key))


@requires_ssh_keygen
def test_any_verifying_seal_makes_head_already_sealed_whatever_its_note(
    repo: Path, signer: Signer
) -> None:
    """A note changed (or forged) after sealing never earns HEAD a second seal."""
    _seal_head(repo, signer)
    attest(repo, _head(repo), parser_version=4)

    assert compose_seal(repo, _request(signer.signers)) == AlreadySealed(tag=f"refs/tags/{SEAL}")


@requires_ssh_keygen
def test_an_unverifiable_seal_does_not_count(repo: Path, signer: Signer, no_key: Path) -> None:
    tag(repo, "sync/forged", _message(repo), "HEAD", None)
    stranger = new_signer(repo.parent / "stranger", "stranger")
    tag(repo, "sync/stranger", _message(repo), "HEAD", stranger.key)

    assert isinstance(compose_seal(repo, _request(signer.signers)), bytes)
    _seal_head(repo, signer)
    assert isinstance(compose_seal(repo, _request(no_key)), bytes), "no key trusts no seal"


@requires_ssh_keygen
def test_a_seal_on_another_commit_does_not_seal_head(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    commit(repo, "second")
    attest(repo, _head(repo))

    assert isinstance(compose_seal(repo, _request(signer.signers)), bytes)


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
    tag(repo, SEAL, _message(repo), "HEAD", None)

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_rejects_a_key_not_in_the_signers_file(repo: Path, signer: Signer) -> None:
    stranger = new_signer(repo.parent / "stranger", "stranger")
    tag(repo, SEAL, _message(repo), "HEAD", stranger.key)

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
    message = _message(repo)
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
def test_verify_rejects_a_signed_tag_with_a_tampered_embedded_attestation(
    repo: Path, signer: Signer
) -> None:
    _seal_head(repo, signer)
    raw = subprocess.run(
        ["git", "cat-file", "tag", SEAL], cwd=repo, capture_output=True, check=True
    ).stdout
    tampered = raw.replace(b"parser_version", b"parser_versiom", 1)
    assert tampered != raw
    oid = (
        subprocess.run(
            ["git", "hash-object", "-t", "tag", "-w", "--stdin"],
            cwd=repo,
            input=tampered,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    git(repo, "update-ref", f"refs/tags/{SEAL}", oid)

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_verify_by_commit_tries_every_seal_and_reports_each_failure(
    repo: Path, signer: Signer
) -> None:
    message = _message(repo)
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
def test_a_signers_file_with_only_comments_holds_no_key(
    repo: Path, signer: Signer, tmp_path: Path
) -> None:
    _seal_head(repo, signer)
    # The shipped file's comment header alone (its key lines dropped) is not a key.
    shipped = Path(__file__).resolve().parents[2] / "ops" / "publication-signers"
    lines = shipped.read_text(encoding="utf-8").splitlines(keepends=True)
    comments = tmp_path / "comments-only"
    comments.write_text(
        "".join(line for line in lines if not line.strip() or line.lstrip().startswith("#")),
        encoding="utf-8",
    )

    with pytest.raises(SealVerificationError, match="holds no key"):
        verify_seal(repo, _head(repo), comments, SEAL)


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


# ------------------------------------------------------------------ verification details


def _dummy_signers(tmp_path: Path) -> Path:
    signers = tmp_path / "signers"
    signers.write_text('p namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")
    return signers


def _literal_tag(repo: Path, body: bytes, name: str = SEAL) -> None:
    """A tag object written as given, bypassing git's own checks, under ``refs/tags/name``."""
    oid = subprocess.run(
        ["git", "hash-object", "-t", "tag", "--literally", "-w", "--stdin"],
        cwd=repo,
        input=body,
        capture_output=True,
        check=True,
    ).stdout.decode()
    git(repo, "update-ref", f"refs/tags/{name}", oid.strip())


def _fake_ssh_keygen(monkeypatch: pytest.MonkeyPatch, directory: Path, script: str) -> None:
    directory.mkdir()
    program = directory / "ssh-keygen"
    program.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    program.chmod(0o700)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


def test_a_missing_tag_is_reported_without_gits_noise(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(SealVerificationError, match=r"exited 1: $"):
        verify_seal(repo, _head(repo), _dummy_signers(tmp_path), SEAL)


def test_a_seal_tag_naming_a_missing_object_is_refused(repo: Path, tmp_path: Path) -> None:
    (repo / ".git" / "refs" / "tags" / "sync").mkdir(parents=True)
    (repo / ".git" / "refs" / "tags" / SEAL).write_text("f" * 40 + "\n", encoding="utf-8")

    with pytest.raises(SealVerificationError, match="cat-file -t"):
        verify_seal(repo, _head(repo), _dummy_signers(tmp_path), SEAL)
    with pytest.raises(PublicationSealError, match="for-each-ref"):
        verify_seal(repo, _head(repo), _dummy_signers(tmp_path))


@requires_ssh_keygen
def test_a_corrupt_seal_tag_object_is_reported(repo: Path, signer: Signer) -> None:
    _seal_head(repo, signer)
    oid = git(repo, "rev-parse", f"refs/tags/{SEAL}")
    loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
    data = loose.read_bytes()
    loose.chmod(0o600)
    loose.write_bytes(data[:-8] + b"\x00" * 8)

    with pytest.raises(PublicationSealError, match="cat-file tag"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_a_message_failure_names_the_tag(repo: Path, signer: Signer) -> None:
    tag(repo, SEAL, b"release notes\n\nnothing here\n", "HEAD", signer.key)

    with pytest.raises(SealVerificationError) as refused:
        verify_seal(repo, _head(repo), signer.signers, SEAL)
    assert str(refused.value) == (
        f"refs/tags/{SEAL}: message is not a 'lovspor-publication-seal v1' seal"
    )


@requires_ssh_keygen
def test_each_tags_failure_is_listed_separated_by_a_semicolon(repo: Path, signer: Signer) -> None:
    tag(repo, "sync/a-unsigned", _message(repo), "HEAD", None)
    tag(repo, "sync/b-unsigned", _message(repo), "HEAD", None)

    with pytest.raises(SealVerificationError) as refused:
        verify_seal(repo, _head(repo), signer.signers)
    failures = str(refused.value).split("; ")
    assert [failure.split(":")[0] for failure in failures] == [
        "refs/tags/sync/a-unsigned",
        "refs/tags/sync/b-unsigned",
    ]


def test_an_indented_comment_in_the_signers_file_is_not_a_key(repo: Path, tmp_path: Path) -> None:
    signers = tmp_path / "indented"
    signers.write_text("   # the key is not committed yet\n", encoding="utf-8")

    with pytest.raises(SealVerificationError, match="holds no key"):
        verify_seal(repo, _head(repo), signers)


@requires_ssh_keygen
def test_a_tag_header_that_is_not_utf8_is_still_judged(repo: Path, signer: Signer) -> None:
    head = _head(repo)
    _literal_tag(
        repo,
        f"object {head}\ntype commit\ntag {SEAL}\n".encode()
        + b"tagger \xff <x@example.invalid> 0 +0000\n\n"
        + _message(repo),
    )

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, head, signer.signers, SEAL)


@requires_ssh_keygen
def test_the_last_object_header_decides_what_a_tag_tags(repo: Path, signer: Signer) -> None:
    head = _head(repo)
    _literal_tag(
        repo,
        f"object {head}\ntype commit\ntag {SEAL}\n".encode()
        + b"tagger t <t@example.invalid> 0 +0000\n"
        + f"object {'f' * 40} trailing\n\n".encode()
        + _message(repo),
    )

    with pytest.raises(SealVerificationError, match="does not tag commit"):
        verify_seal(repo, head, signer.signers, SEAL)


@requires_ssh_keygen
def test_a_seal_header_line_that_is_not_utf8_does_not_void_the_seal(
    repo: Path, signer: Signer
) -> None:
    message = _message(repo).replace(
        b"attestation_ref refs/notes/temporal-attestations", b"attestation_ref \xff", 1
    )
    assert b"\xff" in message
    tag(repo, SEAL, message, "HEAD", signer.key)

    assert verify_seal(repo, _head(repo), signer.signers, SEAL).signer == PRINCIPAL


@requires_ssh_keygen
def test_the_last_corpus_commit_line_decides_whole(repo: Path, signer: Signer) -> None:
    header, _, note = _message(repo).partition(b"\n\n")
    forged = header + f"\ncorpus_commit {'f' * 40} trailing".encode() + b"\n\n" + note
    tag(repo, SEAL, forged, "HEAD", signer.key)

    with pytest.raises(SealVerificationError, match="corpus_commit does not name"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_an_embedded_note_may_hold_a_blank_line(repo: Path, signer: Signer) -> None:
    head = _head(repo)
    note = f'[\n\n{{"corpus_commit": "{head}"}}]\n'.encode()
    tag(repo, SEAL, seal_message(head, "0" * 40, note), "HEAD", signer.key)

    assert verify_seal(repo, head, signer.signers, SEAL).signer == PRINCIPAL


@requires_ssh_keygen
def test_a_signature_check_that_fails_silently_is_reported_by_its_exit_code(
    repo: Path, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seal_head(repo, signer)
    _fake_ssh_keygen(monkeypatch, repo.parent / "fake-bin", "exit 1")

    with pytest.raises(SealVerificationError) as refused:
        verify_seal(repo, _head(repo), signer.signers, SEAL)
    assert str(refused.value) == (
        f"refs/tags/{SEAL}: signature not accepted by {signer.signers.resolve()}: exit 1"
    )


@requires_ssh_keygen
def test_a_good_signature_line_from_a_failing_check_is_not_accepted(
    repo: Path, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seal_head(repo, signer)
    good = f'Good "git" signature for {PRINCIPAL} with ED25519 key SHA256:x'
    _fake_ssh_keygen(monkeypatch, repo.parent / "fake-bin", f"echo '{good}' >&2\nexit 1")

    with pytest.raises(SealVerificationError, match="signature not accepted"):
        verify_seal(repo, _head(repo), signer.signers, SEAL)


@requires_ssh_keygen
def test_a_refusal_reason_is_the_checks_words_on_one_line(
    repo: Path, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seal_head(repo, signer)
    _fake_ssh_keygen(
        monkeypatch, repo.parent / "fake-bin", "printf 'bad\\377  key\\n  refused\\n' >&2\nexit 1"
    )

    with pytest.raises(SealVerificationError) as refused:
        verify_seal(repo, _head(repo), signer.signers, SEAL)
    assert "bad� key refused" in str(refused.value)


@requires_ssh_keygen
def test_verification_uses_ssh_keygen_whatever_the_global_configuration(
    repo: Path, signer: Signer, tmp_path: Path
) -> None:
    _seal_head(repo, signer)
    (tmp_path / "gitconfig").write_text(
        '[gpg "ssh"]\n\tprogram = /usr/bin/false\n', encoding="utf-8"
    )

    assert verify_seal(repo, _head(repo), signer.signers, SEAL).signer == PRINCIPAL


@requires_ssh_keygen
def test_a_seal_tag_name_may_hold_non_ascii_whitespace(repo: Path, signer: Signer) -> None:
    no_break_space = chr(0xA0)
    name = f"sync/seal-1-1{no_break_space}retry"
    _seal_head(repo, signer, name)

    assert verify_seal(repo, _head(repo), signer.signers).tag == f"refs/tags/{name}"
