"""ADR-0023 Phase B4a: evidence builders for step 0, insertions and candidate steps (#861).

Members are built inline and read by the engine itself: ``parse_document`` for the
enacting member, and ``run._events_of`` (``interpret``, nesting, ``resolve_timings``)
for amending members. Attribution, timing and stamps are the engine's, never the test's.
The pinned straffeloven sources are in
``tests/integration/test_legaltime_step_evidence_pinned.py``.
"""

import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.legaltime.commencement import Decisions, Event
from lovspor.legaltime.document import Document, Enclosure, parse_document
from lovspor.legaltime.identity import enacted_id, inserted_id
from lovspor.legaltime.instructions import Address
from lovspor.legaltime.rename_research import RenameRefusal
from lovspor.legaltime.run import _events_of
from lovspor.legaltime.step import Finer, Insertion, Repeal, Replace, apply_step
from lovspor.legaltime.step_evidence import (
    COMMENCEMENT_STATED,
    DUPLICATE,
    EMPTY,
    KIND,
    MISLABELLED,
    NESTED,
    NO_SECTION,
    NOT_ENACTING,
    OUTLINE,
    PART_REWRITE,
    RANGE,
    STEP0_POLICY,
    TITLE_UNREAD,
    UNBOUND_IN_MEMBER,
    UNDATED,
    UNDATED_IN_MEMBER,
    UNHEADED,
    UNITS,
    UNLABELLED,
    UNREAD_CHAPTER,
    UNREAD_PART,
    UNREAD_SECTION,
    BuiltOperation,
    CandidateError,
    Step0,
    Step0Refusal,
    Unbuilt,
    build_candidates,
    build_operation,
    build_step0,
    node_digest,
    verify_step0,
)
from lovspor.legaltime.structure import Pair

ACT = "lov/2005-05-20-28"
ENACTING = "lti/2005/nl-20050520-028.xml"
PUBLISHED = date(2005, 5, 20)
MEMBER = "lti/2008/nl-20080307-004.xml"
OTHER_ACT = "lov/2005-06-17-62"
INTRO = "Lov 20. mai 2005 nr. 28 om straff endres slik:"
INTRO_OTHER = (
    "Lov 17. juni 2005 nr. 62 om arbeidsmiljø, arbeidstid og stillingsvern mv. endres slik:"
)
STRAKS = "Loven trer i kraft straks."
KONGEN = "Loven trer i kraft fra den tid Kongen bestemmer."
EFFECTIVE = date(2008, 3, 7)


# ------------------------------------------------------------------------ fixtures


def _enacting(sections: str, refid: str = ACT, published: str | None = "2005-05-20") -> Document:
    when = "" if published is None else f'<dd class="dateOfPublication">{published}</dd>'
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">2005-05-20</dd>{when}<dd class="title">Lov om straff</dd>'
        f"</dl></header><main>{sections}</main></body></html>"
    )
    return parse_document(xml.encode(), ENACTING)


def _article(label: str, name: str | None = None, inner: str = "") -> str:
    data_name = label.replace(" ", "") if name is None else name
    return (
        f'<article class="legalArticle" data-name="{data_name}">'
        f'<h3><span class="legalArticleValue">{label}</span></h3>'
        f'<p class="legalP">Tekst.</p>{inner}</article>'
    )


def _sec(name: str | None, body: str) -> str:
    attribute = "" if name is None else f' data-name="{name}"'
    return f'<section class="section"{attribute}>{body}</section>'


STRUCTURED = _sec(
    "del1", _sec("kap1", _article("§ 1") + _article("§ 2")) + _sec("kap2", _article("§ 3"))
) + _sec("del3", _article("§ 101"))


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _title(text: str) -> str:
    return f'<article class="futuretitle">{text}</article>'


def _future(key: str, printed: str | None = None) -> str:
    label = printed or f"§ {key[1:]}"
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        '<span class="futureLegalArticleHeader">'
        f'<span class="legalArticleValue">{label}</span>'
        '<span class="legalArticleTitle">Overskrift</span></span>'
        '<article class="legalP">Teksten.</article></article>'
    )


def _member(
    part_one: str,
    part_two: str = STRAKS,
    changes: tuple[str, ...] = (ACT,),
    member: str = MEMBER,
    wrap: str | None = None,
) -> Document:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    one = f'<section class="section"><h2>I</h2>{part_one}</section>'
    if wrap is not None:  # the amending member's own chapter around its instructions
        one = f'<section class="section" data-name="{wrap}"><h2>Kapittel 13</h2>{one}</section>'
    body = one + f'<section class="section"><h2>II</h2>{_p(part_two)}</section>'
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2008-03-07-4</dd>'
        '<dd class="dateInForce">2008-03-07</dd>'
        '<dd class="dateOfPublication">2008-03-07 14:45</dd>'
        '<dd class="title">Lov om endringer i straffeloven 2005</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )
    return parse_document(xml.encode(), member)


