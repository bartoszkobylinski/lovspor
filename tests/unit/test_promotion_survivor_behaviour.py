"""Observable edge cases from the promotion mutation report."""

from datetime import date

import pytest

from lovspor.promotion.corpus import CentralEntry, LocalManifest
from lovspor.promotion.evidence import EvidenceSubject, evidence_file, target_index
from lovspor.promotion.models import ExtractedRegulation
from lovspor.promotion.relations import Unresolved, resolve, short_name_key, stated_relations
from lovspor.promotion.stated_dates import stated_ikraft, stated_vedtatt


def regulation(block: str = "", body: str = "") -> ExtractedRegulation:
    return ExtractedRegulation(title="Forskrift om slam", identification_block=block, body=body)


@pytest.mark.parametrize("continuation", ["iOslo gjelder FOR-2001-07-01-55", "1. juli 2001 nr. 55"])
def test_continuation_uses_only_the_first_character(continuation: str) -> None:
    prefix = (
        "Denne forskriften opphever forskrift"
        if continuation[0].isdigit()
        else "Denne forskriften opphever"
    )
    [found] = stated_relations(regulation(body=f"{prefix}\n{continuation}"))
    assert found.evidence == f"{prefix} {continuation}"


def test_uncued_citation_does_not_parse_an_unbounded_number() -> None:
    # A citation is ignored before target conversion, even for malformed input.
    assert stated_relations(regulation(body="FOR-2001-07-01-" + "9" * 5000)) == ()


def test_colon_cue_can_touch_its_target() -> None:
    [found] = stated_relations(regulation("Hjemmel:LOV-1981-03-13-6"))
    assert found.kind == "hjemmel"


def test_a_date_without_number_retains_its_form() -> None:
    [found] = stated_relations(regulation("Hjemmel: lov 13. mars 1981"))
    assert found.target.form == "dated"
    assert found.target.number is None


def test_invalid_calendar_date_with_number_cannot_resolve() -> None:
    [found] = stated_relations(regulation("Hjemmel: LOV-1981-02-31-6"))
    result = resolve(found, target_index({}, LocalManifest()), "lf-20200101-1")
    assert result.target is None
    assert result.unresolved == Unresolved.NO_NUMBER


def test_short_name_drops_all_section_references_and_normalizes_hyphen() -> None:
    assert short_name_key("Plan -og bygningsloven § 1 § 2") == "plan- og bygningsloven"


def test_evidence_retains_self_reference_reason() -> None:
    doc_id = "sf-20010701-55"
    index = target_index(
        {doc_id: CentralEntry(status="current", slug="slam", markdown_path="forskrifter/slam.md")},
        LocalManifest(),
    )
    subject = EvidenceSubject(
        doc_id=doc_id,
        version=1,
        content_hash="a" * 64,
        regulation=regulation(body="Opphever FOR-2001-07-01-55."),
    )
    [relation] = evidence_file(subject, index).relations
    assert relation.target is None
    assert relation.unresolved == Unresolved.SELF_REFERENCE


@pytest.mark.parametrize("title", [None, "", "Forskrift (forurensningsloven)"])
def test_only_laws_supply_short_names(title: str | None) -> None:
    central = {
        "sf-20010701-55": CentralEntry(
            status="current", slug="slam", markdown_path="forskrifter/slam.md", title=title
        ),
        "nl-19810313-6": CentralEntry(
            status="current", slug="lov", markdown_path="lover/lov.md", title=None
        ),
        "nl-19810313-7": CentralEntry(status="current", slug="missing", markdown_path=None),
    }
    index = target_index(central, LocalManifest())
    assert index.by_short_name == {}
    assert len(index.by_lovdata) == 2


@pytest.mark.parametrize("in_block", [True, False])
def test_enactment_evidence_flattens_line_breaks(in_block: bool) -> None:
    clause = "Forskriften er vedtatt av\nkommunestyret 12. desember 2019"
    found = stated_vedtatt(
        regulation(block=clause if in_block else "", body="" if in_block else clause)
    )
    assert found.status == "stated"
    assert found.value == date(2019, 12, 12)
    [statement] = found.statements
    expected = clause.replace("\n", " ")
    assert statement.evidence == (
        expected.removeprefix("Forskriften er ") if in_block else expected
    )


def test_phrase_stops_at_first_of_multiple_sentence_ends() -> None:
    found = stated_ikraft(regulation(body="Trer i kraft straks. Neste setning. Siste setning."))
    assert found.status == "text"
    assert found.text == "straks"
    assert found.statements[0].evidence == "Trer i kraft straks"


def test_empty_statement_is_absent_but_retains_evidence() -> None:
    found = stated_ikraft(regulation("Ikrafttredelse: ."))
    assert found.status == "absent"
    [statement] = found.statements
    assert statement.date is None
    assert statement.text is None
    assert statement.evidence == "Ikrafttredelse:"
