"""AR5 gate: a valid but foreign quarantine chain never governs a publication (#738; §9.9).

Two quarantine chains, Q1 and Q2, both canonical and anchored on the same
witness under the same repository and trust root. Q1 is the publication's and
quarantines a document of the batch; Q2 is anchored independently, under
another ledger id, and quarantines nothing.

The trust contract, as it stands (#1037): the witness anchors
``quarantine_succession`` per repository and ledger id. Repository, domain and
policy cannot tell the two chains apart (an empty chain names no policy),
``PublicationSettings`` is unverified until AR7, the budget ledger is the
caller's (ledger v2), and no owner-signed record names a quarantine chain. So
the caller's source decides, at planning, preparation, authorization and the
send. Those reproductions are strict ``xfail``: each fails the suite the moment
a binding lands, until the owner chooses one.

The legitimate configurations (one workspace; a second workspace sharing the
ledger, on its own witness or on the same one) are pinned on both paths: the
publication's own chain governs every one of them.
"""

import shutil
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_anchors, publication_ledger
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_quarantine import (
    WitnessedSource,
    Witnessing,
    prepare_witnessed,
)
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.quarantine_succession import (
    QuarantineStore,
    SuccessionRequest,
    append_successor,
    initialize_quarantine,
    policies_of,
    read_quarantine,
)
from lovspor.forarbeider.quarantine_succession_models import document_sha256
from lovspor.forarbeider.release_quarantine import EMPTY_LEDGER, merge
from lovspor.forarbeider.release_quarantine_run import ExceptionMaterial
from tests.unit.forarbeider_execution_support import NOW, KeySigner, Rig, make_rig
from tests.unit.forarbeider_release_control_support import CHECKED_AT, policy_bytes
from tests.unit.test_forarbeider_quarantine_integration import (
    anchored,
    batch_finding,
    batches,
    bootstrapped,
    chain,
    chain_keys,
    chain_witness,
    environment_of,
    plan,
    planner,
    preparation_of,
    quarantined_two,
    reasons_of,
    remote_unchanged,
)
from tests.unit.test_forarbeider_quarantine_succession import REPOSITORY, WITNESS
from tests.unit.test_forarbeider_quarantine_succession import Rig as ChainRig
from tests.unit.test_forarbeider_release_quarantine import render_failure

__all__ = ["chain", "chain_keys", "chain_witness", "planner"]

FOREIGN_ID = "synthetic-quarantine-foreign"
SUBSTITUTION = pytest.mark.xfail(
    strict=True, reason="#1037: no trusted configuration names the publication's quarantine chain"
)


def foreign(root: Path, repository: str, authority: Any) -> WitnessedSource:
    """Q2: another chain, anchored empty on the same witness under the same root."""
    store = QuarantineStore(root / "foreign-quarantine", FOREIGN_ID)
    assert initialize_quarantine(store, repository, authority).status == "verified"
    return WitnessedSource(store, (policy_bytes(),))


def authority_of(chosen: Rig) -> Any:
    """Where the publication's chain is anchored: the ledger's witness (AR5d-3)."""
    authority = publication_anchors.attempts_authority_of(chosen.request())
    assert authority is not None
    return authority


def foreign_of(chosen: Rig) -> WitnessedSource:
    return foreign(chosen.tmp, chosen.inputs.settings.repository, authority_of(chosen))


def blocked(chosen: Rig) -> None:
    """Q1, the publication's own chain, quarantines a document X of the batch."""
    assert chosen.quarantine is not None
    wanted = SuccessionRequest(
        repository=chosen.inputs.settings.repository,
        expected_predecessor_sha256=document_sha256(EMPTY_LEDGER),
        successor=merge(EMPTY_LEDGER, [batch_finding(chosen)], NOW),
        exceptions=ExceptionMaterial(),
        policy=None,
        at=NOW,
    )
    outcome = append_successor(chosen.quarantine.store, wanted, authority_of(chosen))
    assert outcome.status == "appended"


# --- the legitimate configurations, paths A and B -----------------------------------------------

CONFIGURATIONS = ["one_workspace", "shared_ledger_own_witness", "shared_ledger_one_witness"]


