"""AR6a: the one manifest-signature verifier, binding by binding (#738; §9.3 step 4).

Each case signs, with the genuine bot key in the release namespace, a manifest
that differs from the transaction's in exactly one binding, and offers a
transaction that names exactly those bytes by digest. Only the binding can then
refuse it. Real ``ssh-keygen`` signatures throughout; paths A and B.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_manifest_signature
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import RELEASE_NAMESPACE, SignedBytes
from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_manifest_signature import (
    SIGNATURE,
    ManifestClaim,
    held_signature,
    manifest_bytes,
    sign_release_manifest,
    verify_manifest_signature,
    verify_proposed,
)
from lovspor.forarbeider.publication_models import (
    PathA,
    PathB,
    PublicationTransaction,
    ReleaseManifestFile,
)
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_execution_support import NOW, KeySigner, Rig, make_rig
from tests.unit.forarbeider_release_control_support import BOT
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


def claim_of(rig: Rig, transaction: PublicationTransaction | None = None, **changes: Any) -> Any:
    plan = rig.inputs.plan.plan
    assert plan is not None
    fields: dict[str, Any] = {
        "transaction": transaction or rig.transaction,
        "policy": plan.basis.policy,
        "repository": rig.inputs.settings.repository,
        "principal": BOT,
    }
    return ManifestClaim(**(fields | changes))


def manifest_of(rig: Rig) -> ReleaseManifestFile:
    return ReleaseManifestFile.model_validate_json(manifest_bytes(rig.clone, rig.transaction))


def naming(rig: Rig, data: bytes) -> PublicationTransaction:
    """The rig's transaction, made to name ``data`` as its manifest by digest (nothing else)."""
    from hashlib import sha256  # noqa: PLC0415

    digest = sha256(data).hexdigest()
    content = rig.transaction.content.model_copy(
        update={
            "release_manifest_sha256": digest,
            "release_manifest_path": f"releases/{digest}.json",
        }
    )
    return PublicationTransaction.model_construct(transaction_id="0" * 64, content=content)


def verdict(rig: Rig, data: bytes, transaction: PublicationTransaction | None = None) -> Any:
    signed = SignedBytes(data, sign(rig.bot, data, RELEASE_NAMESPACE))
    return verify_manifest_signature(claim_of(rig, transaction), signed, rig.root, NOW)


def other_base(rig: Rig) -> PathA | PathB:
    publication = rig.transaction.content.publication
    return publication.model_copy(
        update={"expected_head_oid" if rig.path == "A" else "base_commit": "f" * 40}
    )


def other_path(rig: Rig) -> PathA | PathB:
    base = rig.transaction.content.publication.base_commit
    return PathB(base_commit=base) if rig.path == "A" else PathA(expected_head_oid=base)


def changed_file(rig: Rig) -> Any:
    files = manifest_of(rig).files
    first = files[0].model_copy(update={"sha256": "e" * 64})
    return (first, *files[1:])


BINDINGS = {
    "policy": (
        lambda r: {"policy": manifest_of(r).policy.model_copy(update={"policy_version": 9})},
        "manifest_policy_mismatch",
    ),
    "batch": (lambda r: {"batch_sequence": 2}, "manifest_batch_mismatch"),
    "plan": (lambda r: {"plan_id": "d" * 64}, "manifest_plan_mismatch"),
    "base": (lambda r: {"publication": other_base(r)}, "manifest_publication_mismatch"),
    "path": (lambda r: {"publication": other_path(r)}, "manifest_publication_mismatch"),
    "files": (lambda r: {"files": changed_file(r)}, "manifest_files_mismatch"),
    "quarantine": (lambda r: {"quarantine_sha256": "c" * 64}, "manifest_quarantine_mismatch"),
}


@pytest.mark.parametrize("binding", sorted(BINDINGS))
def test_a_genuine_signature_on_another_binding_is_refused(rig: Rig, binding: str) -> None:
    change, reason = BINDINGS[binding]
    data = canonical_text(manifest_of(rig).model_copy(update=change(rig))).encode()

    found = verdict(rig, data, naming(rig, data))

    assert found.reasons == (reason,)


def test_the_genuine_manifest_verifies(rig: Rig) -> None:
    found = verdict(rig, manifest_bytes(rig.clone, rig.transaction))

    assert found.verified
    assert found.fingerprint is not None and found.signature_sha256 is not None