def _events(document: Document, *acts: str | None) -> list[Event]:
    events: list[Event] = []
    for act in acts or (ACT, None):
        events += _events_of(document, act, Decisions(documents=()), len(events))
    return events


def _built(document: Document, act: str | None = ACT) -> list[BuiltOperation | Unbuilt]:
    outcomes = [build_operation(e, {document.member: document}) for e in _events(document, act)]
    assert all(isinstance(o, BuiltOperation | Unbuilt) for o in outcomes)
    return outcomes  # type: ignore[return-value]


def _one(document: Document, act: str | None = ACT) -> BuiltOperation | Unbuilt:
    (outcome,) = _built(document, act)
    return outcome


def _step0(sections: str = STRUCTURED) -> Step0:
    built = build_step0(_enacting(sections), ACT)
    assert isinstance(built, Step0), built
    return built


def _refused(document: Document, act: str = ACT) -> Step0Refusal:
    built = build_step0(document, act)
    assert isinstance(built, Step0Refusal), built
    return built


# --------------------------------------------------------------------------- step 0


def test_step_0_builds_label_identity_and_membership_from_one_source_node() -> None:
    document = _enacting(STRUCTURED)
    step0 = _step0()
    assert [i.label for i in step0.ledger.identities] == ["§1", "§101", "§2", "§3"]
    for source in step0.sources:
        identity = step0.ledger.get(source.provision_id)
        membership = step0.structure.membership(source.provision_id)
        assert identity is not None and membership is not None
        assert source.provision_id == enacted_id(ACT, ENACTING, source.label)
        assert identity.label == source.label
        assert membership.current == source.stamp
        (provision,) = [p for p in document.provisions if p.key == source.data_name]
        assert (provision.chapter, provision.part) == (source.stamp.chapter, source.stamp.part)
        assert source.enclosures == provision.enclosures
    stamps = {s.label: s.stamp for s in step0.sources}
    assert stamps == {
        "§1": Pair(chapter="kap1", part="del1"),
        "§2": Pair(chapter="kap1", part="del1"),
        "§3": Pair(chapter="kap2", part="del1"),
        "§101": Pair(chapter=None, part="del3"),
    }
    assert {e.chapter: e.part for e in step0.structure.outline} == {"kap1": "del1", "kap2": "del1"}


def test_a_step_0_proof_is_a_source_node_proof_never_an_amendment_sentence() -> None:
    document = _enacting(STRUCTURED)
    step0 = _step0()
    assert step0.policy == STEP0_POLICY
    for identity in step0.ledger.identities:
        (interval,) = identity.intervals
        proof = interval.start
        assert proof.effective is None  # step 0 proves no effective date (R2)
        assert proof.label_minted_at == PUBLISHED == proof.known_from
        assert (proof.instrument, proof.member) == (ACT, ENACTING)
        assert proof.member_sha256 == document.sha256 == step0.member_sha256
        assert proof.op_id == f"{ACT}#enacted:{interval.label}"
        source = next(s for s in step0.sources if s.provision_id == identity.provision_id)
        assert proof.sentence_sha256 == source.node_sha256


def test_the_step_0_node_digest_is_the_published_policy_recomputed_by_hand() -> None:
    document = _enacting(STRUCTURED)
    step0 = _step0()
    source = next(s for s in step0.sources if s.label == "§101")
    record = {
        "data_name": "§101",
        "member": ENACTING,
        "member_sha256": document.sha256,
        "policy": "lovspor-step0-label-node-v1",
        "printed_label": "§ 101",
    }
    expected = hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert source.node_sha256 == expected
    assert node_digest(ENACTING, document.sha256, "§101", "§ 101") == expected


def test_step_0_bytes_are_stable() -> None:
    first = _step0().canonical_json()
    assert _step0().canonical_json() == first
    assert hashlib.sha256(first.encode()).hexdigest() == STEP0_BYTES


STEP0_BYTES = "a34c692900ed5b9b8ab21b8529793db587918671be247cd31505577276b50202"


def test_a_step_0_verifies_only_against_its_own_member() -> None:
    document = _enacting(STRUCTURED)
    step0 = _step0()
    assert verify_step0(document, step0)
    other = _enacting(STRUCTURED + _article("§ 400"))
    assert not verify_step0(other, step0)


