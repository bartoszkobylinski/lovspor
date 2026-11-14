"""ADR-0023 Phase B3 on the pinned archives: straffeloven's structure map, and what it proves.

``lov/2005-05-20-28`` is enacted by ``lti/2005/nl-20050520-028.xml`` (sha256 ``b405ad44…f8b8``)
in the pinned 2001-2025 archive. ``_enclosing`` reads 102 provisions: §§ 1-100 in chapters
``kap1``-``kap15`` of ``del1``, and §§ 101-102 directly under ``del3`` (no chapter).

The 2009 sentence "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:"
(``lov/2009-06-19-74#1:I:239.0``) carries a part-only clause. R3 gives it (empty, ``del3``)
only if the identity holding § 401 before the step has an empty chapter. That identity is
the enacted § 101 *only* through the 2008 rename "§ 101 i lov 20. mai 2005 nr. 28 blir § 401",
and the engine binds that sentence to no act (B2), so no accepted step moves § 101 to § 401.
The old membership is therefore **not proven**: the rename is refused ("rename source
missing") and no stamp is written. What the source does prove is pinned beside it: the
enacted § 101's own pair is (empty, ``del3``), the pair the clause would confirm.

Step-0 proofs here carry the real member digest; their ``sentence_sha256`` is a test-local
stand-in (the provision's printed label and heading), because step 0 has no instruction
sentence and no production builder of step-0 proofs exists yet (a missing requirement,
recorded in the research note).

Runs only where the pinned archives sit in the gitignored cache; a skip is not a pass.
"""

import hashlib
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256, read_members
from lovspor.legaltime.document import Document
from lovspor.legaltime.identity import LabelProof, Ledger, enacted_id, mint_enacted
from lovspor.legaltime.rename_research import adapt_evidence
from lovspor.legaltime.step import apply_step
from lovspor.legaltime.structure import (
    Clause,
    Pair,
    Structure,
    enacted_structure,
    placed_pair,
    read_clause,
    stamps_of,
)
from lovspor.legaltime.text import section_key

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
ACT = "lov/2005-05-20-28"
ENACTING = "lti/2005/nl-20050520-028.xml"
ENACTING_SHA = "b405ad4434c19109343fee3fa30cd31ec1be499098205420dfae323339d3f8b8"
PUBLISHED = date(2005, 5, 20)
RENAME_2009 = "lov/2009-06-19-74#1:I:239.0"
SENTENCE_2009 = "bbbac519dcb305364cd56dcfebf96675ca010466f7d33ed5f0ec81c000fdc926"
CLAUSE = "som innleder tredje del"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


def _pinned() -> None:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")


@pytest.fixture(scope="module")
def enacting() -> Document:
    _pinned()
    return read_members(ARCHIVES[0], [ENACTING])[ENACTING]


def _proof(document: Document, label: str, heading: str) -> LabelProof:
    stand_in = hashlib.sha256(f"{label}\n{heading}".encode()).hexdigest()
    return LabelProof(
        instrument=ACT, op_id=f"{ACT}#enacted:{section_key(label)}", member=ENACTING,
        member_sha256=document.sha256, sentence_sha256=stand_in, effective=None,
        known_from=PUBLISHED, label_minted_at=PUBLISHED,
    )  # fmt: skip


@pytest.fixture(scope="module")
def step0(enacting: Document) -> tuple[Ledger, Structure]:
    labels = [
        (str(section_key(p.label)), _proof(enacting, p.label, p.heading))
        for p in enacting.provisions
    ]
    ledger = mint_enacted(ACT, ENACTING, PUBLISHED, labels)
    return ledger, enacted_structure(ledger, stamps_of(enacting))


def test_step_0_stamps_every_enacted_identity_from_the_pinned_structure(
    enacting: Document, step0: tuple[Ledger, Structure]
) -> None:
    ledger, structure = step0
    assert enacting.sha256 == ENACTING_SHA
    assert len(ledger.identities) == len(structure.memberships) == 102
    pairs = Counter((p.chapter, p.part) for p in structure.live_map().values() if p is not None)
    assert pairs[(None, "del3")] == 2 and sum(pairs.values()) == 102
    assert {e.chapter: e.part for e in structure.outline} == {
        f"kap{n}": "del1" for n in range(1, 16)
    }
    assert structure.parts() == {"del1", "del3"}


def test_the_enacted_101_stands_directly_under_the_third_part(
    step0: tuple[Ledger, Structure],
) -> None:
    _, structure = step0
    pid = enacted_id(ACT, ENACTING, "§101")
    membership = structure.membership(pid)
    assert membership is not None and membership.current == Pair(chapter=None, part="del3")
    (event,) = membership.events
    assert (event.cause, event.proof.member_sha256, event.proof.effective) == (
        "enacted", ENACTING_SHA, None,
    )  # fmt: skip
    # The 2009 clause, read on the enacted § 101's own pair, would only confirm it:
    assert read_clause(CLAUSE) == Clause(part="del3")
    assert placed_pair(structure, pid, CLAUSE) == Pair(chapter=None, part="del3")


def test_the_outline_witness_is_a_real_enacted_identity(step0: tuple[Ledger, Structure]) -> None:
    ledger, structure = step0
    ids = {i.provision_id for i in ledger.identities}
    assert all(
        e.witness in ids and e.proof.member_sha256 == ENACTING_SHA for e in structure.outline
    )


def test_the_2009_rename_reaches_b1_with_its_clause_and_is_refused_unproven(
    step0: tuple[Ledger, Structure],
) -> None:
    """§ 401's old membership needs the 2008 rename, which no chain binds: refused, not faked."""
    ledger, structure = step0
    research = adapt_evidence(collect(ACT, ARCHIVES))
    unbound = {r.op_id: r.reason for r in research.refusals}
    assert unbound["lov/2008-03-07-4#0:I:112.0"] == "act not bound by the D7/phase-4B chain"
    (candidate,) = research.steps
    assert not candidate.complete  # never executable as a replay step
    (rename,) = candidate.step.renames
    assert (rename.proof.op_id, rename.proof.sentence_sha256) == (RENAME_2009, SENTENCE_2009)
    assert (rename.source, rename.target, rename.placement) == ("§401", "§411", CLAUSE)
    assert ledger.holder("§401") is None
    result = apply_step(ledger, candidate.step, structure)
    (refusal,) = result.refusals
    assert (refusal.phase, refusal.reason) == ("intrinsic", "rename source missing")
    assert result.ledger.canonical_json() == ledger.canonical_json()
    assert result.structure is not None
    assert result.structure.canonical_json() == structure.canonical_json()


def test_the_step_0_structure_is_deterministic(enacting: Document) -> None:
    labels = [
        (str(section_key(p.label)), _proof(enacting, p.label, p.heading))
        for p in reversed(enacting.provisions)
    ]
    ledger = mint_enacted(ACT, ENACTING, PUBLISHED, labels)
    first = enacted_structure(ledger, stamps_of(enacting)).canonical_json()
    stamps = dict(reversed(list(stamps_of(enacting).items())))
    assert enacted_structure(ledger, stamps).canonical_json() == first
