"""ADR-0023 Phase B2 on the pinned archives: what the source proves, and what it does not.

Runs only where the pinned publicData archives sit in the gitignored cache. A skip means
the evidence was not replayed on this machine, not a pass.

Expected outcomes are read from the engine at main ``c582f4d6``. Each is a refusal
because a proof the adapter needs is missing from the engine's events. None is made to
pass by assumption.

* straffeloven ``lov/2005-05-20-28``:
  * 2008 "§ 101 i lov 20. mai 2005 nr. 28 blir § 401, og skal lyde slik:" and the partial
    "De delene av § 102 …" stand under "I straffeloven 2005 tredje del gjøres følgende
    endringer:", which the D7/phase-4B chain does not bind. Act unbound, so both are refused.
  * 2009 "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:" is bound and
    dated 2009-06-19, but carries a placement. With no structure map ``S`` the whole rename
    is refused, and the clause is kept.
  * 2009 "Nåværende § 402 blir § 412 og skal lyde:" is bound by the engine to
    ``lov/1975-06-13-39``, an attribution defect (numbered items inside § 411's payload
    rebind the act). It is therefore absent from straffeloven's events. This test pins that
    absence.
* atomenergiloven ``lov/1972-05-12-28``, amended by ``lov/2007-06-15-37`` (the
  21→22→23→25→21 cycle):
  * ``collect`` refuses the act: its 1972 enacting member is not in the archives;
  * read through the engine's per-member chain, part I's timing is ``unresolved``
    ("commencement sentence outside the grammar");
  * four of the cycle's sentences use ", som skal lyde:", outside R3's closed grammar.
  * No step is produced; the cycle is executed only on synthetic input, in the unit tests.
"""

from pathlib import Path

import pytest

from lovspor.legaltime.archive import (
    _decision_names,
    collect,
    file_sha256,
    index_archive,
    read_members,
)
from lovspor.legaltime.commencement import Decisions
from lovspor.legaltime.errors import LegalTimeInputError
from lovspor.legaltime.rename_research import Research, adapt, adapt_evidence
from lovspor.legaltime.run import _events_of, events_for

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
STRAFFELOVEN = "lov/2005-05-20-28"
ATOMENERGILOVEN = "lov/1972-05-12-28"
L2008 = "lti/2008/nl-20080307-004.xml"
L2009 = "lti/2009/nl-20090619-074.xml"
L2007 = "lti/2007/nl-20070615-037.xml"
MEMBERS = {
    L2008: "cd9379f9431a9a9fb5ec65a42be23f369133b9fc2c24603ec279a438923e1e99",
    L2009: "7ec6a1b4ae319b86ae6735eb2e969022e827afad552c9f9e8a9e4e12db1304d4",
    L2007: "f0ad9d2ee78e893e84efae313e6a772f2121a2e33d32d88d6a6eacdbbe9f8b7c",
}

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


@pytest.fixture(scope="module")
def straffeloven() -> Research:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    return adapt_evidence(collect(STRAFFELOVEN, ARCHIVES))


@pytest.fixture(scope="module")
def atomenergiloven() -> Research:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    index = index_archive(ARCHIVES[0])
    names = _decision_names(index, {ATOMENERGILOVEN})
    documents = read_members(ARCHIVES[0], [L2007, *names])
    decisions = Decisions(documents=tuple(documents[n] for n in names))
    events = _events_of(documents[L2007], ATOMENERGILOVEN, decisions, 0)
    return adapt(events, {L2007: documents[L2007]}, frozenset(names))


def test_straffeloven_yields_no_executable_step(straffeloven: Research) -> None:
    assert straffeloven.steps == ()


def test_straffelovens_rename_shaped_sentences_are_exactly_these(straffeloven: Research) -> None:
    """Every rename-shaped event the adapter sees, bound or not: none dropped, none extra."""
    assert sorted(r.op_id for r in straffeloven.refusals) == [
        "lov/2008-03-07-4#0:I:112.0",
        "lov/2008-03-07-4#0:I:114.0",
        "lov/2009-06-19-74#1:I:239.0",
        "lov/2009-06-19-74#1:I:252.0",  # "Nåværende § 9 blir ny § 10.", unbound
    ]


@pytest.mark.parametrize(
    ("op_id", "sentence_sha256", "act", "reason", "placement"),
    [
        ("lov/2008-03-07-4#0:I:112.0",
         "c69bc8353460feee44014ec02b44df9ec535117a3f5d3fcf7d507b679c03e339",
         None, "act not bound by the D7/phase-4B chain", None),
        ("lov/2008-03-07-4#0:I:114.0",
         "5bf83c5ad9864b5cd2daf3b50458a03843e13601506e41a6ec634252b50fba79",
         None, "act not bound by the D7/phase-4B chain", None),
        ("lov/2009-06-19-74#1:I:239.0",
         "bbbac519dcb305364cd56dcfebf96675ca010466f7d33ed5f0ec81c000fdc926",
         STRAFFELOVEN, "placement incomplete: no structure map S", "som innleder tredje del"),
    ],
)  # fmt: skip
def test_each_straffeloven_rename_is_refused_with_its_proof(
    straffeloven: Research,
    op_id: str,
    sentence_sha256: str,
    act: str | None,
    reason: str,
    placement: str | None,
) -> None:
    refusal = {r.op_id: r for r in straffeloven.refusals}[op_id]
    assert (refusal.act, refusal.reason, refusal.placement) == (act, reason, placement)
    assert refusal.sentence_sha256 == sentence_sha256  # = the ADR-0023 evidence file's digest
    assert refusal.member_sha256 == MEMBERS[refusal.member]


def test_the_2009_rename_is_bound_and_dated_but_still_not_executed(straffeloven: Research) -> None:
    refusal = {r.op_id: r for r in straffeloven.refusals}["lov/2009-06-19-74#1:I:239.0"]
    assert (refusal.timing_status, str(refusal.effective), str(refusal.known_from)) == (
        "dated", "2009-06-19", "2009-06-19",
    )  # fmt: skip


def test_the_misattributed_2009_rename_is_absent_from_straffelovens_events() -> None:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    ops = {e.operation.id for e in events_for(collect(STRAFFELOVEN, ARCHIVES))}
    assert "lov/2009-06-19-74#1:I:245.0" not in ops  # bound to lov/1975-06-13-39 instead


def test_atomenergiloven_cannot_be_collected_without_its_enacting_member() -> None:
    with pytest.raises(LegalTimeInputError, match="no enacting member for lov/1972-05-12-28"):
        collect(ATOMENERGILOVEN, ARCHIVES)


def test_atomenergiloven_cycle_is_refused_for_undated_timing_and_grammar(
    atomenergiloven: Research,
) -> None:
    assert atomenergiloven.steps == ()
    reasons = {r.op_id.rsplit("#", 1)[1]: r.reason for r in atomenergiloven.refusals}
    outside = "rename outside the grammar"
    undated = "timing not proven: unresolved: commencement sentence outside the grammar"
    assert reasons == {
        "0:I:14.0": outside, "0:I:16.0": outside, "0:I:18.0": outside,  # ", som skal lyde:"
        "0:I:20.0": undated,  # "Nåværende § 21 blir ny § 22."
        "0:I:21.0": outside, "0:I:25.0": outside,
        "0:I:47.0": undated, "0:I:49.0": undated, "0:I:51.0": undated,
    }  # fmt: skip
    assert {r.member_sha256 for r in atomenergiloven.refusals} == {MEMBERS[L2007]}
    assert {r.timing_status for r in atomenergiloven.refusals} == {"unresolved"}
