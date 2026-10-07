"""Source-explicit relations: recorded only with verbatim evidence, never guessed (ADR-0016 1e)."""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion.corpus import CentralEntry, LocalManifest, LocalRecord
from lovspor.promotion.evidence import target_index
from lovspor.promotion.models import ExtractedRegulation
from lovspor.promotion.relations import (
    LinkedDocument,
    RelationKind,
    StatedRelation,
    TargetIndex,
    Unresolved,
    resolve,
    short_name_key,
    stated_relations,
)

OWN_ID = "lf-20200505-0900"
FORURENSNINGSLOVEN = CentralEntry(
    status="current",
    slug="forurensningsloven-forurl",
    title="Lov om vern mot forurensninger og om avfall (forurensningsloven)",
    markdown_path="lover/forurensningsloven-forurl.md",
)
OPPLARINGSLOVA = CentralEntry(
    status="current",
    slug="opplæringslova",
    title="Lov om grunnskoleopplæringa og den vidaregåande opplæringa (opplæringslova)",
    markdown_path="lover/opplæringslova.md",
)
CENTRAL_FORSKRIFT = CentralEntry(
    status="current", slug="avfallsforskriften", markdown_path="forskrifter/avfallsforskriften.md"
)


def _regulation(*lines: str) -> ExtractedRegulation:
    return ExtractedRegulation(
        title=lines[0], identification_block="\n".join(lines[:2]), body="\n".join(lines[2:])
    )


def _local_record(slug: str) -> LocalRecord:
    return LocalRecord(
        status="current",
        slug=slug,
        title="Forskrift om renovasjon",
        markdown_path=f"lokale-forskrifter/0301/{slug}.md",
        renderer_version=1,
        last_seen="2026-08-19T15:17:23Z",
        authority_id="0301",
        authority_type="kommune",
        content_hash="0" * 64,
        version=1,
        extractor_version=4,
    )


def _index(**local: LocalRecord) -> TargetIndex:
    central = {
        "nl-19810313-006": FORURENSNINGSLOVEN,
        "nl-20230609-030": OPPLARINGSLOVA,
        "sf-20040601-0930": CENTRAL_FORSKRIFT,
    }
    return target_index(central, LocalManifest(documents=dict(local)))


def _flat(regulation: ExtractedRegulation) -> str:
    return regulation.full_text.replace("\n", " ")