def test_a_manifest_for_another_repository_is_refused(rig: Rig) -> None:
    raw = json.loads(manifest_bytes(rig.clone, rig.transaction))
    raw["repository"] = "someone/else"
    data = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()

    assert verdict(rig, data, naming(rig, data)).reasons == ("manifest_repository_mismatch",)


def test_bytes_that_are_not_canonical_are_refused(rig: Rig) -> None:
    data = json.dumps(json.loads(manifest_bytes(rig.clone, rig.transaction)), indent=2).encode()

    assert verdict(rig, data, naming(rig, data)).reasons == ("manifest_not_canonical",)


def test_a_genuine_signature_on_other_bytes_is_not_the_transactions(rig: Rig) -> None:
    data = canonical_text(manifest_of(rig).model_copy(update={"batch_sequence": 2})).encode()

    found = verdict(rig, data)

    assert "manifest_digest_not_the_transactions" in found.reasons


def test_another_configured_principal_is_not_the_bot(rig: Rig) -> None:
    data = manifest_bytes(rig.clone, rig.transaction)
    signed = SignedBytes(data, sign(rig.bot, data, RELEASE_NAMESPACE))

    found = verify_manifest_signature(claim_of(rig, principal="another-bot"), signed, rig.root, NOW)

    assert found.reasons == ("manifest_signature:not_the_bot",)


def test_no_plan_policy_is_refused(rig: Rig) -> None:
    data = manifest_bytes(rig.clone, rig.transaction)
    signed = SignedBytes(data, sign(rig.bot, data, RELEASE_NAMESPACE))

    found = verify_manifest_signature(claim_of(rig, policy=None), signed, rig.root, NOW)

    assert found.reasons == ("manifest_policy_mismatch",)


def test_an_empty_signature_is_missing(rig: Rig) -> None:
    data = manifest_bytes(rig.clone, rig.transaction)

    found = verify_manifest_signature(claim_of(rig), SignedBytes(data, b""), rig.root, NOW)

    assert found.reasons == ("manifest_signature_missing",)


# --- signing and holding --------------------------------------------------------------------


def test_the_held_signature_is_private_and_over_the_exact_bytes(rig: Rig) -> None:
    path = rig.clone / SIGNATURE
    assert path.stat().st_mode & 0o777 == 0o600
    held = held_signature(rig.clone)
    assert held is not None
    data = manifest_bytes(rig.clone, rig.transaction)
    assert verify_manifest_signature(claim_of(rig), SignedBytes(data, held), rig.root, NOW).verified


def test_only_a_prepared_proposal_of_this_transaction_is_signed(rig: Rig) -> None:
    other = rig.transaction.model_copy(update={"transaction_id": "1" * 64})

    with pytest.raises(ForarbeiderRefusedError) as raised:
        sign_release_manifest(rig.clone, other, KeySigner(BOT, rig.bot))

    assert raised.value.detail == "manifest_not_prepared"


def test_a_crash_while_signing_leaves_no_signature_and_a_retry_signs(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    (rig.clone / SIGNATURE).unlink()

    def crash(git: int) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(publication_manifest_signature, "renamed", crash)
    with pytest.raises(SimulatedCrash):
        sign_release_manifest(rig.clone, rig.transaction, KeySigner(BOT, rig.bot))
    monkeypatch.undo()

    assert held_signature(rig.clone) is None
    assert execute(rig.request(), Adapters(rig).at(NOW)).status == "denied"
    sign_release_manifest(rig.clone, rig.transaction, KeySigner(BOT, rig.bot))
    assert execute(rig.request(), Adapters(rig).at(NOW)).status == "published"


def test_a_linked_signature_is_unreadable_never_followed(rig: Rig) -> None:
    path = rig.clone / SIGNATURE
    target = rig.tmp / "elsewhere.sig"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)

    found = verify_proposed(rig.clone, claim_of(rig), rig.root, NOW)

    assert found.reasons == ("manifest_signature_unreadable",)


def test_a_changed_proposed_manifest_is_not_the_proposal(rig: Rig) -> None:
    manifest = rig.clone / rig.transaction.content.release_manifest_path
    manifest.write_bytes(manifest.read_bytes() + b" ")

    found = verify_proposed(rig.clone, claim_of(rig), rig.root, NOW)

    assert found.reasons == ("manifest_bytes_not_the_proposal",)
