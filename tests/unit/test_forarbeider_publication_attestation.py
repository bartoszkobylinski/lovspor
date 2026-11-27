"""AR6b: the bot-signed publication attestation and the independent reader (#738; §9.3 A8, B11).

Every case publishes for real (path A through the in-memory endpoint, path B
to a disposable bare remote), recovers from the remote, and verifies with real
``ssh-keygen``; nothing is approved by a mock. The reader is given the remote,
the trust root and the settings only: the runner's clone and workspace are
deleted before it runs.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_attestation_store,
    publication_path_b,
    publication_receipts,
    publication_stage,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import PUBLICATION_NAMESPACE, RELEASE_NAMESPACE
from lovspor.forarbeider.publication_attestation import (
    Expected,
    PublicationProof,
    PublicSource,
    attest,
    attestation_of,
    public_source,
    read_attestation,
    verify_publication,
)
from lovspor.forarbeider.publication_attestation_models import (
    ATTESTATION_FILE,
    ATTESTATION_SIGNATURE_FILE,
    PublicationAttestation,
    attestation_ref,
)
from lovspor.forarbeider.publication_attestation_store import Stored
from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_models import release_signature_path
from lovspor.forarbeider.publication_path_a import body, headline
from lovspor.forarbeider.publication_remote_recovery import RecoveryOutcome, recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    KeySigner,
    Rig,
    _root,
    ledger_bytes,
    make_rig,
)
from tests.unit.forarbeider_publication_support import git
from tests.unit.forarbeider_release_control_support import BOT
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters

CHECK = "ar6_release_manifest_signature"


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "rig", request.param)


@pytest.fixture
def rig_a(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "rig", "A")


def bot(rig: Rig) -> KeySigner:
    return KeySigner(BOT, rig.bot)


def source(rig: Rig, adapters: Adapters, builder: bool = False) -> PublicSource:
    made = (rig.clone, bot(rig)) if builder else None
    return public_source(adapters.at(NOW), Expected(rig.root, rig.inputs.settings), made)


def read(rig: Rig, adapters: Adapters, commit: str, root: Any = None) -> PublicationProof:
    found = source(rig, adapters)
    return verify_publication(found, Expected(root or rig.root, rig.inputs.settings), commit, NOW)


def published(rig: Rig) -> tuple[Adapters, str, RecoveryOutcome]:
    adapters = Adapters(rig)
    sent = execute(rig.request(), adapters.at(NOW))
    assert sent.status == "published" and sent.commit is not None
    outcome = recover(rig.request(), adapters.at(NOW))
    assert outcome.classification == "published_authorized", outcome
    return adapters, sent.commit, outcome


def attested(rig: Rig) -> tuple[Adapters, str]:
    adapters, commit, _ = published(rig)
    assert attest(rig.request(), adapters.at(NOW), bot(rig)).status == "attested"
    return adapters, commit


def refused(action: Callable[[], Any]) -> str:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value.detail


def tag_count(rig: Rig, adapters: Adapters) -> int:
    if rig.path == "A":
        return len(adapters.github.refs)
    return len(git(rig.bare, "tag", "--list").split())


# --- valid publication, independent reader -------------------------------------------------


def test_an_attested_publication_verifies_without_the_runner(rig: Rig) -> None:
    adapters, commit = attested(rig)
    shutil.rmtree(rig.clone)
    shutil.rmtree(rig.workspace)

    proof = read(rig, adapters, commit)

    assert (proof.status, proof.reasons) == ("attested", ())
    assert proof.facts is not None and proof.attestation is not None
    assert proof.facts.commit == commit
    assert proof.facts.parent == rig.base
    assert proof.facts.signature_class == ("github_app" if rig.path == "A" else "bot_key")
    digest = rig.transaction.content.release_manifest_sha256
    assert proof.facts.release_manifest_sha256 == digest
    assert proof.facts.release_manifest_signature_path == release_signature_path(digest)
    assert proof.claims is not None
    assert proof.claims.transaction_id == rig.transaction.transaction_id
    assert "policy_owner_signature" in proof.attestation.not_independently_verifiable


def test_the_receipt_names_the_bot_key_and_the_published_signature(rig: Rig) -> None:
    adapters, commit, outcome = published(rig)
    receipt = outcome.receipt
    assert receipt is not None and receipt.receipt_version == "publication-receipt/2"

    proof = read(rig, adapters, commit)

    assert proof.facts is not None
    assert receipt.bot_fingerprint == proof.facts.manifest_signer_fingerprint
    assert receipt.manifest_signature_sha256 == proof.facts.release_manifest_signature_sha256


def test_recovery_reads_an_attested_publication_as_attested(rig: Rig) -> None:
    adapters, _ = attested(rig)

    outcome = recover(rig.request(), adapters.at(NOW))

    assert (outcome.classification, outcome.attestation) == ("already_receipted", "attested")
    assert not publication_attempts.latched(rig.workspace)


# --- missing attestation: published, attestable, never proof -------------------------------


def test_a_missing_attestation_is_attestable_never_attested(rig: Rig) -> None:
    adapters, commit, outcome = published(rig)

    assert outcome.attestation == "missing"
    proof = read(rig, adapters, commit)
    assert (proof.status, proof.reasons) == ("unattested", ("attestation_missing",))
    assert proof.attestation is None
    assert not publication_attempts.latched(rig.workspace)


def test_a_withheld_attestation_reads_as_unattested(rig_a: Rig) -> None:
    adapters, commit = attested(rig_a)
    del adapters.github.refs[attestation_ref(commit)]

    assert read(rig_a, adapters, commit).status == "unattested"
    assert recover(rig_a.request(), adapters.at(NOW)).attestation == "missing"


def test_a_crash_before_the_attestation_leaves_it_to_a_later_attest(rig: Rig) -> None:
    adapters, commit, _ = published(rig)
    shutil.rmtree(rig.clone / ".git" / "lovspor-release-manifest-signatures")

    again = recover(rig.request(), adapters.at(NOW))
    done = attest(rig.request(), adapters.at(NOW), bot(rig))

    assert (again.classification, again.attestation) == ("already_receipted", "missing")
    assert done.status == "attested"
    assert read(rig, adapters, commit).status == "attested"


# --- create-only, idempotent, crash-recoverable --------------------------------------------


def test_attesting_again_writes_nothing(rig: Rig) -> None:
    adapters, commit = attested(rig)
    before = tag_count(rig, adapters)

    again = attest(rig.request(), adapters.at(NOW), bot(rig))

    assert again.status == "already_attested"
    assert tag_count(rig, adapters) == before == 1
    assert read(rig, adapters, commit).status == "attested"


def test_a_crash_after_the_tag_is_created_is_recovered_by_a_retry(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters, commit, _ = published(rig)
    if rig.path == "A":
        adapters.github.ref_fault = "crash"
    else:
        push = publication_path_b.push_attestation

        def crashing(*args: Any) -> Any:
            push(*args)
            raise SimulatedCrash

        monkeypatch.setattr(publication_path_b, "push_attestation", crashing)
    with pytest.raises(SimulatedCrash):
        attest(rig.request(), adapters.at(NOW), bot(rig))
    adapters.github.ref_fault = None
    monkeypatch.undo()

    again = attest(rig.request(), adapters.at(NOW), bot(rig))

    assert again.status == "already_attested"
    assert tag_count(rig, adapters) == 1
    assert read(rig, adapters, commit).status == "attested"


def test_a_lost_answer_is_read_back(rig_a: Rig) -> None:
    adapters, commit, _ = published(rig_a)
    adapters.github.ref_fault = "lost"

    done = attest(rig_a.request(), adapters.at(NOW), bot(rig_a))

    assert done.status == "attested"
    assert read(rig_a, adapters, commit).status == "attested"


def test_a_competing_writer_never_moves_the_tag(rig: Rig) -> None:
    adapters, commit = attested(rig)
    store = source(rig, adapters, builder=True).attestations
    other = forged(rig, adapters, commit, lambda a: a.model_copy(update={"mode": "simulation"}))

    for files in (other, held_files(rig, adapters, commit)):
        assert store.write(commit, files, NOW).status == "exists"

    assert read(rig, adapters, commit).status == "attested"
    assert tag_count(rig, adapters) == 1


def test_attesting_never_publishes_and_never_settles_twice(rig: Rig) -> None:
    adapters, commit, _ = published(rig)
    ledger = ledger_bytes(rig)
    head = adapters.github.head_oid if rig.path == "A" else rig.remote_files()

    attest(rig.request(), adapters.at(NOW), bot(rig))
    attest(rig.request(), adapters.at(NOW), bot(rig))
    recover(rig.request(), adapters.at(NOW))

    assert ledger_bytes(rig) == ledger
    assert (adapters.github.head_oid if rig.path == "A" else rig.remote_files()) == head
    assert read(rig, adapters, commit).facts is not None


# --- not attestable --------------------------------------------------------------------------


def test_nothing_published_is_not_attestable(rig: Rig) -> None:
    adapters = Adapters(rig)

    detail = refused(lambda: attest(rig.request(), adapters.at(NOW), bot(rig)))

    assert detail == "attestation_not_attestable:no_published_receipt"
    assert tag_count(rig, adapters) == 0


def test_unknown_authorization_is_published_but_never_attested(rig_a: Rig) -> None:
    adapters = Adapters(rig_a)
    adapters.github.now = None  # GitHub kept no record of the ref update
    assert execute(rig_a.request(), adapters.at(NOW)).status == "published"
    outcome = recover(rig_a.request(), adapters.at(NOW))

    detail = refused(lambda: attest(rig_a.request(), adapters.at(NOW), bot(rig_a)))

    assert outcome.classification == "published_authorization_unknown"
    assert outcome.attestation == "not_attestable"
    assert detail == "attestation_not_attestable:published_authorization_unknown"
    assert not adapters.github.refs


# --- invalid evidence in the commit ---------------------------------------------------------


def forged_commit(rig: Rig, adapters: Adapters, signature: bytes | None) -> str:
    """Another App-signed commit naming the transaction, with this manifest signature."""
    files = {
        item.path: publication_stage.read_file(rig.clone, item.path)
        for item in rig.transaction.content.files
    }
    if signature is not None:
        files[release_signature_path(rig.transaction.content.release_manifest_sha256)] = signature
    message = (headline(rig.transaction), body(rig.transaction))
    return adapters.github.advance(files, message, (APP, True))


@pytest.mark.parametrize(
    ("signature", "reason"),
    [
        (None, "manifest_signature_not_published"),
        (b"", "manifest_signature_not_published"),
        ("stranger", "manifest_signature:signer_not_in_trust_root"),
        ("namespace", "manifest_signature:signature_namespace_mismatch"),
        ("other_bytes", "manifest_signature:signature_invalid"),
    ],
)
def test_a_missing_or_substituted_manifest_signature_is_invalid_evidence(
    rig_a: Rig, signature: Any, reason: str
) -> None:
    adapters = Adapters(rig_a)
    manifest = publication_stage.read_file(
        rig_a.clone, rig_a.transaction.content.release_manifest_path
    )
    made = {
        "stranger": lambda: sign(rig_a.stranger, manifest, RELEASE_NAMESPACE),
        "namespace": lambda: sign(rig_a.bot, manifest, PUBLICATION_NAMESPACE),
        "other_bytes": lambda: sign(rig_a.bot, manifest + b" ", RELEASE_NAMESPACE),
    }
    commit = forged_commit(rig_a, adapters, made[signature]() if signature in made else signature)

    proof = read(rig_a, adapters, commit)
    outcome = recover(rig_a.request(), adapters.at(NOW))

    assert proof.status == "invalid" and reason in proof.reasons, proof.reasons
    assert outcome.classification == "halted_integrity_failed"
    assert outcome.receipt is not None
    (found,) = [c for c in outcome.receipt.integrity_checks if c.name == CHECK]
    assert found.status == "failed" and reason in found.reasons, found.reasons
    assert outcome.attestation is None
    assert publication_attempts.latched(rig_a.workspace)
    detail = refused(lambda: attest(rig_a.request(), adapters.at(NOW), bot(rig_a)))
    assert detail == "attestation_not_attestable:no_published_receipt"


def test_a_revoked_bot_key_invalidates_the_reader(rig: Rig) -> None:
    adapters, commit = attested(rig)
    revoked = _root(rig.tmp / "revoked", rig.owner, rig.bot, rig.witness_key, (rig.bot,))

    proof = read(rig, adapters, commit, root=revoked)

    assert proof.status == "invalid"
    assert "manifest_signature:signer_key_revoked" in proof.reasons


# --- invalid attestations ---------------------------------------------------------------------


def held_files(rig: Rig, adapters: Adapters, commit: str) -> dict[str, bytes]:
    stored = source(rig, adapters).attestations.read(commit, NOW)
    assert stored is not None and stored.attestation and stored.signature
    return {ATTESTATION_FILE: stored.attestation, ATTESTATION_SIGNATURE_FILE: stored.signature}


def genuine(rig: Rig, adapters: Adapters, commit: str) -> PublicationAttestation:
    """The attestation an honest runner writes for this receipt and this remote."""
    proof = read(rig, adapters, commit)
    assert proof.facts is not None
    tid = rig.transaction.transaction_id
    ((receipt, raw),) = [
        (r, b) for r, b in publication_receipts.chain(rig.workspace) if r.transaction_id == tid
    ]
    return attestation_of(receipt, raw, proof.facts)


def forged(
    rig: Rig,
    adapters: Adapters,
    commit: str,
    change: Callable[[PublicationAttestation], PublicationAttestation],
    key: Any = None,
    namespace: str = PUBLICATION_NAMESPACE,
) -> dict[str, bytes]:
    payload = canonical_text(change(genuine(rig, adapters, commit))).encode()
    signature = sign(key or rig.bot, payload, namespace)
    return {ATTESTATION_FILE: payload, ATTESTATION_SIGNATURE_FILE: signature}


def remote_change(**fields: Any) -> Callable[[PublicationAttestation], PublicationAttestation]:
    def change(found: PublicationAttestation) -> PublicationAttestation:
        return found.model_copy(update={"remote": found.remote.model_copy(update=fields)})

    return change


OTHER = "f" * 40


def wrong_policy(found: PublicationAttestation) -> PublicationAttestation:
    policy = found.remote.policy.model_copy(update={"policy_sha256": "0" * 64})
    return remote_change(policy=policy)(found)


CASES: dict[str, tuple[Callable[[Rig, Adapters, str], dict[str, bytes]], str]] = {
    "wrong_commit": (
        lambda r, a, c: forged(r, a, c, remote_change(commit=OTHER)),
        "attestation_not_this_commit",
    ),
    "wrong_parent": (
        lambda r, a, c: forged(r, a, c, remote_change(parent=OTHER)),
        "attestation_facts_mismatch",
    ),
    "wrong_plan": (
        lambda r, a, c: forged(r, a, c, remote_change(plan_id="0" * 64)),
        "attestation_facts_mismatch",
    ),
    "wrong_policy": (
        lambda r, a, c: forged(r, a, c, wrong_policy),
        "attestation_facts_mismatch",
    ),
    "wrong_manifest": (
        lambda r, a, c: forged(r, a, c, remote_change(release_manifest_signature_sha256="0" * 64)),
        "attestation_facts_mismatch",
    ),
    "wrong_signer": (
        lambda r, a, c: forged(r, a, c, lambda x: x, key=r.stranger),
        "attestation_signature:signer_not_in_trust_root",
    ),
    "wrong_namespace": (
        lambda r, a, c: forged(r, a, c, lambda x: x, namespace=RELEASE_NAMESPACE),
        "attestation_signature:signature_namespace_mismatch",
    ),
    "substituted_signature": (
        lambda r, a, c: {
            ATTESTATION_FILE: canonical_text(genuine(r, a, c)).encode(),
            ATTESTATION_SIGNATURE_FILE: sign(r.bot, b"something else", PUBLICATION_NAMESPACE),
        },
        "attestation_signature:signature_invalid",
    ),
    "missing_signature": (
        lambda r, a, c: {
            ATTESTATION_FILE: canonical_text(genuine(r, a, c)).encode(),
            ATTESTATION_SIGNATURE_FILE: b"",
        },
        "attestation_incomplete",
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_invalid_attestation_is_never_attested_and_halts(rig: Rig, case: str) -> None:
    adapters, commit, _ = published(rig)
    build, reason = CASES[case]
    files = build(rig, adapters, commit)
    store = source(rig, adapters, builder=True).attestations
    assert store.write(commit, files, NOW).status == "created"

    proof = read(rig, adapters, commit)
    outcome = recover(rig.request(), adapters.at(NOW))
    latched_by_recovery = publication_attempts.latched(rig.workspace)
    detail = refused(lambda: attest(rig.request(), adapters.at(NOW), bot(rig)))

    assert proof.status == "invalid" and reason in proof.reasons, proof.reasons
    assert outcome.attestation == "invalid"
    assert latched_by_recovery
    assert detail.startswith("attestation_conflict:")


def test_an_attestation_resting_on_another_receipt_is_invalid_for_recovery(rig: Rig) -> None:
    """The bot's statement verifies and its facts are the remote's, but not this receipt's."""
    adapters, commit, _ = published(rig)

    def other_receipt(found: PublicationAttestation) -> PublicationAttestation:
        claims = found.claims.model_copy(update={"receipt_sequence": 7})
        return found.model_copy(update={"claims": claims})

    files = forged(rig, adapters, commit, other_receipt)
    assert source(rig, adapters, builder=True).attestations.write(commit, files, NOW).status == (
        "created"
    )

    proof = read(rig, adapters, commit)
    outcome = recover(rig.request(), adapters.at(NOW))
    latched_by_recovery = publication_attempts.latched(rig.workspace)
    detail = refused(lambda: attest(rig.request(), adapters.at(NOW), bot(rig)))

    assert proof.status == "attested"  # a reader can check the facts, not the runner's claims
    assert outcome.attestation == "invalid"
    assert latched_by_recovery
    assert detail == "attestation_conflict:attestation_not_this_receipts"