@pytest.fixture(params=[(c, p) for c in CONFIGURATIONS for p in ("A", "B")], ids=str)
def publisher(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    configuration, path = request.param
    first = make_rig(tmp_path / "first", path)
    if configuration == "one_workspace":
        return first
    one_witness = configuration == "shared_ledger_one_witness"
    shared = make_rig(tmp_path / "shared", path, share=first, one_witness=one_witness)
    environment, _ = environment_of(first)
    assert execute(first.request(), environment).status == "published"
    assert recover(first.request(), environment).settlement.status == "settled"
    assert publication_ledger.reserve(shared.request(), NOW).status == "appended"
    return shared


def test_the_publications_own_empty_chain_reaches_the_send(publisher: Rig) -> None:
    environment, _ = environment_of(publisher)

    outcome = execute(publisher.request(), environment)

    assert outcome.status == "published"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ()


def test_a_document_the_publications_own_chain_quarantines_is_never_sent(publisher: Rig) -> None:
    blocked(publisher)
    environment, github = environment_of(publisher)

    outcome = execute(publisher.request(), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ("quarantine_not_the_anchored_head",)
    assert remote_unchanged(publisher, github)


def test_a_publication_with_no_chain_is_never_sent(publisher: Rig) -> None:
    environment, github = environment_of(publisher)

    outcome = execute(publisher.request(quarantine=None), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ("quarantine_source_missing",)
    assert remote_unchanged(publisher, github)


@SUBSTITUTION
def test_a_foreign_chain_that_quarantines_nothing_never_sends(publisher: Rig) -> None:
    blocked(publisher)
    substitute = foreign_of(publisher)
    environment, github = environment_of(publisher)

    outcome = execute(publisher.request(quarantine=substitute), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") != ()
    assert remote_unchanged(publisher, github)


# --- preparation, paths A and B ------------------------------------------------------------------


@pytest.fixture(params=["A", "B"])
def sender(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


@SUBSTITUTION
def test_a_batch_is_never_prepared_from_a_foreign_chain(sender: Rig) -> None:
    blocked(sender)
    substitute = foreign_of(sender)
    repository = sender.inputs.settings.repository
    witnessing = Witnessing(substitute, authority_of(sender), repository, NOW)
    work = sender.tmp / "work-again"
    work.mkdir(mode=0o700)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        prepare_witnessed(preparation_of(sender), witnessing, sender.source, work)

    assert raised.value.detail.startswith("quarantine_witness:prepare:")
    assert list(work.iterdir()) == []


# --- planning ------------------------------------------------------------------------------------


@SUBSTITUTION
def test_a_foreign_chain_never_plans_what_the_publications_chain_quarantines(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    governed = {d for batch in batches(plan(chain, store)) for d in batch}
    substitute = foreign(chain.tmp, REPOSITORY, chain.authority())

    try:
        result = plan(chain, store, source=substitute)
    except ForarbeiderRefusedError as error:
        assert error.detail.startswith("quarantine_witness:")
        return

    assert {d for batch in batches(result) for d in batch} <= governed


# --- why a binding of identifiers alone would be a sham guard (#1037) ----------------------------


def test_a_chain_id_alone_never_identifies_the_source(chain: ChainRig, planner: Any) -> None:
    """The same ledger id, witness key and root on another store verify with other content.

    So an owner-signed binding that names only the ledger and quarantine ids
    could be satisfied by a second store under the publication's own ids. The
    binding needs one designated anchor authority (the bootstrap contract in
    §9.9, #1037) before any guard is built on it.
    """
    store, rows = planner
    quarantined_two(chain, store, rows)
    directory = chain.tmp / "another-witness-store"
    directory.mkdir(mode=0o700)
    another = chain.authority(
        witness=OfflineAnchorWitness(directory, KeySigner(WITNESS, chain.witness_key))
    )
    same_id = QuarantineStore(chain.tmp / "quarantine-again", chain.store.ledger_id)
    assert initialize_quarantine(same_id, REPOSITORY, another).status == "verified"
    policies = policies_of(policy_bytes())

    own = read_quarantine(chain.store, REPOSITORY, chain.authority(), policies)
    other = read_quarantine(same_id, REPOSITORY, another, policies)

    assert same_id.ledger_id == chain.store.ledger_id
    assert len(own.ledger.records) == 1
    assert other.ledger.records == ()


def test_a_witness_and_store_rolled_back_together_read_the_old_head_as_verified(
    chain: ChainRig, planner: Any
) -> None:
    """The offline witness cannot prove it was not rolled back (AR5a); no independent pin exists.

    Whoever holds both directories restores an older copy, and the newer
    quarantine disappears with nothing to refuse it. The binding authority
    needs a pin independent of the request and of the store, which AR5 has
    not got: the missing AR7 contract in §9.9 (#1037).
    """
    store, rows = planner
    bootstrapped(chain)
    witness_dir, chain_dir = chain.tmp / "witness", chain.store.root
    kept = chain.tmp / "kept"
    shutil.copytree(witness_dir, kept / "witness")
    shutil.copytree(chain_dir, kept / "quarantine")
    anchored(chain, plan(chain, store, reported=(render_failure(rows, 2),)), CHECKED_AT)
    policies = policies_of(policy_bytes())
    assert len(read_quarantine(chain.store, REPOSITORY, chain.authority(), policies).ledger.records)

    for live, copy in ((witness_dir, kept / "witness"), (chain_dir, kept / "quarantine")):
        shutil.rmtree(live)
        shutil.copytree(copy, live)

    rolled_back = read_quarantine(chain.store, REPOSITORY, chain.authority(), policies)
    assert rolled_back.ledger.records == ()
