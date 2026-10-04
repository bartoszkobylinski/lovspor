"""The personal-data gate (ADR-0016 Decision 6, personopplysningsloven): hold, never redact.

Every number, name and address here is invented. The two identity numbers are
built to pass the mod-11 check and belong to no one known to this project.
"""

from __future__ import annotations

import pytest

from lovspor.promotion import PersonalDataHit, PersonalDataKind
from lovspor.promotion.personal_data import _check_digit, screen_personal_data

VALID_FODSELSNUMMER = "01019012480"
VALID_D_NUMMER = "41019012393"


def _kinds(text: str) -> set[PersonalDataKind]:
    return {hit.kind for hit in screen_personal_data(text)}


@pytest.mark.parametrize(
    "number",
    [
        VALID_FODSELSNUMMER,
        VALID_D_NUMMER,
        "010190 12480",
        "01419012110",  # H-nummer: month + 40
        "01819012012",  # synthetic test number: month + 80
        "31129010188",  # the last day of the last month
        "71129010090",  # D-nummer on the 31st: day 71 is 31 after the offset
    ],
)
def test_identity_number_passing_mod_11_is_a_hit(number: str) -> None:
    assert _kinds(f"Søker {number} har fått dispensasjon.") == {PersonalDataKind.FODSELSNUMMER}


@pytest.mark.parametrize(
    "number",
    [
        "01019012481",  # second check digit wrong
        "01019012580",  # first check digit wrong
        "01139012085",  # passes mod-11, but month 13
        "32109012067",  # passes mod-11, but day 32
        "01539012068",  # passes mod-11, month 53 is 13 after the H-nummer offset
        "01939012040",  # passes mod-11, month 93 is 13 after the synthetic offset
        "001019012480",  # twelve digits: a longer number, not an identity number
        "1019012480",  # ten digits
    ],
)
def test_eleven_digits_failing_the_check_is_not_a_hit(number: str) -> None:
    assert PersonalDataKind.FODSELSNUMMER not in _kinds(f"Nummer {number} i registeret.")


def test_email_is_a_hit() -> None:
    assert _kinds("Spørsmål sendes til ola.testperson@eksempel.kommune.no.") == {
        PersonalDataKind.EMAIL
    }


@pytest.mark.parametrize(
    "line",
    [
        "Tlf. 912 34 567",
        "Telefon: 22334455",
        "mobil +47 912 34 567",
        "Ring 0047 22 33 44 55 for hjelp",
        "Vakttelefon 98 76 54 32",
    ],
)
def test_phone_number_is_a_hit(line: str) -> None:
    assert PersonalDataKind.PHONE in _kinds(line)


@pytest.mark.parametrize(
    "line",
    [
        "Gebyret er kr 1 250 per år.",
        "Eiendommen gnr. 12 bnr. 345 er unntatt.",
        "Gnr./bnr. 45/678 og 45/679",
        "Dato: FOR-2019-12-12-2077",
        "lov 13. mars 1981 nr. 6 § 30",
        "Avgiften er 12 345 678 kroner samlet.",
        "Forskriften trer i kraft 1.1.2020.",
    ],
)
def test_legal_content_is_not_a_hit(line: str) -> None:
    assert screen_personal_data(line) == ()


@pytest.mark.parametrize(
    "line",
    ["Testveien 12, 9999 Eksempelby", "Storgata 5 B 9999 Eksempelby", "Postboks 123"],
)
def test_postal_address_is_a_hit(line: str) -> None:
    assert _kinds(line) == {PersonalDataKind.POSTAL_ADDRESS}


def test_street_name_without_postal_code_is_legal_content() -> None:
    assert screen_personal_data("Parkering er forbudt i Storgata og Testveien.") == ()


@pytest.mark.parametrize(
    "line",
    ["Kontaktperson: Kari Testperson", "Saksbehandler Ola Testperson", "Kontakt: Ola"],
)
def test_contact_line_is_a_hit(line: str) -> None:
    assert _kinds(line) == {PersonalDataKind.CONTACT_LINE}


@pytest.mark.parametrize(
    "lines",
    [
        "Kari Testperson\nordfører",
        "Ordfører\nKari Anne Testperson",
        "Ola Testperson-Hansen\nKommunedirektør.",
    ],
)
def test_signature_block_is_a_hit(lines: str) -> None:
    assert _kinds(lines) == {PersonalDataKind.SIGNATURE}


def test_office_title_without_a_name_beside_it_is_not_a_hit() -> None:
    text = "Eksempel kommune\nordfører\n§ 1 Formål\nForskriften gjelder i hele kommunen."
    assert screen_personal_data(text) == ()