def test_a_receipt_the_remote_contradicts_is_never_attested(rig: Rig) -> None:
    adapters, _, outcome = published(rig)
    assert outcome.receipt is not None
    forged_receipt = outcome.receipt.model_copy(update={"bot_fingerprint": "SHA256:forged"})
    held = rig.workspace / "receipts" / "000001.json"
    held.unlink()
    held.write_text(canonical_text(forged_receipt))
    held.chmod(0o600)

    detail = refused(lambda: attest(rig.request(), adapters.at(NOW), bot(rig)))

    assert detail == "attestation_conflict:receipt_contradicted:bot_fingerprint"
    assert publication_attempts.latched(rig.workspace)
    assert tag_count(rig, adapters) == 0


def test_an_attestation_replayed_under_another_commit_is_refused(rig: Rig) -> None:
    adapters, commit = attested(rig)
    stored = source(rig, adapters).attestations.read(commit, NOW)
    proof = read(rig, adapters, commit)
    assert stored is not None and proof.facts is not None
    elsewhere = proof.facts.model_copy(update={"commit": OTHER})

    found, reasons = read_attestation(stored, elsewhere, rig.inputs.settings, (rig.root, NOW))

    assert found is None
    assert "attestation_not_this_commit" in reasons


def test_a_storage_commit_by_someone_else_is_invalid(rig_a: Rig) -> None:
    adapters, commit, _ = published(rig_a)
    payload = canonical_text(genuine(rig_a, adapters, commit)).encode()
    files = {
        ATTESTATION_FILE: payload,
        ATTESTATION_SIGNATURE_FILE: sign(rig_a.bot, payload, PUBLICATION_NAMESPACE),
    }
    oid = adapters.github.put_object(files, f"forarbeider attestation {commit}\n", (None, False))
    adapters.github.refs[attestation_ref(commit)] = oid

    proof = read(rig_a, adapters, commit)

    assert proof.status == "invalid"
    assert "storage_not_made_by_an_app" in proof.reasons
    assert "storage_signature_not_verified" in proof.reasons


