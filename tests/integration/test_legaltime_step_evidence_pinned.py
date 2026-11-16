"""ADR-0023 Phase B4a on the pinned archives: what the evidence builders prove, and refuse.

``lov/2005-05-20-28`` is enacted by ``lti/2005/nl-20050520-028.xml`` (sha256 ``b405ad44…f8b8``)
in the pinned 2001-2025 archive. ``build_step0`` mints its 102 identities and their stamps
from the member's own nodes; § 101 stands directly under ``del3``. Over the act's collected
evidence, ``build_evidence`` builds what D7 attribution, a proven date and the sentence
prove, and refuses the rest: the 2008 rename ``§ 101 … blir § 401`` stays unbound (B2), so
the 2009 rename of § 401 finds no source and changes nothing.

A plain insertion inside an amending member's own chapter (``lti/2011/nl-20110624-030.xml``,
its *Kapittel 13*) carries that chapter as ``_enclosing``'s stamp; the builder keeps it as
raw evidence and never as a placement (issue #935).

Runs only where the pinned archives sit in the gitignored cache; a skip is not a pass.
"""

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256, read_members
from lovspor.legaltime.commencement import Decisions
from lovspor.legaltime.document import Document
from lovspor.legaltime.identity import enacted_id
from lovspor.legaltime.run import _events_of
from lovspor.legaltime.step import apply_step
from lovspor.legaltime.step_evidence import (
    PART_REWRITE,
    STEP0_POLICY,
    Build,
    BuiltOperation,
    CandidateError,
    Step0,
    Unbuilt,
    _placement,
    build_evidence,
    build_operation,
    build_step0,
    node_digest,
    verify_step0,
)
from lovspor.legaltime.structure import Pair

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
ACT = "lov/2005-05-20-28"
ENACTING = "lti/2005/nl-20050520-028.xml"
ENACTING_SHA = "b405ad4434c19109343fee3fa30cd31ec1be499098205420dfae323339d3f8b8"
STEP0_BYTES = "2efa7985e0785ac6e111a845650e72b1c88be7ed667196207305c7e52fe0bd82"
RENAME_2008 = "lov/2008-03-07-4#0:I:112.0"
RENAME_2009 = "lov/2009-06-19-74#1:I:239.0"
HELSE = "lti/2011/nl-20110624-030.xml"

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


@pytest.fixture(scope="module")
def step0(enacting: Document) -> Step0:
    built = build_step0(enacting, ACT)
    assert isinstance(built, Step0), built
    return built


@pytest.fixture(scope="module")
def build() -> Build:
    _pinned()
    return build_evidence(collect(ACT, ARCHIVES))


def test_step_0_is_built_from_the_pinned_enacting_member(enacting: Document, step0: Step0) -> None:
    assert enacting.sha256 == ENACTING_SHA == step0.member_sha256
    assert (step0.policy, step0.published.isoformat()) == (STEP0_POLICY, "2005-05-20")
    assert len(step0.sources) == len(step0.ledger.identities) == 102
    assert verify_step0(enacting, step0)
    assert hashlib.sha256(step0.canonical_json().encode()).hexdigest() == STEP0_BYTES


def test_the_enacted_101_stands_under_the_third_part_by_its_own_node(step0: Step0) -> None:
    pid = enacted_id(ACT, ENACTING, "§101")
    (source,) = [s for s in step0.sources if s.provision_id == pid]
    assert (source.data_name, source.printed_label, source.stamp) == (
        "§101", "§ 101", Pair(chapter=None, part="del3"),
    )  # fmt: skip
    assert [e.name for e in source.enclosures] == ["del3"]
    assert source.node_sha256 == node_digest(ENACTING, ENACTING_SHA, "§101", "§ 101")
    identity = step0.ledger.get(pid)
    assert (
        identity is not None and identity.intervals[0].start.sentence_sha256 == source.node_sha256
    )


def test_the_2008_2009_chain_stays_refused(step0: Step0, build: Build) -> None:
    refused = {r.op_id: r.reason for r in build.rename_refusals}
    assert refused[RENAME_2008] == "act not bound by the D7/phase-4B chain"
    (candidate,) = [c for c in build.candidates if c.step.renames]
    (rename,) = candidate.step.renames
    assert (rename.proof.op_id, rename.source, rename.target) == (RENAME_2009, "§401", "§411")
    assert not candidate.complete
    with pytest.raises(CandidateError):
        candidate.executable()
    result = apply_step(step0.ledger, candidate.step, step0.structure)
    reasons = {r.op_id: (r.phase, r.reason) for r in result.refusals}
    assert reasons[RENAME_2009] == ("intrinsic", "rename source missing")
    assert step0.ledger.holder("§401") is None


def test_no_built_insertion_takes_a_stamp_its_structure_does_not_state(build: Build) -> None:
    insertions = [e for c in build.candidates for e in c.evidence if e.placement is not None]
    assert len(insertions) == 204
    for built in insertions:
        evidence = built.placement
        assert evidence is not None
        assert built.operation.placement == evidence.stated  # type: ignore[union-attr]
        assert (evidence.stated is None) == (evidence.sentence_sha256 is None)
    stated = Counter(e.placement.stated is not None for e in insertions)  # type: ignore[union-attr]
    assert stated == {True: 174, False: 30}


def test_rewritten_parts_are_refused_not_manufactured(build: Build) -> None:
    rewrites = [u for u in build.unbuilt if u.reason == PART_REWRITE]
    assert len(rewrites) == 86
    assert {u.member for u in rewrites} == {
        "lti/2008/nl-20080307-004.xml"
    }  # "Annen del skal lyde:"


def test_the_pinned_build_is_pinned(build: Build) -> None:
    assert (len(build.candidates), sum(c.complete for c in build.candidates)) == (48, 30)
    reasons = Counter(u.reason.split(":")[0] for u in build.unbuilt)
    assert reasons == {
        "timing not proven": 120,
        "act not bound by the D7/phase-4B chain": 120,
        "whole part rewritten": 86,
        "operation kind not represented by B1": 55,
        "range or several sections": 4,
        "payload not headed by the addressed label": 3,
    }
    assert len(build.member_gaps) == 148


def test_an_amending_members_own_chapter_is_never_a_placement() -> None:
    _pinned()
    document = read_members(ARCHIVES[0], [HELSE])[HELSE]
    events = _events_of(document, "lov/1999-07-02-61", Decisions(documents=()), 0)
    (event,) = [e for e in events if e.operation.id == "lov/2011-06-24-30#1:I:335.0"]
    payload = event.operation.new_provision
    assert payload is not None and payload.chapter is None  # #935: not the member's kap13
    assert event.operation.stated is None
    evidence, problem = _placement(event.operation.stated, payload)
    assert problem is None and evidence.stated is None
    assert evidence.inherited == Pair(chapter="kap13")
    built = build_operation(event, {HELSE: document})
    assert isinstance(built, Unbuilt | BuiltOperation)
    assert not (isinstance(built, BuiltOperation) and built.operation.placement is not None)  # type: ignore[union-attr]