def test_a_tampered_step_0_does_not_validate() -> None:
    step0 = _step0()
    forged = step0.sources[0].model_copy(update={"node_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="node digest"):
        step0.model_copy(update={"sources": (forged, *step0.sources[1:])})
    with pytest.raises(ValidationError, match="one source per identity"):
        step0.model_copy(update={"sources": step0.sources[1:]})
    moved = step0.sources[0].model_copy(update={"stamp": Pair(part="del9")})
    with pytest.raises(ValidationError, match="membership"):
        step0.model_copy(update={"sources": (moved, *step0.sources[1:])})


def test_step_0_sources_follow_identity_order_not_document_order() -> None:
    forward = _step0(_article("§ 1") + _article("§ 2") + _article("§ 3"))
    backward = _step0(_article("§ 3") + _article("§ 2") + _article("§ 1"))
    for built in (forward, backward):
        ids = [s.provision_id for s in built.sources]
        assert ids == sorted(ids) == [i.provision_id for i in built.ledger.identities]
    strip = lambda s: s.model_dump(exclude={"node_sha256"})  # noqa: E731 - member bytes differ
    assert [strip(s) for s in forward.sources] == [strip(s) for s in backward.sources]


def test_a_flat_act_has_empty_stamps_and_no_outline() -> None:
    step0 = _step0(_article("§ 1") + _article("§ 2"))
    assert {s.stamp for s in step0.sources} == {Pair()}
    assert step0.structure.outline == ()


def test_another_acts_member_is_not_a_step_0() -> None:
    refusal = _refused(_enacting(STRUCTURED, refid="lov/2005-06-17-62"))
    assert [p.reason for p in refusal.problems] == [NOT_ENACTING]


def test_a_member_without_a_publication_date_mints_nothing() -> None:
    refusal = _refused(_enacting(STRUCTURED, published=None))
    assert [p.reason for p in refusal.problems] == [UNDATED]


def test_a_member_without_provisions_is_an_incomplete_source() -> None:
    refusal = _refused(_enacting("<p>Ingen paragrafer.</p>"))
    assert [p.reason for p in refusal.problems] == [EMPTY]


def test_a_label_printed_twice_refuses_step_0_and_names_both_nodes() -> None:
    refusal = _refused(_enacting(_article("§ 1") + _article("§ 1", name="§1") + _article("§ 2")))
    assert [(p.node, p.reason) for p in refusal.problems] == [("§1", DUPLICATE), ("§1", DUPLICATE)]


def test_a_label_the_data_name_contradicts_is_refused() -> None:
    refusal = _refused(_enacting(_article("§ 1") + _article("§ 2", name="§3")))
    assert [(p.node, p.reason) for p in refusal.problems] == [("§3", MISLABELLED)]


def test_an_unreadable_label_is_refused() -> None:
    refusal = _refused(_enacting(_article("§ 1") + _article("Paragraf en", name="x")))
    assert [(p.node, p.reason) for p in refusal.problems] == [("x", UNLABELLED)]


def test_a_roman_chapter_is_never_read_as_no_chapter() -> None:
    """``_enclosing`` stamps no roman data-name: the provision would look chapterless."""
    document = _enacting(_sec("del1", _sec("kapVII", _article("§ 1"))))
    assert (document.provisions[0].chapter, document.provisions[0].part) == (None, "del1")
    refusal = _refused(document)
    assert [(p.node, p.reason, p.detail) for p in refusal.problems] == [
        ("§1", UNREAD_SECTION, "kapVII")
    ]


@pytest.mark.parametrize("name", ["delii", "vedlegg1", "kapa", "§1-1", "kap1.1"])
def test_an_enclosing_section_outside_the_stamp_grammar_is_refused(name: str) -> None:
    refusal = _refused(_enacting(_sec(name, _article("§ 1"))))
    assert [(p.reason, p.detail) for p in refusal.problems] == [(UNREAD_SECTION, name)]


def test_a_nameless_enclosing_section_is_refused() -> None:
    refusal = _refused(_enacting(_sec("del1", _sec(None, _article("§ 1")))))
    assert [(p.reason, p.detail) for p in refusal.problems] == [(UNREAD_SECTION, None)]


def test_a_provision_nested_in_another_is_refused() -> None:
    """A section printed inside another section's article is not one of the act's own."""
    document = _enacting(_article("§ 1", inner=_article("§ 7")))
    refusal = _refused(document)
    assert ("§7", NESTED) in [(p.node, p.reason) for p in refusal.problems]


@pytest.mark.parametrize(
    "sections",
    [
        _sec("kap1", _sec("del1", _article("§ 1"))),  # a part inside a chapter
        _sec("del1", _sec("kap1", _sec("kap2", _article("§ 1")))),  # two chapters
        _sec("del1", _sec("del2", _article("§ 1"))),  # two parts
    ],
)
def test_a_misleading_hierarchy_is_refused_not_read_nearest_first(sections: str) -> None:
    refusal = _refused(_enacting(sections))
    assert [p.reason for p in refusal.problems] == [UNREAD_SECTION]


def test_a_chapter_under_two_parts_is_conflicting_outline() -> None:
    document = _enacting(
        _sec("del1", _sec("kap1", _article("§ 1"))) + _sec("del2", _sec("kap1", _article("§ 2")))
    )
    refusal = _refused(document)
    assert [p.reason for p in refusal.problems] == [OUTLINE]


def test_every_problem_is_listed_in_node_order() -> None:
    document = _enacting(
        _article("§ 1") + _article("§ 1") + _sec("kapIV", _article("§ 2")) + _article("x", "y")
    )
    refusal = _refused(document)
    assert [(p.node, p.reason) for p in refusal.problems] == [
        ("§1", DUPLICATE), ("§1", DUPLICATE), ("§2", UNREAD_SECTION), ("y", UNLABELLED),
    ]  # fmt: skip


# ------------------------------------------------------------------------ insertions


def test_a_chapter_title_states_the_inserted_chapter() -> None:
    document = _member(
        _p(INTRO)
        + _p("Nytt kapittel 16 skal lyde:")
        + _title("Kapittel 16. Nye regler")
        + _future("§16-1")
    )
    built = _one(document)
    assert isinstance(built, BuiltOperation)
    assert isinstance(built.operation, Insertion)
    assert built.operation.label == "§16-1"
    assert built.operation.placement == Pair(chapter="kap16")
    evidence = built.placement
    assert evidence is not None and evidence.stated == Pair(chapter="kap16")
    assert evidence.chapter_from == "title"
    assert evidence.title_sha256 == hashlib.sha256(b"Kapittel 16. Nye regler").hexdigest()
    assert evidence.sentence_sha256 == hashlib.sha256(b"Nytt kapittel 16 skal lyde:").hexdigest()


def test_a_chapter_sentence_without_a_title_states_its_chapter() -> None:
    document = _member(_p(INTRO) + _p("Nytt kapittel 16 skal lyde:") + _future("§16-1"))
    built = _one(document)
    assert isinstance(built, BuiltOperation) and built.placement is not None
    assert built.placement.chapter_from == "sentence"
    assert built.operation == Insertion(
        label="§16-1", proof=built.operation.proof, placement=Pair(chapter="kap16")
    )


def test_a_chapter_placed_in_a_part_states_both_stamps() -> None:
    document = _member(
        _p(INTRO)
        + _p("Nytt kapittel 16 plasseres i tredje del og skal lyde:")
        + _title("Kapittel 16. Nye regler")
        + _future("§16-1")
    )
    built = _one(document)
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Insertion)
    assert built.operation.placement == Pair(chapter="kap16", part="del3")


def test_a_roman_chapter_insertion_is_unresolved_not_chapterless() -> None:
    document = _member(
        _p(INTRO)
        + _p("Nytt kapittel VII skal lyde:")
        + _title("Kapittel VII. Nytt")
        + _future("§70")
    )
    built = _one(document)
    assert isinstance(built, Unbuilt)
    assert built.reason in {UNREAD_CHAPTER, TITLE_UNREAD}
    assert built.placement is not None and built.placement.stated is None


def test_an_unread_title_never_lets_the_previous_chapter_stand() -> None:
    document = _member(
        _p(INTRO)
        + _p("Nye kapitler 16 til 17 skal lyde:")
        + _title("Kapittel 16. En")
        + _future("§16-1")
        + _title("Kapittel XVII. To")
        + _future("§17-1")
    )
    first, second = _built(document)
    assert isinstance(first, BuiltOperation) and isinstance(first.operation, Insertion)
    assert first.operation.placement == Pair(chapter="kap16")
    assert isinstance(second, Unbuilt) and second.reason == TITLE_UNREAD


def test_several_chapters_without_a_title_each_are_unresolved() -> None:
    document = _member(
        _p(INTRO) + _p("Nye kapitler 16 til 17 skal lyde:") + _future("§16-1") + _future("§17-1")
    )
    assert {o.reason for o in _built(document) if isinstance(o, Unbuilt)} == {UNREAD_CHAPTER}


def test_a_rewritten_part_is_not_manufactured_into_insertions() -> None:
    """``Tredje del skal lyde:`` repeals what it leaves out; no operation states that."""
    document = _member(_p(INTRO) + _p("Tredje del skal lyde:") + _future("§101"))
    built = _one(document)
    assert isinstance(built, Unbuilt) and built.reason == PART_REWRITE


def test_an_inherited_stamp_of_the_amending_member_is_never_a_placement() -> None:
    """The member's own ``kap13`` encloses its instructions; it places nothing in the act."""
    document = _member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5a"), wrap="kap13")
    (operation,) = _events(document, ACT)
    assert operation.operation.new_provision is not None
    assert operation.operation.new_provision.chapter == "kap13"  # what _enclosing says
    built = _one(document)
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Insertion)
    assert built.operation.placement is None
    assert built.placement is not None
    assert built.placement.stated is None and built.placement.inherited == Pair(chapter="kap13")
    assert Enclosure(tag="section", css_class="section", name="kap13") in (
        built.placement.enclosures
    )


