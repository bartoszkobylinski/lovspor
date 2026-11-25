"""A ``§`` link names its act; the slug window does not get a vote (#1034).

Found on åndsverkloven § 81: ``kan lempes etter [lov 13. juni 1969 nr. 26]
(lov/1969-06-13-26) om skadeserstatning [§ 5-2](lov/1969-06-13-26/§5-2)`` was
reported as ``§ 5-2 not found in 'åndsverkloven'``. The prose names the act by
date and number, never by slug, so the window fell back to the act being read,
and a real citation was handed to the AI as a verified denial.
"""

from pathlib import Path

import pytest

from lovspor.act_links import _ACT_LINK, KnownActs, doc_id_of, known_acts
from lovspor.mcp import CorpusReader, SectionIndex, _extract_cross_references
from tests.unit.test_mcp import _record, _seed_corpus

_PARAGRAPH_81 = (
    "## Kapittel 5. Straff og erstatning\n\n"
    "### § 81. Vederlag og erstatning\n\n"
    "Bestemmelsene gjelder ikke bruk som nevnt i [§ 3](lov/2018-06-15-40/§3) tredje ledd. "
    "Ansvar kan lempes etter [lov 13. juni 1969 nr. 26](lov/1969-06-13-26) om "
    "skadeserstatning [§ 5-2](lov/1969-06-13-26/§5-2).\n\n"
    "### § 3. Definisjoner\n\nTekst.\n"
)
_SKADESERSTATNING = "## Kapittel 5.\n\n### § 5-2. Lemping\n\nTekst.\n"


def _corpus(
    tmp_path: Path, body: str = _PARAGRAPH_81, target: str = _SKADESERSTATNING
) -> CorpusReader:
    _seed_corpus(
        tmp_path,
        {
            "nl-20180615-040": _record(slug="åndsverkloven", title="Åndsverkloven"),
            "nl-19690613-026": _record(slug="skadeserstatningsloven-skl", title="Skl"),
        },
        body_for={"åndsverkloven": body, "skadeserstatningsloven-skl": target},
    )
    return CorpusReader(tmp_path)


def test_a_linked_section_of_another_act_is_validated_in_that_act(tmp_path: Path) -> None:
    refs = _corpus(tmp_path).get_section("åndsverkloven", "81")["cross_references"]

    assert refs == [
        {
            "text": "§ 3",
            "target_slug": "åndsverkloven",
            "target_section_id": "3",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 5-2",
            "target_slug": "skadeserstatningsloven-skl",
            "target_section_id": "5-2",
            "valid": True,
            "reason": None,
        },
    ]


def test_a_section_sign_inside_a_link_target_is_not_a_reference(tmp_path: Path) -> None:
    body = _PARAGRAPH_81.replace(
        "[§ 3](lov/2018-06-15-40/§3)", "[tredje ledd](lov/2018-06-15-40/§3)"
    )

    refs = _corpus(tmp_path, body).get_section("åndsverkloven", "81")["cross_references"]

    assert [ref["text"] for ref in refs] == ["§ 5-2"]


def test_a_link_to_a_document_this_corpus_does_not_hold_is_left_unverified(
    tmp_path: Path,
) -> None:
    body = _PARAGRAPH_81.replace("lov/1969-06-13-26/§5-2", "lov/1902-05-22-10/§5-2")

    refs = _corpus(tmp_path, body).get_section("åndsverkloven", "81")["cross_references"]

    assert refs[1] == {
        "text": "§ 5-2",
        "target_slug": None,
        "target_section_id": "5-2",
        "valid": None,
        "reason": (
            "the link names lov/1902-05-22-10, which is not a current document in this "
            "corpus, so its sections cannot be checked here"
        ),
    }


