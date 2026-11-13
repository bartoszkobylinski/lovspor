"""ADR-0023 Phase B2: the research adapter from Lovtidend rename sentences to B1 steps (#861).

Members are built inline and run through the engine's own chain (``interpret``, nesting,
``resolve_timings``) by ``run._events_of``: attribution and timing are the engine's, never
the test's. Sentences are shaped like the pinned ones (straffeloven 2008/2009,
atomenergiloven 2007); the pinned members themselves are in
``tests/integration/test_legaltime_rename_research_pinned.py``.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.commencement import Decisions, Event
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.identity import LabelProof, enacted_id, mint_enacted
from lovspor.legaltime.rename_research import (
    Research,
    adapt,
    parse_rename,
)
from lovspor.legaltime.run import _events_of
from lovspor.legaltime.step import apply_step

MEMBER = "lti/2008/nl-20080307-004.xml"
STRAFFELOVEN = "lov/2005-05-20-28"
AML = "lov/2005-06-17-62"
INTRO = "Lov 20. mai 2005 nr. 28 om straff endres slik:"
INTRO_AML = "Lov 17. juni 2005 nr. 62 om arbeidsmiljø, arbeidstid og stillingsvern mv. endres slik:"
STRAKS = "Loven trer i kraft straks."
KONGEN = "Loven trer i kraft fra den tid Kongen bestemmer."


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _future(key: str, heading: str = "Ikraftsetting", printed: str | None = None) -> str:
    label = printed or f"§ {key[1:]}"
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        '<span class="futureLegalArticleHeader">'
        f'<span class="legalArticleValue">{label}</span>'
        f'<span class="legalArticleTitle">{heading}</span></span>'
        '<article class="legalP">Teksten.</article></article>'
    )


def _member(
    part_one: str, part_two: str = STRAKS, changes: tuple[str, ...] = (STRAFFELOVEN,)
) -> Document:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    body = (
        f'<section class="section"><h2>I</h2>{part_one}</section>'
        f'<section class="section"><h2>II</h2>{_p(part_two)}</section>'
    )
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2008-03-07-4</dd>'
        '<dd class="dateInForce">2008-03-07</dd>'
        '<dd class="dateOfPublication">2008-03-07 14:45</dd>'
        '<dd class="title">Lov om endringer i straffeloven 2005</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )
    return parse_document(xml.encode(), MEMBER)


def _events(document: Document, *acts: str | None) -> list[Event]:
    events: list[Event] = []
    for act in acts or (STRAFFELOVEN, None):
        events += _events_of(document, act, Decisions(documents=()), len(events))
    return events


def _run(part_one: str, part_two: str = STRAKS, **kwargs: object) -> Research:
    document = _member(part_one, part_two, **kwargs)  # type: ignore[arg-type]
    return adapt(_events(document, STRAFFELOVEN, AML, None), {MEMBER: document})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reasons(research: Research) -> list[str]:
    return [r.reason for r in research.refusals]


# ------------------------------------------------------------------ the pure parser (R3)


@pytest.mark.parametrize(
    ("sentence", "source", "target", "replacement"),
    [
        ("Nåværende § 21 blir ny § 22.", "§21", "§22", False),
        ("Noverande § 3 blir § 4.", "§3", "§4", False),
        ("Gjeldende § 5 blir ny § 6", "§5", "§6", False),
        ("Tidligere § 7 blir § 8.", "§7", "§8", False),
        ("Nåværende § 402 blir § 412 og skal lyde:", "§402", "§412", True),
        ("Nåværende § 9 A-1 blir § 9 A-2.", "§9A-1", "§9A-2", False),
        ("Nåværende § 14 b blir § 14 c, og skal lyde slik:", "§14b", "§14c", True),
        ("§ 44 blir ny § 43 og skal lyde:", "§44", "§43", True),
        ("§ 101 i lov 20. mai 2005 nr. 28 blir § 401, og skal lyde slik:", "§101", "§401", True),
    ],
)
def test_the_two_r3_shapes_are_read_with_engine_section_keys(
    sentence: str, source: str, target: str, replacement: bool
) -> None:
    parsed = parse_rename(sentence)
    assert parsed.refusal is None
    assert (parsed.source, parsed.target, parsed.replacement) == (source, target, replacement)


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        ("De delene av § 102 i lov 20. mai 2005 nr. 28 som ikke allerede er satt i kraft, "
         "blir § 402.", "partial rename: extent not in the source"),
        ("Nåværende §§ 5 og 6 blir §§ 6 og 7.", "range or several sections: unsupported"),
        ("Nåværende §§ 5 til 7 blir §§ 6 til 8.", "range or several sections: unsupported"),
        ("Nåværende § 5 og § 6 blir § 7.", "rename outside the grammar"),
        ("Nåværende § 5 blir § 6 og § 7.", "rename outside the grammar"),
        ("nåværende § 5 blir § 6.", "rename outside the grammar"),
        ("Nåværende § 5 blir § 6;", "rename outside the grammar"),
        ("Nåværende § 20 blir ny § 19, som skal lyde:", "rename outside the grammar"),
        ("Nåværende § 5 a blir § 5a.", "rename to the same label"),
        ("Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:",
         "placement incomplete: no structure map S"),
        ("Nåværende § 7 blir § 8 under kapittel 3.", "placement incomplete: no structure map S"),
    ],
)  # fmt: skip
def test_anything_else_is_a_structured_refusal(sentence: str, reason: str) -> None:
    assert parse_rename(sentence).refusal == reason


def test_a_placement_clause_is_recorded_not_dropped() -> None:
    parsed = parse_rename("Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:")
    assert parsed.placement == "som innleder tredje del"
    assert (parsed.source, parsed.target) == ("§401", "§411")


# ------------------------------------------------------- source-backed adapter output


def test_a_bound_dated_rename_becomes_a_step_with_its_full_proof() -> None:
    sentence = "Nåværende § 401 blir § 411."
    document = _member(_p(INTRO) + _p(sentence))
    research = adapt(_events(document), {MEMBER: document})
    assert research.refusals == ()
    (candidate,) = research.steps
    assert (candidate.act, candidate.member, candidate.effective) == (
        STRAFFELOVEN, MEMBER, date(2008, 3, 7),
    )  # fmt: skip
    assert candidate.complete
    (rename,) = candidate.step.renames
    assert (rename.source, rename.target, rename.replacement) == ("§401", "§411", None)
    proof = rename.proof
    assert proof.instrument == "lov/2008-03-07-4"
    assert proof.member_sha256 == document.sha256
    assert proof.sentence_sha256 == _sha(sentence)
    assert (proof.effective, proof.known_from) == (date(2008, 3, 7), date(2008, 3, 7))
    assert proof.op_id.startswith("lov/2008-03-07-4#")


def test_a_replacement_headed_by_the_target_is_one_rename_with_its_payload() -> None:
    research = _run(
        _p(INTRO) + _p("Nåværende § 402 blir § 412 og skal lyde:") + _future("§412", "Endringer")
    )
    (candidate,) = research.steps
    (rename,) = candidate.step.renames
    assert rename.replacement is not None
    assert rename.replacement.headed == "§412"
    assert len(rename.replacement.payload_sha256) == 64


@pytest.mark.parametrize(
    "payload",
    [_future("§413", "Feil"), _future("§412", printed="§ 4 1 2 x"), ""],
)
def test_a_replacement_not_headed_by_the_target_refuses_both_effects(payload: str) -> None:
    research = _run(_p(INTRO) + _p("Nåværende § 402 blir § 412 og skal lyde:") + payload)
    assert research.steps == ()
    assert _reasons(research) == ["replacement not headed by the target label"]


def test_a_payload_without_a_replacement_clause_is_refused() -> None:
    research = _run(_p(INTRO) + _p("Nåværende § 402 blir § 412.") + _future("§412"))
    assert _reasons(research) == ["payload without a replacement clause"]


def test_a_citation_of_the_bound_act_is_accepted_and_another_act_is_refused() -> None:
    good = _run(_p(INTRO) + _p("§ 101 i lov 20. mai 2005 nr. 28 blir § 401."))
    assert [r.target for s in good.steps for r in s.step.renames] == ["§401"]
    bad = _run(_p(INTRO) + _p("§ 101 i lov 17. juni 2005 nr. 62 blir § 401."))
    assert _reasons(bad) == ["citation names another act than the bound one"]


def test_a_rename_no_chain_binds_is_refused_not_guessed() -> None:
    """2008: "I straffeloven 2005 tredje del gjøres følgende endringer:" binds no act."""
    research = _run(
        _p("I straffeloven 2005 tredje del gjøres følgende endringer:")
        + _p("Nåværende § 101 blir § 401.")
    )
    (refusal,) = research.refusals
    assert (refusal.act, refusal.reason) == (None, "act not bound by the D7/phase-4B chain")
    assert research.steps == ()


def test_a_placement_refuses_the_whole_rename_and_keeps_the_clause() -> None:
    sentence = "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:"
    research = _run(_p(INTRO) + _p(sentence) + _future("§411"))
    (refusal,) = research.refusals
    assert refusal.reason == "placement incomplete: no structure map S"
    assert refusal.placement == "som innleder tredje del"
    assert refusal.sentence_sha256 == _sha(sentence)
    assert research.steps == ()


def test_an_undated_rename_is_recorded_outside_every_step() -> None:
    research = _run(_p(INTRO) + _p("Nåværende § 21 blir ny § 22."), part_two=KONGEN)
    (refusal,) = research.refusals
    assert refusal.reason.startswith("timing not proven: pending")
    assert (refusal.effective, refusal.timing_status) == (None, "pending")
    assert research.steps == ()


def test_a_mixed_act_member_gives_one_step_per_act() -> None:
    body = _p(INTRO) + _p("Nåværende § 5 blir § 6.") + _p(INTRO_AML) + _p("Nåværende § 7 blir § 8.")
    research = _run(body, changes=(STRAFFELOVEN, AML))
    assert sorted((s.act, s.step.renames[0].source) for s in research.steps) == [
        (STRAFFELOVEN, "§5"), (AML, "§7"),
    ]  # fmt: skip


def test_a_step_with_an_operation_the_adapter_does_not_represent_is_incomplete() -> None:
    research = _run(_p(INTRO) + _p("Nåværende § 5 blir § 6.") + _p("§ 9 oppheves."))
    (candidate,) = research.steps
    assert not candidate.complete
    assert len(candidate.gaps) == 1
    assert candidate.gaps[0].startswith("operation not represented: lov/2008-03-07-4#")
    assert candidate.gaps[0].endswith("(repeal_section)")


def test_a_refused_rename_leaves_its_step_incomplete() -> None:
    body = (
        _p(INTRO)
        + _p("Nåværende § 5 blir § 6.")
        + _p("Nåværende § 7 blir § 8, som skal lyde:")
        + _future("§8")
    )
    (candidate,) = _run(body).steps
    assert not candidate.complete
    assert [g.split(":")[0] for g in candidate.gaps] == ["rename refused"]


def test_a_member_that_is_a_decision_for_the_act_states_units_so_its_step_is_incomplete() -> None:
    document = _member(_p(INTRO) + _p("Nåværende § 5 blir § 6."))
    research = adapt(_events(document), {MEMBER: document}, decision_members=frozenset({MEMBER}))
    (candidate,) = research.steps
    assert candidate.gaps == ("member states commencement units for the act",)


def test_an_event_whose_member_is_not_in_the_evidence_is_refused() -> None:
    document = _member(_p(INTRO) + _p("Nåværende § 5 blir § 6."))
    research = adapt(_events(document), {})
    assert _reasons(research) == ["member not in evidence"]


def test_ledd_and_punktum_renumberings_are_not_section_renames() -> None:
    research = _run(_p(INTRO) + _p("Nåværende annet til fjerde ledd blir tredje til femte ledd."))
    assert research == Research(steps=(), refusals=())


# --------------------------------------------- the cycle, executed only through B1


def test_the_atomenergiloven_cycle_is_one_step_that_b1_accepts_simultaneously() -> None:
    body = _p(INTRO) + "".join(
        _p(f"Nåværende § {s} blir ny § {t}.") for s, t in ((21, 22), (22, 23), (23, 25), (25, 21))
    )
    (candidate,) = _run(body).steps
    assert candidate.complete
    ledger = mint_enacted(STRAFFELOVEN, "lti/2005/nl-20050520-028.xml", date(2005, 5, 20), [
        (f"§{n}", _start0(f"§{n}")) for n in (21, 22, 23, 25)
    ])  # fmt: skip
    result = apply_step(ledger, candidate.step)
    assert result.refusals == ()
    held = {k: v.rsplit("|", 1)[1] for k, v in result.ledger.live_map().items()}
    assert held == {"§22": "§21", "§23": "§22", "§25": "§23", "§21": "§25"}


def _start0(label: str) -> LabelProof:
    return LabelProof(
        instrument=STRAFFELOVEN, op_id=f"{STRAFFELOVEN}#enacted:{label}",
        member="lti/2005/nl-20050520-028.xml", member_sha256="a" * 64,
        sentence_sha256="b" * 64, effective=None, known_from=date(2005, 5, 20),
        label_minted_at=date(2005, 5, 20),
    )  # fmt: skip


def test_enacted_ids_are_untouched_by_the_adapter() -> None:
    assert enacted_id(STRAFFELOVEN, "lti/2005/nl-20050520-028.xml", "§21").endswith("|§21")


# ------------------------------------------------------------------- determinism (I6, I7)


_CYCLE = [(21, 22), (22, 23), (23, 25), (25, 21)]


def _cycle_member(order: list[tuple[int, int]]) -> Document:
    return _member(_p(INTRO) + "".join(_p(f"Nåværende § {s} blir ny § {t}.") for s, t in order))


def test_reversed_events_give_byte_identical_output() -> None:
    document = _cycle_member(_CYCLE)
    events = _events(document)
    once = adapt(events, {MEMBER: document}).canonical_json()
    assert adapt(list(reversed(events)), {MEMBER: document}).canonical_json() == once


def test_permuted_document_order_gives_one_projection() -> None:
    """Op ids follow document position (I6 drops them); every other field is identical.

    A permuted member is other bytes, so its own sha256 differs, and is the one field set
    aside here: it is the member's true digest, not something the adapter derives.
    """
    seen = set()
    for order in (_CYCLE, list(reversed(_CYCLE)), _CYCLE[2:] + _CYCLE[:2]):
        document = _cycle_member(order)
        projection = adapt(_events(document), {MEMBER: document}).projection()
        seen.add(projection.replace(document.sha256, "<member sha256>"))
    assert len(seen) == 1


def test_output_is_byte_identical_across_hash_seeds() -> None:
    here = str(Path(__file__).parent)
    script = (
        f"import sys; sys.path.insert(0, {here!r})\n"
        "import test_legaltime_rename_research as t\n"
        "d = t._cycle_member(t._CYCLE)\n"
        "print(t.adapt(t._events(d), {t.MEMBER: d}).canonical_json())\n"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "4242")
    }  # fmt: skip
    assert len(outputs) == 1
    assert json.loads(outputs.pop())["steps"]


# ----------------------------------------------------- research only: nothing imports it


@pytest.mark.structural
def test_no_production_module_imports_the_research_adapter() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "lovspor"
    importers = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if p.name != "rename_research.py" and "rename_research" in p.read_text(encoding="utf-8")
    ]
    assert importers == []