def test_a_plain_insertion_without_structure_states_no_pair() -> None:
    built = _one(_member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5a")))
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Insertion)
    assert built.operation.placement is None
    assert built.placement is not None and built.placement.inherited == Pair()


def test_an_insertion_headed_by_another_label_is_unresolved() -> None:
    built = _one(_member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5b")))
    assert isinstance(built, Unbuilt) and built.reason == UNHEADED


def test_an_undated_insertion_builds_nothing() -> None:
    built = _one(_member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5a"), KONGEN))
    assert isinstance(built, Unbuilt)
    assert built.reason.startswith("timing not proven: ")
    assert built.effective is None


def test_an_unattributed_operation_is_never_given_an_act() -> None:
    document = _member(_p("Ny § 5 a skal lyde:") + _future("§5a"), changes=())
    outcomes = [build_operation(e, {MEMBER: document}) for e in _events(document, None)]
    assert outcomes and all(
        isinstance(o, Unbuilt)
        and o.act is None
        and o.reason == "act not bound by the D7/phase-4B chain"
        for o in outcomes
    )


def test_an_operation_from_a_member_not_in_evidence_is_refused() -> None:
    document = _member(_p(INTRO) + _p("§ 9 oppheves."))
    (event,) = _events(document, ACT)
    built = build_operation(event, {})
    assert isinstance(built, Unbuilt) and built.reason == "member not in evidence"
    assert built.member_sha256 is None


# ------------------------------------------------------------------ other operations


def test_a_repeal_is_built_on_its_sentence_proof() -> None:
    built = _one(_member(_p(INTRO) + _p("§ 9 oppheves.")))
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Repeal)
    assert built.operation.label == "§9"
    proof = built.operation.proof
    assert proof.sentence_sha256 == hashlib.sha256(b"\xc2\xa7 9 oppheves.").hexdigest()
    assert (proof.effective, proof.member) == (EFFECTIVE, MEMBER)


def test_a_whole_replacement_carries_its_payload_digest() -> None:
    built = _one(_member(_p(INTRO) + _p("§ 9 skal lyde:") + _future("§9")))
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Replace)
    assert built.operation.label == "§9"
    assert len(built.operation.payload_sha256) == 64