def test_the_link_outranks_a_slug_token_near_the_reference(tmp_path: Path) -> None:
    body = _PARAGRAPH_81.replace(
        "skadeserstatning [§ 5-2]", "skadeserstatning (ikke åndsverkloven) [§ 5-2]"
    )
    refs = _corpus(tmp_path, body).get_section("åndsverkloven", "81")["cross_references"]

    assert refs[1]["target_slug"] == "skadeserstatningsloven-skl"


def test_a_lettered_section_is_checked_under_the_id_its_link_names(tmp_path: Path) -> None:
    """``§ 5 A-2`` parses as ``§ 5``; its link names ``5A-2``, indexed as ``5a-2``."""
    body = _PARAGRAPH_81.replace(
        "[§ 5-2](lov/1969-06-13-26/§5-2)", "[§ 5 A-2](lov/1969-06-13-26/§5A-2)"
    )

    refs = _corpus(tmp_path, body, target="### § 5a-2. Lemping\n\nTekst.\n").get_section(
        "åndsverkloven", "81"
    )["cross_references"]

    assert refs[1] == {
        "text": "§ 5",
        "target_slug": "skadeserstatningsloven-skl",
        "target_section_id": "5a-2",
        "valid": True,
        "reason": None,
    }


def test_a_link_naming_another_section_is_left_unverified(tmp_path: Path) -> None:
    """A link to § 50 under the text § 5 is not a lettered form: neither id is asserted."""
    body = _PARAGRAPH_81.replace("[§ 5-2](lov/1969-06-13-26/§5-2)", "[§ 5](lov/1969-06-13-26/§50)")

    refs = _corpus(tmp_path, body).get_section("åndsverkloven", "81")["cross_references"]

    assert refs[1] == {
        "text": "§ 5",
        "target_slug": "skadeserstatningsloven-skl",
        "target_section_id": "5",
        "valid": None,
        "reason": "the link points at § 50, which this parser reads as § 5, so it is not checked",
    }


def test_a_section_sign_in_a_link_target_still_bounds_the_next_window() -> None:
    """Skipped as a reference, kept as a boundary: the slug before it stays out of reach."""
    body = "Etter annen-lov. Se [x](lov/2018-06-15-40/§3) og § 7."
    acts = known_acts({"egen-lov": ("nl-1", None), "annen-lov": ("nl-2", None)})

    refs = _extract_cross_references(
        body, "egen-lov", acts, lambda _slug: SectionIndex(ids={"7"}, complete=True)
    )

    assert [(ref["text"], ref["target_slug"]) for ref in refs] == [("§ 7", "egen-lov")]


def test_a_plain_slug_set_reads_no_links_and_keeps_the_window_rule() -> None:
    refs = _extract_cross_references(
        "Se [§ 5-2](lov/1969-06-13-26/§5-2).",
        "åndsverkloven",
        {"åndsverkloven"},
        lambda _slug: SectionIndex(ids={"5-2"}, complete=True),
    )

    assert [(ref["text"], ref["target_slug"]) for ref in refs] == [("§ 5-2", "åndsverkloven")]


@pytest.mark.parametrize(
    ("ref", "doc_id"),
    [
        ("lov/1969-06-13-26", "nl-19690613-026"),
        ("lov/2018-06-15-40", "nl-20180615-040"),
        ("lov/1814-05-17", "nl-18140517-000"),
        ("forskrift/2004-06-01-930", "sf-20040601-0930"),
        ("forskrift/2026-09-11-1775", "sf-20260911-1775"),
    ],
)
def test_a_link_reference_maps_to_the_manifest_doc_id(ref: str, doc_id: str) -> None:
    match = _ACT_LINK.search(f"[x]({ref})")
    assert match is not None
    assert doc_id_of(match["kind"], match) == doc_id


def test_known_acts_keeps_the_slug_set_and_maps_doc_ids() -> None:
    acts = known_acts({"skl": ("nl-19690613-026", object())}, "0301/lokal")

    assert isinstance(acts, KnownActs)
    assert acts == {"skl", "0301/lokal"}
    assert acts.by_doc_id == {"nl-19690613-026": "skl"}