class TestStated:
    def test_a_hjemmel_sentence_names_each_target_and_keeps_the_sentence_verbatim(self) -> None:
        sentence = (
            "Vedtatt av kommunestyret 12.12.2019 med hjemmel i lov 13. mars 1981 nr. 6 om vern "
            "mot forurensninger og om avfall (forurensningsloven) § 30."
        )
        regulation = _regulation("Forskrift om renovasjon", sentence, "§ 1 Formål")

        found = stated_relations(regulation)

        assert [(r.kind, r.target.text, r.target.form) for r in found] == [
            (RelationKind.HJEMMEL, "lov 13. mars 1981 nr. 6", "date_and_number"),
            (RelationKind.HJEMMEL, "forurensningsloven", "short_name"),
        ]
        assert all(r.evidence == sentence for r in found)
        assert all(r.evidence in _flat(regulation) for r in found)

    def test_a_hjemmel_header_reads_lovdata_ids(self) -> None:
        regulation = _regulation(
            "Forskrift om renovasjon", "Hjemmel: LOV-1981-03-13-6 § 30", "§ 1 Formål"
        )

        [found] = stated_relations(regulation)

        assert found.kind is RelationKind.HJEMMEL
        assert found.target.model_dump() == {
            "text": "LOV-1981-03-13-6",
            "form": "lovdata_id",
            "kind": "lov",
            "stated_date": date(1981, 3, 13),
            "number": 6,
        }

    @pytest.mark.parametrize(
        ("sentence", "target"),
        [
            (
                "Samtidig oppheves forskrift 1. juli 2001 nr. 55 om slam.",
                "forskrift 1. juli 2001 nr. 55",
            ),
            ("Forskrift 2. mai 2002 nr. 33 om slam oppheves.", "Forskrift 2. mai 2002 nr. 33"),
            (
                "Frå same tid vert forskrift 2. mai 2002 nr. 33 oppheva.",
                "forskrift 2. mai 2002 nr. 33",
            ),
            ("Denne forskriften opphever FOR-2002-05-02-33.", "FOR-2002-05-02-33"),
        ],
    )
    def test_a_repeal_is_read_with_the_cue_before_or_passive_after(
        self, sentence: str, target: str
    ) -> None:
        regulation = _regulation("Forskrift om slam", "Vedtatt 1.1.2020.", sentence)

        [found] = stated_relations(regulation)

        assert (found.kind, found.target.text, found.evidence) == (
            RelationKind.REPEALS,
            target,
            sentence,
        )

    def test_endret_ved_names_the_act_that_amended_this_one(self) -> None:
        sentence = "Endret ved forskrift 3. mars 2021 nr. 12."
        regulation = _regulation("Forskrift om slam", "Vedtatt 1.1.2020.", sentence)

        [found] = stated_relations(regulation)

        assert (found.kind, found.target.text) == (
            RelationKind.AMENDED_BY,
            "forskrift 3. mars 2021 nr. 12",
        )

    def test_the_own_number_in_an_amending_title_is_not_a_target_of_itself(self) -> None:
        title = "Forskrift 5. mai 2020 nr. 900 om endring i forskrift 12. desember 2019 nr. 2077"
        regulation = _regulation(title, "Vedtatt 5.5.2020.", "§ 1 Endring")

        [found] = stated_relations(regulation)

        assert (found.kind, found.target.text) == (
            RelationKind.AMENDS,
            "forskrift 12. desember 2019 nr. 2077",
        )

    def test_a_citation_without_a_cue_is_not_a_relation(self) -> None:
        regulation = _regulation(
            "Forskrift om renovasjon",
            "Vedtatt 1.1.2020.",
            "Gebyr fastsettes etter forurensningsloven § 34, jf. lov 13. mars 1981 nr. 6.",
        )

        assert stated_relations(regulation) == ()

    def test_each_target_takes_the_nearest_cue_before_it(self) -> None:
        regulation = _regulation(
            "Forskrift om renovasjon",
            "Fastsatt med hjemmel i forurensningsloven § 30 og opphever forskrift 1.1.2001 nr. 5.",
            "§ 1",
        )

        found = stated_relations(regulation)

        assert [(r.kind, r.target.text) for r in found] == [
            (RelationKind.HJEMMEL, "forurensningsloven § 30"),
            (RelationKind.REPEALS, "forskrift 1.1.2001 nr. 5"),
        ]

    @pytest.mark.parametrize(
        "sentence",
        [
            "Enkeltvedtak truffet i medhold av denne forskriften kan påklages etter "
            "forvaltningsloven § 28.",
            "Saksbehandlingen følger bestemmelsene gitt i medhold av forvaltningsloven.",
        ],
    )
    def test_a_body_hjemmel_cue_outside_the_own_basis_is_no_relation(self, sentence: str) -> None:
        regulation = _regulation("Forskrift om renovasjon", "Vedtatt 1.1.2020.", "§ 5", sentence)

        assert stated_relations(regulation) == ()

    @pytest.mark.parametrize(
        "sentence",
        [
            "Forskriften er gitt med hjemmel i forurensningsloven § 30.",
            "Hjemmel: forurensningsloven § 30.",
        ],
    )
    def test_a_body_statement_of_the_own_basis_is_a_hjemmel(self, sentence: str) -> None:
        regulation = _regulation("Forskrift om renovasjon", "Vedtatt 1.1.2020.", "§ 1", sentence)

        [found] = stated_relations(regulation)

        assert (found.kind, found.target.text) == (
            RelationKind.HJEMMEL,
            "forurensningsloven § 30",
        )

    def test_a_law_is_only_ever_a_hjemmel_target(self) -> None:
        sentence = "Forskrift 22. mai 2008 nr. 534 om skjenking etter alkoholloven oppheves."
        regulation = _regulation("Forskrift om skjenking", "Vedtatt 1.1.2020.", "§ 9", sentence)

        [found] = stated_relations(regulation)

        assert (found.kind, found.target.text) == (
            RelationKind.REPEALS,
            "Forskrift 22. mai 2008 nr. 534",
        )

    def test_a_target_after_jf_is_a_citation(self) -> None:
        regulation = _regulation(
            "Forskrift om renovasjon",
            "Fastsatt med hjemmel i forurensningsloven § 30, jf. forskrift 1.6.2004 nr. 931.",
            "§ 1",
        )

        [found] = stated_relations(regulation)

        assert found.target.text == "forurensningsloven § 30"


