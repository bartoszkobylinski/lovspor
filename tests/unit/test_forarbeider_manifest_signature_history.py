"""AR6a: a publication's manifest signature stays its own after another is signed (#738; §9.3a).

Recovery re-derives every receipt from verified facts and compares it with the
one on record. The manifest signature is one of those facts, so the evidence
for an earlier publication must survive any later signing. Nothing makes a
clone one transaction's for good: it is chosen by batch and path
(:func:`~lovspor.forarbeider.publication_prepare.clone_path`), a published
proposal's journal is rolled back by preparation recovery without an expected
transaction, and :class:`~lovspor.forarbeider.publication_execute.ExecutionRequest`
names its clone as a plain path. These cases put a second transaction's
proposal in the first one's clone through the real staging, sign it with the
real signer, and recover the first again. Paths A and B, real ``ssh-keygen``.
"""

from pathlib import Path

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_manifest_signature,
    publication_receipts,
    publication_stage,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import load_trust_root
from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_manifest_signature import (
    ManifestClaim,
    sign_release_manifest,
    signature_path,
    verify_proposed,
)
from lovspor.forarbeider.publication_prepare import recover_preparation
from lovspor.forarbeider.publication_proposal import stage_payloads
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.release_lock import release_lock
from tests.unit.forarbeider_execution_support import NOW, KeySigner, Rig, make_rig
from tests.unit.forarbeider_publication_support import git
from tests.unit.forarbeider_release_control_support import BOT
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters

CHECK = "ar6_release_manifest_signature"


@pytest.fixture(params=["A", "B"])
def pair(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Rig, Rig]:
    first = make_rig(tmp_path / "first", request.param)
    return first, make_rig(tmp_path / "second", request.param, share=first)


def published_and_recovered(rig: Rig) -> Adapters:
    adapters = Adapters(rig)
    assert execute(rig.request(), adapters.at(NOW)).status == "published"
    outcome = recover(rig.request(), adapters.at(NOW))
    assert outcome.receipt is not None
    assert outcome.receipt.integrity == "integrity_verified"
    assert outcome.receipt.published
    assert not publication_attempts.latched(rig.workspace)
    return adapters


def second_signed_in(clone: Path, second: Rig) -> None:
    """The second transaction prepared and signed in ``clone``, through the real staging."""
    assert recover_preparation(clone, None).status == "rolled_back"
    git(clone, "read-tree", second.base)  # path B's commit build left the index staged
    transaction = second.transaction
    payloads = {
        item.path: publication_stage.read_file(second.clone, item.path)
        for item in transaction.content.files
    }
    with release_lock(clone):
        base = publication_stage.base_files(clone, None)
        journal = publication_stage.begin(clone, transaction, dict(stage_payloads(payloads, base)))
        journal = publication_stage.write_all(clone, journal)
        publication_stage.read_back(clone, journal)
    sign_release_manifest(clone, transaction, KeySigner(BOT, second.bot))


def receipts_of(rig: Rig) -> list[object]:
    tid = rig.transaction.transaction_id
    return [r for r in publication_receipts.receipts(rig.workspace) if r.transaction_id == tid]


def test_both_transactions_share_one_base_and_differ(pair: tuple[Rig, Rig]) -> None:
    first, second = pair

    assert first.base == second.base
    assert first.transaction.transaction_id != second.transaction.transaction_id
    assert (
        first.transaction.content.release_manifest_sha256
        != second.transaction.content.release_manifest_sha256
    )


def test_signing_another_manifest_in_the_clone_leaves_a_receipt_verified(
    pair: tuple[Rig, Rig],
) -> None:
    first, second = pair
    adapters = published_and_recovered(first)
    before = receipts_of(first)

    second_signed_in(first.clone, second)
    outcome = recover(first.request(), adapters.at(NOW))

    assert outcome.classification == "already_receipted"
    assert outcome.receipt is not None
    assert outcome.receipt.integrity == "integrity_verified"
    assert receipts_of(first) == before
    assert not publication_attempts.latched(first.workspace)


def test_a_fresh_recovery_after_another_signing_still_verifies(pair: tuple[Rig, Rig]) -> None:
    """No receipt yet: the publication is first recovered after the clone moved on."""
    first, second = pair
    adapters = Adapters(first)
    assert execute(first.request(), adapters.at(NOW)).status == "published"
    second_signed_in(first.clone, second)

    outcome = recover(first.request(), adapters.at(NOW))

    assert outcome.receipt is not None
    (found,) = [c for c in outcome.receipt.integrity_checks if c.name == CHECK]
    assert found.status == "passed"
    assert outcome.receipt.integrity == "integrity_verified"
    assert not publication_attempts.latched(first.workspace)


# --- one record per manifest ---------------------------------------------------------------------


def record(rig: Rig, clone: Path | None = None) -> Path:
    digest = rig.transaction.content.release_manifest_sha256
    return (clone or rig.clone) / signature_path(digest)


def test_transactions_sharing_a_clone_keep_one_record_each(pair: tuple[Rig, Rig]) -> None:
    first, second = pair
    published_and_recovered(first)
    mine = record(first).read_bytes()

    second_signed_in(first.clone, second)

    assert record(first).read_bytes() == mine
    assert record(second, first.clone).read_bytes() == record(second).read_bytes()
    assert sorted(p.name for p in record(first).parent.iterdir()) == sorted(
        f"{r.transaction.content.release_manifest_sha256}.sig" for r in pair
    )
    found = verify_proposed(first.clone, claim_of(second), first.root, NOW)
    assert found.verified, found.reasons