def test_a_whole_replacement_headed_by_another_label_is_unresolved() -> None:
    built = _one(_member(_p(INTRO) + _p("§ 9 skal lyde:") + _future("§10")))
    assert isinstance(built, Unbuilt) and built.reason == UNHEADED


@pytest.mark.parametrize(
    ("sentence", "payload", "kind"),
    [
        ("§ 9 annet ledd skal lyde:", '<article class="legalP">Ny tekst.</article>', "ledd"),
        ("§ 9 første ledd annet punktum oppheves.", "", "punktum"),
        ("§ 9 annet ledd oppheves.", "", "ledd"),
    ],
)
def test_finer_operations_are_built_on_their_section(
    sentence: str, payload: str, kind: str
) -> None:
    built = _one(_member(_p(INTRO) + _p(sentence) + payload))
    assert isinstance(built, BuiltOperation) and isinstance(built.operation, Finer)
    assert (built.operation.label, built.operation.kind) == ("§9", kind)


def test_an_operation_b1_does_not_model_is_never_manufactured() -> None:
    built = _one(_member(_p(INTRO) + _p('I § 9 skal "gammel" erstattes med "ny".')))
    assert isinstance(built, Unbuilt) and built.reason.startswith(KIND)


def test_a_range_is_never_split_into_operations() -> None:
    outcomes = _built(_member(_p(INTRO) + _p("§§ 5 til 7 oppheves.")))
    assert outcomes and all(
        (isinstance(o, Unbuilt) and o.reason in {RANGE})
        or (isinstance(o, Unbuilt) and o.reason.startswith(KIND))
        for o in outcomes
    )


# ----------------------------------------------------------------------- candidates


def _candidates(document: Document, *acts: str | None, decisions: frozenset[str] = frozenset()):
    return build_candidates(_events(document, *acts), {document.member: document}, decisions)


def test_a_group_whose_every_operation_is_built_is_complete_and_executable() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves.") + _p("Ny § 3 a skal lyde:") + _future("§3a"))
    build = _candidates(document, ACT)
    (candidate,) = build.candidates
    assert (candidate.act, candidate.member, candidate.effective) == (ACT, MEMBER, EFFECTIVE)
    assert candidate.complete and candidate.gaps == ()
    step = candidate.executable()
    assert [r.label for r in step.repeals] == ["§3"]
    assert [i.label for i in step.insertions] == ["§3a"]


def test_a_group_with_an_unbuilt_operation_lists_it_and_forbids_execution() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves.") + _p('I § 9 skal "a" erstattes med "b".'))
    (candidate,) = _candidates(document, ACT).candidates
    assert not candidate.complete
    assert len(candidate.gaps) == 1 and candidate.gaps[0].startswith("operation not built: ")
    with pytest.raises(CandidateError, match="incomplete"):
        candidate.executable()