class TestResolved:
    def _one(self, sentence: str) -> StatedRelation:
        [stated] = stated_relations(_regulation("Forskrift om x", sentence, "§ 1"))
        return stated

    def test_a_date_and_number_links_the_central_law(self) -> None:
        stated = self._one("Gitt med hjemmel i lov 9. juni 2023 nr. 30 § 15-2.")

        relation = resolve(stated, _index(), OWN_ID)

        assert relation.target == LinkedDocument(
            doc_id="nl-20230609-030", dataset="lover", address="opplæringslova"
        )
        assert relation.unresolved is None

    def test_a_short_name_links_the_one_law_whose_short_title_it_is(self) -> None:
        relation = resolve(self._one("Med hjemmel i opplæringslova § 15-2."), _index(), OWN_ID)

        assert relation.target is not None
        assert relation.target.doc_id == "nl-20230609-030"
        assert relation.target_text == "opplæringslova § 15-2"

    def test_a_short_name_two_laws_share_stays_text(self) -> None:
        twin = OPPLARINGSLOVA.model_copy(update={"slug": "opplæringslova-2"})
        index = target_index(
            {"nl-20230609-030": OPPLARINGSLOVA, "nl-19980717-061": twin}, LocalManifest()
        )

        relation = resolve(self._one("Med hjemmel i opplæringslova § 15-2."), index, OWN_ID)

        assert (relation.target, relation.unresolved) == (None, Unresolved.AMBIGUOUS_NAME)

    def test_a_forskrift_id_links_a_central_or_local_document(self) -> None:
        local = {"lf-20191212-2077": _local_record("renovasjonsforskrift")}
        index = _index(**local)

        central = resolve(self._one("Endring i FOR-2004-06-01-930."), index, OWN_ID)
        promoted = resolve(self._one("Endring i forskrift 12.12.2019 nr. 2077."), index, OWN_ID)

        assert central.target is not None
        assert (central.target.dataset, central.target.address) == (
            "forskrifter",
            "avfallsforskriften",
        )
        assert promoted.target == LinkedDocument(
            doc_id="lf-20191212-2077",
            dataset="lokale-forskrifter",
            address="0301/renovasjonsforskrift",
        )

    @pytest.mark.parametrize(
        ("sentence", "reason"),
        [
            ("Samtidig oppheves forskrift 1. juli 2001 om slam.", Unresolved.NO_NUMBER),
            ("Samtidig oppheves forskrift 1. juli 2001 nr. 4 om slam.", Unresolved.NOT_IN_CORPUS),
            ("Med hjemmel i vegtrafikkloven § 7.", Unresolved.NOT_IN_CORPUS),
        ],
    )
    def test_an_unresolved_target_is_kept_as_text_with_its_reason(
        self, sentence: str, reason: Unresolved
    ) -> None:
        stated = self._one(sentence)

        relation = resolve(stated, _index(), OWN_ID)

        assert relation.target is None
        assert relation.unresolved is reason
        assert relation.target_text == stated.target.text
        assert relation.evidence == sentence

    def test_a_target_that_is_the_document_itself_is_not_linked(self) -> None:
        local = {OWN_ID: _local_record("endringsforskrift")}

        relation = resolve(
            self._one("Endring i forskrift 5. mai 2020 nr. 900."), _index(**local), OWN_ID
        )

        assert (relation.target, relation.unresolved) == (None, Unresolved.SELF_REFERENCE)

    def test_removed_and_unaddressable_central_records_are_not_linked(self) -> None:
        removed = FORURENSNINGSLOVEN.model_copy(update={"status": "removed"})
        index = target_index({"nl-19810313-006": removed}, LocalManifest())

        relation = resolve(self._one("Med hjemmel i lov 13. mars 1981 nr. 6."), index, OWN_ID)

        assert relation.unresolved is Unresolved.NOT_IN_CORPUS


def test_short_names_compare_by_words_and_case_only() -> None:
    assert short_name_key("Plan-  og  bygningsloven § 12-2") == "plan- og bygningsloven"
    assert short_name_key("plan- og bygningsloven") == "plan- og bygningsloven"
