"""``scripts/seal/sign-and-tag.sh`` end to end, against a local bare origin.

The script is the only code that ever holds the seal key. It must re-derive
the composed tag from origin with git alone and refuse anything that does not
match byte for byte. Throwaway keys only, created in the test's tmp dir.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from lovspor.publication_seal import SealRequest, compose_seal, verify_seal
from tests.unit.publication_seal_support import (
    PRINCIPAL,
    Signer,
    attest,
    commit,
    git,
    hermetic_git,
    new_repo,
    new_signer,
    raw_note,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seal" / "sign-and-tag.sh"
_NOTES = "refs/notes/temporal-attestations"
RUN_ID = "77"
SEAL = f"sync/seal-{RUN_ID}-1"

pytestmark = pytest.mark.skipif(
    not Path("/usr/bin/ssh-keygen").exists(), reason="the script signs with /usr/bin/ssh-keygen"
)


@dataclass(frozen=True)
class Setup:
    work: Path
    origin: Path
    payload: Path
    head: str
    signer: Signer
    tmpdir: Path


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    hermetic_git(monkeypatch, tmp_path)
    work = new_repo(tmp_path / "work")
    head = git(work, "rev-parse", "HEAD")
    attest(work, head)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main", _NOTES)
    signer = new_signer(tmp_path / "keys")
    request = SealRequest(tag_name=SEAL, tagged_at=int(time.time()), allowed_signers=signer.signers)
    payload = tmp_path / "payload"
    composed = compose_seal(work, request)
    assert isinstance(composed, bytes)
    payload.write_bytes(composed)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    return Setup(work, origin, payload, head, signer, tmpdir)


def _run(setup: Setup, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "TMPDIR": str(setup.tmpdir),
        "SEAL_PAYLOAD": str(setup.payload),
        "SEAL_HEAD": setup.head,
        "SEAL_TAG": SEAL,
        "SEAL_SIGNERS": str(setup.signer.signers),
        "SEAL_REMOTE": str(setup.origin),
        "SEAL_SIGNING_KEY": setup.signer.key.read_text(encoding="utf-8"),
        "SEAL_REQUIRED": "true",
        "GITHUB_RUN_ID": RUN_ID,
        **overrides,
    }
    return subprocess.run(
        ["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def _origin_has_seal(setup: Setup) -> bool:
    probe = ["git", "rev-parse", "--quiet", "--verify", f"refs/tags/{SEAL}"]
    return subprocess.run(probe, cwd=setup.origin, capture_output=True, check=False).returncode == 0


def test_the_script_seals_and_pushes_a_tag_git_and_lovspor_accept(setup: Setup) -> None:
    result = _run(setup)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _origin_has_seal(setup)
    git(
        setup.origin, "-c", f"gpg.ssh.allowedSignersFile={setup.signer.signers}", "verify-tag", SEAL
    )
    verdict = verify_seal(setup.origin, setup.head, setup.signer.signers, SEAL)
    assert verdict.signer == PRINCIPAL
    assert list(setup.tmpdir.iterdir()) == [], "the scratch dir with the key must be removed"


def test_the_signed_tag_carries_exactly_the_composed_bytes(setup: Setup) -> None:
    assert _run(setup).returncode == 0
    raw = subprocess.run(
        ["git", "cat-file", "tag", SEAL], cwd=setup.origin, capture_output=True, check=True
    ).stdout

    assert raw.startswith(setup.payload.read_bytes() + b"-----BEGIN SSH SIGNATURE-----\n")


def test_origin_moved_after_the_sync_is_refused(setup: Setup) -> None:
    commit(setup.work, "later")
    git(setup.work, "push", "-q", "origin", "main")

    result = _run(setup)

    assert result.returncode == 1
    assert "origin main is" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_payload_for_another_object_is_refused(setup: Setup) -> None:
    newer = commit(setup.work, "later")
    git(setup.work, "push", "-q", "origin", "main")

    result = _run(setup, SEAL_HEAD=newer)

    assert result.returncode == 1
    assert "payload object is not" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_note_that_differs_on_origin_is_refused(setup: Setup) -> None:
    raw_note(setup.work, "HEAD", [{"corpus_commit": setup.head, "forged": True}])
    git(setup.work, "push", "-q", "-f", "origin", _NOTES)

    result = _run(setup)

    assert result.returncode == 1
    assert "differs from the tag re-derived from origin" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_tampered_payload_message_is_refused(setup: Setup) -> None:
    setup.payload.write_bytes(
        setup.payload.read_bytes().replace(b"parser_version", b"parser_versiom")
    )

    result = _run(setup)

    assert result.returncode == 1
    assert "differs" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_malformed_tagger_line_is_refused(setup: Setup) -> None:
    payload = setup.payload.read_bytes().replace(b"lovspor-sync[bot]", b"someone-else", 1)
    setup.payload.write_bytes(payload)

    result = _run(setup)

    assert result.returncode == 1
    assert "tagger line is malformed" in result.stdout


def test_a_tag_of_another_run_is_refused(setup: Setup) -> None:
    result = _run(setup, GITHUB_RUN_ID="78")

    assert result.returncode == 1
    assert "does not belong to run 78" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_signers_file_without_a_key_is_refused(setup: Setup, tmp_path: Path) -> None:
    empty = tmp_path / "empty-signers"
    empty.write_text("# no key\n", encoding="utf-8")

    result = _run(setup, SEAL_SIGNERS=str(empty))

    assert result.returncode == 1
    assert "holds no key" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_key_not_in_the_signers_file_fails_verification(setup: Setup, tmp_path: Path) -> None:
    stranger = new_signer(tmp_path / "stranger", "stranger")

    result = _run(setup, SEAL_SIGNING_KEY=stranger.key.read_text(encoding="utf-8"))

    assert result.returncode != 0
    assert not _origin_has_seal(setup)
    assert list(setup.tmpdir.iterdir()) == []


def test_an_absent_key_fails_when_a_seal_is_required(setup: Setup) -> None:
    result = _run(setup, SEAL_SIGNING_KEY="")

    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert not _origin_has_seal(setup)


def test_an_absent_key_only_warns_when_a_seal_is_not_required(setup: Setup) -> None:
    result = _run(setup, SEAL_SIGNING_KEY="", SEAL_REQUIRED="")

    assert result.returncode == 0
    assert "::warning::" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_tag_name_with_a_suffix_after_the_attempt_is_refused(setup: Setup) -> None:
    result = _run(setup, SEAL_TAG=f"{SEAL}x")

    assert result.returncode == 1
    assert "does not belong to run" in result.stdout
    assert not _origin_has_seal(setup)


def test_a_tagger_time_outside_the_window_is_refused(setup: Setup) -> None:
    # The tagger date judges valid-after/valid-before, so it cannot be chosen.
    old = SealRequest(tag_name=SEAL, tagged_at=1000000000, allowed_signers=setup.signer.signers)
    composed = compose_seal(setup.work, old)
    assert isinstance(composed, bytes)
    setup.payload.write_bytes(composed)

    result = _run(setup)

    assert result.returncode == 1
    assert "tagger time" in result.stdout
    assert not _origin_has_seal(setup)