def test_a_mixed_act_member_gives_one_candidate_per_act() -> None:
    document = _member(
        _p(INTRO) + _p("§ 3 oppheves.") + _p(INTRO_OTHER) + _p("§ 4 oppheves."),
        changes=(ACT, OTHER_ACT),
    )
    build = _candidates(document, ACT, OTHER_ACT)
    assert [(c.act, [r.label for r in c.step.repeals]) for c in build.candidates] == [
        (ACT, ["§3"]), (OTHER_ACT, ["§4"]),
    ]  # fmt: skip
    assert all(c.complete for c in build.candidates)


def test_an_unattributed_operation_of_the_member_keeps_every_candidate_incomplete() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves."))
    stray = _member(_p("§ 8 oppheves."), changes=())
    events = _events(document, ACT) + [
        e.model_copy(update={"member": MEMBER}) for e in _events(stray, None)
    ]
    (candidate,) = build_candidates(events, {MEMBER: document}).candidates
    assert any(g.startswith(UNBOUND_IN_MEMBER) for g in candidate.gaps)


def test_an_undated_operation_of_the_same_act_and_member_keeps_the_candidate_incomplete() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves.") + _p("§ 4 oppheves."))
    events = _events(document, ACT)
    undated = events[1].timing.model_copy(update={"status": "pending", "effective": None})
    events[1] = events[1].model_copy(update={"timing": undated})
    (candidate,) = build_candidates(events, {MEMBER: document}).candidates
    assert any(g.startswith(UNDATED_IN_MEMBER) for g in candidate.gaps)
    assert len(build_candidates(events, {MEMBER: document}).unbuilt) == 1


def test_a_decision_member_states_units_no_step_represents() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves."))
    build = _candidates(document, ACT, decisions=frozenset({MEMBER}))
    (candidate,) = build.candidates
    assert UNITS in candidate.gaps


def test_a_commencement_only_member_is_listed_never_dropped() -> None:
    decision = "lti/2008/sf-20080307-0225.xml"
    document = _member(_p(INTRO) + _p("§ 3 oppheves."))
    build = _candidates(document, ACT, decisions=frozenset({decision}))
    assert [(g.member, g.reason) for g in build.member_gaps] == [(decision, UNITS)]


def test_an_operation_writing_the_acts_commencement_is_a_gap() -> None:
    document = _member(
        _p(INTRO)
        + _p("§ 9 skal lyde:")
        + _future("§9").replace("Teksten.", "Loven trer i kraft 1. januar 2009.")
    )
    (candidate,) = _candidates(document, ACT).candidates
    assert any(g.startswith(COMMENCEMENT_STATED) for g in candidate.gaps)


def test_candidates_do_not_depend_on_event_order() -> None:
    document = _member(
        _p(INTRO)
        + _p("§ 3 oppheves.")
        + _p("Ny § 3 a skal lyde:")
        + _future("§3a")
        + _p("§ 4 annet ledd oppheves.")
        + _p('I § 9 skal "a" erstattes med "b".')
    )
    events = _events(document, ACT, None)
    first = build_candidates(events, {MEMBER: document}).canonical_json()
    for seed in range(5):
        shuffled = list(events)
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - test permutation
        assert build_candidates(shuffled, {MEMBER: document}).canonical_json() == first


def test_a_reused_label_mints_a_new_identity() -> None:
    """``§ 3 oppheves`` and a new ``§ 3`` in one step: OD-5, a new identity, empty history."""
    step0 = _step0(_article("§ 1") + _article("§ 3"))
    document = _member(_p(INTRO) + _p("§ 3 oppheves.") + _p("Ny § 3 skal lyde:") + _future("§3"))
    (candidate,) = _candidates(document, ACT).candidates
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert result.refusals == ()
    new = inserted_id(ACT, MEMBER, EFFECTIVE, "§3")
    assert result.ledger.holder("§3") is not None
    assert result.ledger.holder("§3").provision_id == new  # type: ignore[union-attr]
    old = result.ledger.get(enacted_id(ACT, ENACTING, "§3"))
    assert old is not None and not old.live


def test_two_insertions_of_one_label_are_built_and_refused_by_the_step() -> None:
    step0 = _step0(_article("§ 1"))
    document = _member(
        _p(INTRO)
        + _p("Ny § 5 skal lyde:")
        + _future("§5")
        + _p("Ny § 5 skal lyde:")
        + _future("§5")
    )
    (candidate,) = _candidates(document, ACT).candidates
    assert len(candidate.step.insertions) == 2
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert {r.reason for r in result.refusals} == {"label collision"}
    assert result.ledger.canonical_json() == step0.ledger.canonical_json()