def test_hits_carry_kind_and_line_never_the_value() -> None:
    text = "Forskrift om gebyr\n§ 1 Gebyr\nKontakt: ola@eksempel.no, tlf. 912 34 567"
    hits = screen_personal_data(text)
    assert hits == (
        PersonalDataHit(kind=PersonalDataKind.CONTACT_LINE, line=3),
        PersonalDataHit(kind=PersonalDataKind.EMAIL, line=3),
        PersonalDataHit(kind=PersonalDataKind.PHONE, line=3),
    )
    assert "ola" not in repr(hits)


def test_hits_are_ordered_by_line_then_kind() -> None:
    text = f"tlf. 912 34 567\n{VALID_FODSELSNUMMER}\nola@eksempel.no"
    assert [(hit.line, hit.kind) for hit in screen_personal_data(text)] == [
        (1, PersonalDataKind.PHONE),
        (2, PersonalDataKind.FODSELSNUMMER),
        (3, PersonalDataKind.EMAIL),
    ]


def test_a_digit_list_shorter_than_its_weights_is_refused_not_weighted_by_a_prefix() -> None:
    """``zip`` would silently weigh only the first nine digits of a ten-digit list."""
    with pytest.raises(ValueError, match="zip"):
        _check_digit([0, 1, 0, 1, 9, 0, 1, 2, 4], (5, 4, 3, 2, 7, 6, 5, 4, 3, 2))


def test_signature_hit_names_the_name_line_not_the_title_line() -> None:
    text = "§ 9 Ikrafttredelse\nKari Testperson\nordfører\n"
    assert screen_personal_data(text) == (PersonalDataHit(kind=PersonalDataKind.SIGNATURE, line=2),)


def test_a_word_that_only_starts_with_an_office_title_is_not_one() -> None:
    assert screen_personal_data("Kari Testperson\nordførerX") == ()


@pytest.mark.parametrize(
    "line",
    [
        "Publisert av Kari Testperson",
        "Skrevet av Ola Testperson-Hansen",
        "Skrive av Ola Testperson",
        "Sist endret av Kari Anne Testperson",
        "Sist endra av Kari Testperson",
        "Oppdatert av: Ola Testperson",
        "Publisert 12.03.2020 av Kari Testperson",
        "Sist endra 13.10.2017 08.44 av Ola Testperson",
        "Publisert av Kari Testperson 12.03.2020",
        "Ansvarlig: Kari Testperson",
        "Ansvarleg Ola Testperson",
        "Ansvarlig redaktør: Kari Testperson",
        "Sist oppdatert 01.02.2024 | Publisert av Ola Testperson",
    ],
)
def test_byline_naming_a_person_is_a_hit(line: str) -> None:
    assert _kinds(line) == {PersonalDataKind.BYLINE}


@pytest.mark.parametrize(
    "line",
    [
        "Publisert av Teknisk etat",
        "Publisert av Eksempel Kommune",
        "Sist endret av Plan- og byggesaksavdelingen",
        "Oppdatert av Servicetorget",
        "Skrevet av Kommunestyret",
        "Publisert av Kari",
        "Ansvarlig myndighet er kommunestyret.",
        "Ansvarleg for tiltaket er Eksempel kommune.",
        "Forskriften er fastsatt av Kommunestyret i Eksempel kommune.",
        "Vedtatt av Eksempel Kommunestyre 12.03.2020.",
        "Endret av Statsforvalteren i Trøndelag.",
        "Publisert av Eksempel Bystyre",
        "Sist endret 13.10.2017 08.44",
    ],
)
def test_byline_naming_no_person_is_not_a_hit(line: str) -> None:
    assert screen_personal_data(line) == ()


def test_byline_hit_carries_its_line_never_the_name() -> None:
    text = "§ 1 Formål\nForskriften gjelder i hele kommunen.\nPublisert av Kari Testperson"
    hits = screen_personal_data(text)
    assert hits == (PersonalDataHit(kind=PersonalDataKind.BYLINE, line=3),)
    assert "Testperson" not in repr(hits)


@pytest.mark.parametrize("line", ["Sakshandsamar: Kari Testperson", "Kontaktperson Ola"])
def test_nynorsk_contact_line_is_a_hit(line: str) -> None:
    assert _kinds(line) == {PersonalDataKind.CONTACT_LINE}


def test_an_organ_word_as_a_compound_tail_still_names_a_unit() -> None:
    assert screen_personal_data("Publisert av Eksempel Bystyre\nSkrevet av Ola Helsesenteret") == ()


@pytest.mark.parametrize(
    "line", ["Publisert av Teknisk Etat Nord", "Oppdatert av Plan-Kontoret Sør"]
)
def test_an_organ_word_before_the_last_word_still_names_a_unit(line: str) -> None:
    """Each word is judged on its own, so an organ word mid-name is not hidden by the next."""
    assert screen_personal_data(line) == ()