def test_an_unreadable_store_is_never_attested(rig_a: Rig) -> None:
    adapters, commit = attested(rig_a)
    adapters.github.objects.clear()

    proof = read(rig_a, adapters, commit)

    assert proof.status == "invalid"
    assert proof.reasons == ("attestation_store_unreadable", "attestation_incomplete")


# --- history -----------------------------------------------------------------------------------


def later_commit(rig: Rig, adapters: Adapters) -> str:
    """Someone moves ``main`` past the publication (a later batch, as the reader sees it)."""
    if rig.path == "A":
        return adapters.github.advance({"INDEX.md": b"later\n"}, ("later", ""), (APP, True))
    work = rig.tmp / "later"
    git(rig.tmp, "clone", "--quiet", str(rig.bare), str(work))
    (work / "INDEX.md").write_text("later\n")
    git(work, "commit", "--quiet", "-am", "later")
    git(work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    return git(work, "rev-parse", "HEAD").strip()


def test_a_historical_publication_stays_attested_after_later_commits(rig: Rig) -> None:
    adapters, commit = attested(rig)
    later = later_commit(rig, adapters)

    proof = read(rig, adapters, commit)

    assert (proof.status, proof.reasons) == ("attested", ())
    assert read(rig, adapters, later).status == "invalid"


def test_a_commit_not_on_main_is_not_found(rig: Rig) -> None:
    adapters, _ = attested(rig)

    proof = read(rig, adapters, OTHER)

    assert (proof.status, proof.reasons) == ("not_found", ("commit_not_on_main",))


def test_the_store_reads_nothing_for_an_unattested_commit(rig: Rig) -> None:
    adapters, commit, _ = published(rig)

    found: Stored | None = source(rig, adapters).attestations.read(commit, NOW)

    assert found is None
    assert publication_attestation_store.Store is type(source(rig, adapters).attestations)