def test_a_built_chapter_insertion_enters_the_outline_with_its_proof() -> None:
    step0 = _step0()
    document = _member(
        _p(INTRO)
        + _p("Nytt kapittel 16 plasseres i tredje del og skal lyde:")
        + _title("Kapittel 16. Nye regler")
        + _future("§16-1")
    )
    (candidate,) = _candidates(document, ACT).candidates
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert result.refusals == () and result.structure is not None
    entry = result.structure.enclosing("kap16")
    assert entry is not None and entry.part == "del3"
    assert entry.proof.member == MEMBER and entry.proof.effective == EFFECTIVE


def test_an_unread_placement_keeps_the_candidate_from_executing() -> None:
    document = _member(
        _p(INTRO)
        + _p("Nytt kapittel VII skal lyde:")
        + _title("Kapittel VII. Nytt")
        + _future("§70")
    )
    build = _candidates(document, ACT)
    assert build.candidates == ()  # nothing built, so no step to execute at all
    assert [u.reason for u in build.unbuilt] == [TITLE_UNREAD]
    mixed = _member(
        _p(INTRO) + _p("§ 3 oppheves.") + _p("Nytt kapittel VII skal lyde:")
        + _title("Kapittel VII. Nytt") + _future("§70")
    )  # fmt: skip
    (candidate,) = _candidates(mixed, ACT).candidates
    assert candidate.step.insertions == () and not candidate.complete
    assert any(TITLE_UNREAD in g for g in candidate.gaps)


def test_no_built_operation_carries_a_fabricated_date_or_digest() -> None:
    document = _member(
        _p(INTRO)
        + _p("§ 3 oppheves.")
        + _p("Ny § 3 a skal lyde:")
        + _future("§3a")
        + _p("§ 4 annet ledd oppheves.")
    )
    events = _events(document, ACT)
    sentences = {e.operation.id: e.operation.instruction for e in events}
    for outcome in (build_operation(e, {MEMBER: document}) for e in events):
        assert isinstance(outcome, BuiltOperation)
        proof = outcome.operation.proof
        assert proof.effective == EFFECTIVE and proof.known_from == date(2008, 3, 7)
        assert proof.member_sha256 == document.sha256
        expected = hashlib.sha256(sentences[proof.op_id].encode()).hexdigest()
        assert proof.sentence_sha256 == expected


# --------------------------------------------------- research only: nothing imports it


@pytest.mark.structural
def test_no_production_module_imports_the_evidence_builders() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "lovspor"
    importers = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if p.name != "step_evidence.py" and "step_evidence" in p.read_text(encoding="utf-8")
    ]
    assert importers == []


def _chapter_insertion(sentence: str, title: str, key: str) -> Document:
    return _member(_p(INTRO) + _p(sentence) + _title(title) + _future(key))


def test_an_insertion_into_a_known_chapter_takes_the_outlines_part() -> None:
    step0 = _step0()
    document = _chapter_insertion("Nytt kapittel 2 skal lyde:", "Kapittel 2. Igjen", "§3a")
    (candidate,) = _candidates(document, ACT).candidates
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert result.refusals == () and result.structure is not None
    pid = inserted_id(ACT, MEMBER, EFFECTIVE, "§3a")
    assert result.structure.live_map()[pid] == Pair(chapter="kap2", part="del1")


def test_an_unknown_chapter_without_a_part_stays_unresolved_in_an_act_with_parts() -> None:
    step0 = _step0()
    document = _chapter_insertion("Nytt kapittel 16 skal lyde:", "Kapittel 16. Nytt", "§16-1")
    (candidate,) = _candidates(document, ACT).candidates
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert result.structure is not None
    pid = inserted_id(ACT, MEMBER, EFFECTIVE, "§16-1")
    assert result.structure.live_map()[pid] is None  # part unknown: never guessed
    assert result.structure.enclosing("kap16") is None


def test_a_known_chapter_stated_under_another_part_is_refused_with_s_unchanged() -> None:
    step0 = _step0()
    document = _chapter_insertion(
        "Nytt kapittel 2 plasseres i tredje del og skal lyde:", "Kapittel 2. Flyttet", "§3a"
    )
    (candidate,) = _candidates(document, ACT).candidates
    result = apply_step(step0.ledger, candidate.executable(), step0.structure)
    assert [r.reason for r in result.refusals] == ["placement conflicts with the act outline"]
    assert result.structure is not None
    assert result.structure.canonical_json() == step0.structure.canonical_json()


_SEEDED = """
import sys
sys.path.insert(0, sys.argv[1])
import test_legaltime_step_evidence as t
document = t._member(
    t._p(t.INTRO) + t._p("§ 3 oppheves.") + t._p("Ny § 3 a skal lyde:") + t._future("§3a")
    + t._p("Nytt kapittel 16 skal lyde:") + t._title("Kapittel 16. Nytt") + t._future("§16-1")
)
build = t.build_candidates(t._events(document, t.ACT, None), {t.MEMBER: document})
print(t._step0().canonical_json() + build.canonical_json())
"""


