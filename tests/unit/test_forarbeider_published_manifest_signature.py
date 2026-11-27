"""AR6b: the manifest's bot signature is published with the commit (#738; §9.3, §9.3a).

The genuine AR6a SSHSIG travels in the published tree as
``releases/<release_manifest_sha256>.sig``, beside the manifest it signs, so a
reader, an auditor or a later recovery verifies the manifest without the
runner's private clone. Paths A and B, real ``ssh-keygen``.
"""

import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_execute,
    publication_path_b,
    publication_stage,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import RELEASE_NAMESPACE
from lovspor.forarbeider.publication_execute import execute, publish_authorized
from lovspor.forarbeider.publication_manifest_signature import held_signature, signature_path
from lovspor.forarbeider.publication_remote_recovery import recover
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_execution_support import NOW, Rig, make_rig
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters, authorized

CHECK = "ar6_release_manifest_signature"


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "rig", request.param)


def companion(rig: Rig) -> str:
    return f"releases/{rig.transaction.content.release_manifest_sha256}.sig"


def published_files(rig: Rig, adapters: Adapters, commit: str) -> dict[str, bytes]:
    if rig.path == "A":
        found = adapters.github.commit(commit)
        assert found is not None
        return dict(found.changes)
    tree = publication_path_b.remote_tree(rig.bare, commit)
    return {
        path: publication_path_b.remote_object(rig.bare, "blob", blob)
        for path, blob in tree.items()
    }


def test_the_commit_publishes_the_genuine_manifest_signature(rig: Rig) -> None:
    adapters = Adapters(rig)
    held = held_signature(rig.clone, rig.transaction.content.release_manifest_sha256)
    assert held

    outcome = execute(rig.request(), adapters.at(NOW))

    assert outcome.status == "published" and outcome.commit is not None
    files = published_files(rig, adapters, outcome.commit)
    assert files[companion(rig)] == held
    assert outcome.evidence is not None
    assert sha256(files[companion(rig)]).hexdigest() == (
        outcome.evidence.release_manifest_signature_sha256
    )


def test_recovery_verifies_the_publication_after_the_clone_is_gone(rig: Rig) -> None:
    """The runner's private clone holds no evidence recovery needs any more."""
    adapters = Adapters(rig)
    assert execute(rig.request(), adapters.at(NOW)).status == "published"
    shutil.rmtree(rig.clone)

    outcome = recover(rig.request(), adapters.at(NOW))

    assert outcome.receipt is not None
    (found,) = [c for c in outcome.receipt.integrity_checks if c.name == CHECK]
    assert (found.status, found.reasons) == ("passed", ())
    assert outcome.receipt.integrity == "integrity_verified"
    assert outcome.classification == "published_authorized"
    assert not publication_attempts.latched(rig.workspace)


def test_a_later_base_may_hold_earlier_published_signatures(rig: Rig) -> None:
    """A later batch prepares on a base whose ``releases/`` holds earlier companions."""
    earlier = rig.clone / "releases" / f"{'e' * 64}.sig"
    earlier.write_bytes(b"an earlier batch's signature")
    journal = publication_stage.pending_journal(rig.clone)

    base = publication_stage.base_files(rig.clone, journal)

    assert f"releases/{'e' * 64}.sig" not in base
    (rig.clone / "releases" / "not-a-digest.sig").write_bytes(b"x")
    with pytest.raises(ForarbeiderRefusedError) as raised:
        publication_stage.base_files(rig.clone, journal)
    assert raised.value.detail == "base_tree"


def test_the_bytes_sent_are_exactly_the_signature_the_evidence_sealed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last guard, isolated: the record read for the send must be the sealed one."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    monkeypatch.setattr(publication_execute, "_still_current", lambda *_: None)
    target = rig.clone / signature_path(rig.transaction.content.release_manifest_sha256)
    target.unlink()
    manifest = publication_stage.read_file(rig.clone, rig.transaction.content.release_manifest_path)
    target.write_bytes(sign(rig.stranger, manifest, RELEASE_NAMESPACE))
    target.chmod(0o600)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publish_authorized(evidence, rig.request(), adapters.at(NOW))

    assert raised.value.detail == "authorization_stale:manifest_signature:not_the_evidences"
    assert not adapters.sent()