def claim_of(rig: Rig) -> ManifestClaim:
    plan = rig.inputs.plan.plan
    assert plan is not None
    return ManifestClaim(rig.transaction, plan.basis.policy, rig.inputs.settings.repository, BOT)


@pytest.mark.parametrize("change", ["removed", "corrupted", "the_others"])
def test_a_lost_historical_record_no_longer_matters_after_publication(
    pair: tuple[Rig, Rig], change: str
) -> None:
    """AR6b: the published signature is the evidence; the clone's record is only the signer's."""
    first, second = pair
    adapters = published_and_recovered(first)
    second_signed_in(first.clone, second)
    before = receipts_of(first)
    target = record(first)
    if change == "removed":
        target.unlink()
    else:
        payload = b"not a signature" if change == "corrupted" else record(second).read_bytes()
        target.unlink()
        target.write_bytes(payload)
        target.chmod(0o600)

    outcome = recover(first.request(), adapters.at(NOW))

    assert outcome.classification == "already_receipted"
    assert receipts_of(first) == before
    assert not publication_attempts.latched(first.workspace)


def test_a_fresh_recovery_without_its_own_record_verifies_the_published_one(
    pair: tuple[Rig, Rig],
) -> None:
    first, second = pair
    adapters = Adapters(first)
    assert execute(first.request(), adapters.at(NOW)).status == "published"
    second_signed_in(first.clone, second)
    record(first).unlink()

    outcome = recover(first.request(), adapters.at(NOW))

    receipt = outcome.receipt
    assert receipt is not None
    (found,) = [c for c in receipt.integrity_checks if c.name == CHECK]
    assert (found.status, found.reasons) == ("passed", ())
    assert receipt.classification == "published_authorized"
    assert not publication_attempts.latched(first.workspace)


def test_the_retired_shared_file_is_never_adopted(pair: tuple[Rig, Rig]) -> None:
    first, _ = pair
    legacy = first.clone / ".git/lovspor-release-manifest.sig"
    legacy.write_bytes(record(first).read_bytes())
    legacy.chmod(0o600)
    record(first).unlink()
    adapters = Adapters(first)

    outcome = execute(first.request(), adapters.at(NOW))

    assert outcome.status == "denied"
    assert outcome.evidence is not None
    (found,) = [c for c in outcome.evidence.checks if c.name == CHECK]
    assert (found.status, found.reasons) == ("failed", ("manifest_signature_missing",))
    assert not adapters.sent()


# --- signing: create-only, crash, concurrency, retry -----------------------------------------


def test_signing_the_same_manifest_again_keeps_the_record(pair: tuple[Rig, Rig]) -> None:
    first, _ = pair
    before = record(first).stat()

    again = sign_release_manifest(first.clone, first.transaction, KeySigner(BOT, first.bot))

    after = record(first).stat()
    assert again == record(first).read_bytes()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_another_signature_never_replaces_a_record(pair: tuple[Rig, Rig]) -> None:
    first, _ = pair
    mine = record(first).read_bytes()

    with pytest.raises(ForarbeiderRefusedError) as raised:
        sign_release_manifest(first.clone, first.transaction, KeySigner(BOT, first.stranger))

    assert raised.value.detail == "manifest_signature_exists"
    assert record(first).read_bytes() == mine


def test_a_crash_while_signing_another_leaves_every_record_whole(
    pair: tuple[Rig, Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = pair
    adapters = published_and_recovered(first)
    mine = record(first).read_bytes()

    def crash(records: int, name: str) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(publication_manifest_signature, "renamed", crash)
    with pytest.raises(SimulatedCrash):
        second_signed_in(first.clone, second)
    monkeypatch.undo()

    assert not record(second, first.clone).exists()
    assert record(first).read_bytes() == mine
    sign_release_manifest(first.clone, second.transaction, KeySigner(BOT, second.bot))
    assert record(second, first.clone).read_bytes() == record(second).read_bytes()
    assert recover(first.request(), adapters.at(NOW)).classification == "already_receipted"


def test_a_concurrent_holder_of_the_clone_refuses_the_signing(pair: tuple[Rig, Rig]) -> None:
    first, _ = pair
    mine = record(first).read_bytes()

    with release_lock(first.clone), pytest.raises(ForarbeiderRefusedError) as raised:
        sign_release_manifest(first.clone, first.transaction, KeySigner(BOT, first.stranger))

    assert raised.value.reason is RefusalReason.CORPUS_LOCKED
    assert record(first).read_bytes() == mine


def test_an_independent_restart_recovers_the_history(pair: tuple[Rig, Rig]) -> None:
    """A new process after the clone moved on: root and request rebuilt from disk."""
    first, second = pair
    adapters = published_and_recovered(first)
    second_signed_in(first.clone, second)
    root = load_trust_root(first.tmp / "root")
    request = first.request(
        root=root, clone=Path(str(first.clone)), anchoring=first.anchoring(root=root)
    )

    outcome = recover(request, adapters.at(NOW))

    assert outcome.classification == "already_receipted"
    assert not publication_attempts.latched(first.workspace)
