"""Chapter placement must survive the evidence → commencement wiring (#744)."""

from datetime import date

import pytest

from lovspor.legaltime.archive import Evidence
from lovspor.legaltime.document import Document, Provision, parse_document
from lovspor.legaltime.run import chapter_map, events_for
from tests.unit.test_legaltime_phase2_commencement import LAW, _decision, _law
from tests.unit.test_legaltime_phase3_instructions import _future, _p, _title

ACT = "lov/2019-06-14-99"


def _insertion(act: str, chapter: int, key: str) -> Document:
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2021-06-18-1</dd>'
        '<dd class="dateInForce">Kongen bestemmer</dd></dl></header><main>'
        '<section class="section"><h2>I</h2>'
        + _p(f"I {act} gjøres følgende endringer:")
        + _p(f"Nytt kapittel {chapter} skal lyde:")
        + _title(f"Kapittel {chapter}. Prøving")
        + _future(key)
        + "</section></main></body></html>"
    )
    return parse_document(xml.encode(), f"lti/insert-{chapter}.xml")


def _evidence(*amenders: Document, decisions: tuple[Document, ...] = ()) -> Evidence:
    source = _law("")
    baseline = source.model_copy(
        update={
            "header": source.header.model_copy(update={"refid": ACT}),
            "provisions": (Provision(key="§1", label="§ 1", heading="", ledd=(), chapter="kap1"),),
        }
    )
    return Evidence(act=ACT, archives=(), baseline=baseline, amenders=amenders, decisions=decisions)


def test_chapter_map_includes_pending_insertions_only_for_the_target_act() -> None:
    """chapter_map's ADR-0020 Decision 3a contract uses text existence, not force dates."""
    target = _insertion("lov 14. juni 2019 nr. 99 om prøving", 9, "§9-1")
    foreign = _insertion("lov 20. mai 2005 nr. 28 om straff", 16, "§101")
    evidence = _evidence(target, foreign)
    assert chapter_map(evidence) == {"§1": "kap1", "§9-1": "kap9"}
    assert evidence.baseline.provisions[0].chapter == "kap1"


@pytest.mark.parametrize("placed", [True, False])
def test_events_resolve_chapter_exceptions_using_the_baseline_map(placed: bool) -> None:
    law = _law("Loven trer i kraft fra den tid Kongen bestemmer.")
    decision = _decision(
        "for/2021-01-01-1",
        "Delt ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",
        "2021-01-01",
        "Følgende trer i kraft 1. januar 2021: Endringsloven del I med unntak av kapittel 2.",
        (LAW,),
    )
    evidence = _evidence(law, decisions=(decision,))
    if not placed:
        evidence = evidence.model_copy(
            update={"baseline": evidence.baseline.model_copy(update={"provisions": ()})}
        )
    events = events_for(evidence)
    assert len(events) == 1
    assert events[0].operation.address.section == "§1"
    timing = events[0].timing
    if placed:
        assert (timing.status, timing.effective) == ("dated", date(2021, 1, 1))
    else:
        assert timing.status == "unresolved"
        assert timing.effective is None