def test_builds_do_not_depend_on_the_hash_seed() -> None:
    """I7: the same pins give the same bytes, whatever Python's string hashing."""
    here = str(Path(__file__).resolve().parent)
    outputs = {
        subprocess.run(
            [sys.executable, "-c", _SEEDED, here], capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "4242")
    }  # fmt: skip
    assert len(outputs) == 1 and outputs.pop()


def test_a_rename_is_built_through_the_b2_adapter() -> None:
    document = _member(_p(INTRO) + _p("Nåværende § 5 blir § 6.") + _p("Nåværende § 7 blir § 7."))
    events = _events(document, ACT)
    outcomes = [build_operation(e, {MEMBER: document}) for e in events]
    renamed = [o for o in outcomes if isinstance(o, BuiltOperation)]
    assert [(o.operation.source, o.operation.target) for o in renamed] == [("§5", "§6")]  # type: ignore[union-attr]
    refused = [o.reason for o in outcomes if isinstance(o, RenameRefusal)]
    assert refused == ["rename to the same label"]


def test_a_part_the_structure_names_but_does_not_read_is_unresolved() -> None:
    document = _member(
        _p(INTRO) + _p("Nytt kapittel 16 plasseres i siste del og skal lyde:") + _future("§16-1")
    )
    built = _one(document)
    assert isinstance(built, Unbuilt) and built.reason == UNREAD_PART


def test_a_section_operation_without_a_section_is_never_built() -> None:
    document = _member(_p(INTRO) + _p("§ 9 oppheves."))
    (event,) = _events(document, ACT)
    blind = event.operation.model_copy(update={"address": Address()})
    built = build_operation(event.model_copy(update={"operation": blind}), {MEMBER: document})
    assert isinstance(built, Unbuilt) and built.reason == NO_SECTION


def test_a_source_whose_node_does_not_rebuild_its_proof_does_not_validate() -> None:
    step0 = _step0()
    relabelled = step0.sources[0].model_copy(update={"printed_label": "§ 999"})
    with pytest.raises(ValidationError, match="node digest"):
        step0.model_copy(update={"sources": (relabelled, *step0.sources[1:])})
    renamed = step0.sources[0].model_copy(update={"label": "§999"})
    with pytest.raises(ValidationError, match="node digest"):
        step0.model_copy(update={"sources": (renamed, *step0.sources[1:])})


def test_a_candidate_is_only_its_own_group_and_its_own_evidence() -> None:
    document = _member(_p(INTRO) + _p("§ 3 oppheves.") + _p("§ 4 oppheves."))
    (candidate,) = _candidates(document, ACT).candidates
    with pytest.raises(ValidationError, match="its own group"):
        candidate.model_copy(update={"effective": date(2009, 1, 1)})
    with pytest.raises(ValidationError, match="one built operation"):
        candidate.model_copy(update={"evidence": candidate.evidence[:1]})


def test_a_roman_chapter_beside_a_read_part_is_never_read_as_directly_under_the_part() -> None:
    document = _member(
        _p(INTRO) + _p("Nytt kapittel VII plasseres i tredje del og skal lyde:") + _future("§70")
    )
    built = _one(document)
    assert isinstance(built, Unbuilt) and built.reason == UNREAD_CHAPTER
    assert built.placement is not None and built.placement.stated is None


def test_a_nynorsk_chapter_range_read_as_its_first_chapter_is_unresolved() -> None:
    """``Nye kapittel 16 til 17``: ``chapter_key`` reads kap16 for every provision carried."""
    document = _member(
        _p(INTRO) + _p("Nye kapittel 16 til 17 skal lyde:") + _future("§16-1") + _future("§17-1")
    )
    outcomes = _built(document)
    assert len(outcomes) == 2
    assert {o.reason for o in outcomes if isinstance(o, Unbuilt)} == {UNREAD_CHAPTER}
    assert all(isinstance(o, Unbuilt) for o in outcomes)


def test_an_insertion_whose_printed_label_only_contains_the_label_is_unresolved() -> None:
    """``section_key`` searches a substring: ``§ 5 junk`` must not pass as ``§5``."""
    built = _one(_member(_p(INTRO) + _p("Ny § 5 skal lyde:") + _future("§5junk", "§ 5 junk")))
    assert isinstance(built, Unbuilt) and built.reason == UNHEADED


def test_an_insertion_whose_data_name_is_not_its_label_is_unresolved() -> None:
    built = _one(_member(_p(INTRO) + _p("Ny § 5 skal lyde:") + _future("§5x", "§ 5")))
    assert isinstance(built, Unbuilt) and built.reason == UNHEADED


@pytest.mark.parametrize(("label", "name"), [("§ 1 garbage", "§1garbage"), ("§ 1", "§1 garbage")])
def test_a_step_0_node_must_print_exactly_its_label(label: str, name: str) -> None:
    refusal = _refused(_enacting(_article(label, name=name) + _article("§ 2")))
    assert [p.reason for p in refusal.problems] == [MISLABELLED]
