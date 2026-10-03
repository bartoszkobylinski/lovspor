"""Identity layer for promoted local regulations (ADR-0016 Decision 1, slice S1).

Fixtures are hand-written snippets in the shape ADR-0016 describes (a Lovdata
style header, a Lovtidend title line, a municipal page body) — never copied
from the observatory archive, and nothing here touches the network.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest
from pydantic import TypeAdapter, ValidationError

from lovspor.promotion import (
    Authority,
    AuthorityType,
    ExtractedRegulation,
    HeldIdentity,
    HoldReason,
    IdentityResult,
    IdScheme,
    MintedIdentity,
    content_hash,
    find_identification_ids,
    mint_identity,
    normalise_text,
    normalise_title,
)

OSLO = Authority(id="0301", type=AuthorityType.KOMMUNE, name="Oslo", klass_version="2024")
VESTLAND = Authority(
    id="46", type=AuthorityType.FYLKESKOMMUNE, name="Vestland", klass_version="2024"
)

LOVDATA_HEADER = """\
Forskrift om renovasjon og slam, Oslo kommune
Dato: FOR-2019-12-12-2077
Hjemmel: LOV-1981-03-13-6-§30
Kunngjort 20.12.2019 kl. 14.10
"""

LOVTIDEND_TITLE = "Forskrift 12. desember 2019 nr. 2077 om renovasjon og slam, Oslo kommune\n"

BODY = """\
§ 1. Formål
Forskriften skal sikre en miljømessig forsvarlig renovasjon.

§ 2. Virkeområde
Forskriften gjelder for alle eiendommer i Oslo kommune.
"""

BODY_WITH_CITATION = """\
§ 1. Endring
I forskrift 1. juni 2004 nr. 931 om begrensning av forurensning gjøres endringer.
Endrer FOR-2004-06-01-931.
"""

MUNICIPAL_TITLE_BLOCK = """\
Forskrift om feiing og tilsyn med fyringsanlegg, Oslo kommune
Vedtatt av bystyret 12.03.2015.
"""


def _regulation(
    identification_block: str, body: str = BODY, vedtaksdato: date | None = None
) -> ExtractedRegulation:
    return ExtractedRegulation(
        title=identification_block.splitlines()[0],
        identification_block=identification_block,
        body=body,
        vedtaksdato=vedtaksdato,
    )


# --- lf- ids come only from the identification block -------------------------


def test_lovdata_header_mints_lf_id_and_ref_id() -> None:
    result = mint_identity(_regulation(LOVDATA_HEADER), OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.doc_id == "lf-20191212-2077"
    assert result.scheme is IdScheme.LF
    assert result.ref_id == "forskrift/2019-12-12-2077"


def test_lovtidend_title_line_mints_lf_id() -> None:
    result = mint_identity(_regulation(LOVTIDEND_TITLE), OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.doc_id == "lf-20191212-2077"


def test_lf_number_is_zero_padded_but_ref_id_matches_central_form() -> None:
    result = mint_identity(_regulation("Dato: FOR-2020-01-14-63\n"), OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.doc_id == "lf-20200114-0063"
    assert result.ref_id == "forskrift/2020-01-14-63"


def test_header_and_title_naming_the_same_id_are_one_candidate() -> None:
    result = mint_identity(_regulation(LOVTIDEND_TITLE + LOVDATA_HEADER), OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.doc_id == "lf-20191212-2077"
    assert result.candidates == ("lf-20191212-2077",)


def test_body_citation_is_never_minted() -> None:
    regulation = _regulation(MUNICIPAL_TITLE_BLOCK, BODY_WITH_CITATION, date(2015, 3, 12))
    result = mint_identity(regulation, OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.scheme is IdScheme.LK
    assert result.ref_id is None
    assert result.candidates == ()


def test_body_citation_without_vedtaksdato_is_held_not_minted() -> None:
    result = mint_identity(_regulation(MUNICIPAL_TITLE_BLOCK, BODY_WITH_CITATION), OSLO)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.NO_IDENTITY


def test_hjemmel_and_endrer_lines_in_the_header_are_not_identification() -> None:
    block = (
        "Forskrift om endring i forskrift om renovasjon, Oslo kommune\n"
        "Hjemmel: forskrift 1. juni 2004 nr. 931\n"
        "Endrer: FOR-2004-06-01-931\n"
    )
    assert find_identification_ids(block) == ()


def test_endringsforskrift_title_cites_target_after_its_own_number() -> None:
    block = "Forskrift 3. mai 2021 nr. 1400 om endring i forskrift 1. juni 2004 nr. 931\n"
    assert find_identification_ids(block) == ("lf-20210503-1400",)


def test_two_candidates_in_identification_block_fall_back_to_lk() -> None:
    block = "Dato: FOR-2019-12-12-2077\nDato: FOR-2019-12-13-2078\n"
    result = mint_identity(_regulation(block, vedtaksdato=date(2019, 12, 12)), OSLO)
    assert isinstance(result, MintedIdentity)
    assert result.scheme is IdScheme.LK
    assert result.candidates == ("lf-20191212-2077", "lf-20191213-2078")


def test_two_candidates_and_no_vedtaksdato_is_held() -> None:
    block = "Dato: FOR-2019-12-12-2077\nDato: FOR-2019-12-13-2078\n"
    result = mint_identity(_regulation(block), OSLO)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.NO_IDENTITY
    assert result.candidates == ("lf-20191212-2077", "lf-20191213-2078")


@pytest.mark.parametrize(
    "line",
    [
        "Dato: FOR-2019-02-30-12",
        "Dato: FOR-2019-12-12-0",
        "Forskrift 31. februar 2019 nr. 12 om renovasjon",
        "Forskrift 12. brumaire 2019 nr. 12 om renovasjon",
    ],
)
def test_impossible_dates_and_numbers_are_not_candidates(line: str) -> None:
    assert find_identification_ids(line) == ()


@pytest.mark.parametrize(
    "line",
    [
        "Forskrift 12.12.2019 nr. 2077 om renovasjon",
        "Forskrift 2019-12-12 nr 2077 om renovasjon",
        "Forskrift av 12. desember 2019 nr. 2077 om renovasjon",
        "FOR-2019-12-12-2077",
        "Kunngjort i Norsk Lovtidend 20. desember 2019 som forskrift 12. desember 2019 nr. 2077",
    ],
)
def test_identification_line_forms(line: str) -> None:
    assert find_identification_ids(line) == ("lf-20191212-2077",)


def test_numeric_date_reads_day_then_month() -> None:
    # 14.01: the day and the month differ, so a swapped field cannot pass.
    assert find_identification_ids("Forskrift 14.01.2020 nr. 63 om gebyr") == ("lf-20200114-0063",)


def test_kunngjort_line_without_number_is_not_a_candidate() -> None:
    assert find_identification_ids("Kunngjort 20. desember 2019 kl. 14.10") == ()


# --- collision with the central corpus ---------------------------------------


def test_collision_with_central_ref_id_is_held() -> None:
    central = frozenset({"forskrift/2019-12-12-2077"})
    result = mint_identity(_regulation(LOVDATA_HEADER), OSLO, central)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.CENTRAL_COLLISION
    assert result.colliding_ref_id == "forskrift/2019-12-12-2077"


def test_collision_compares_numbers_not_padding() -> None:
    central = frozenset({"forskrift/2020-01-14-63"})
    result = mint_identity(_regulation("FOR-2020-01-14-0063\n"), OSLO, central)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.CENTRAL_COLLISION


def test_unrelated_central_ref_ids_do_not_hold() -> None:
    central = frozenset({"forskrift/2019-12-12-2076", "lov/2019-12-12-2077", "garbage"})
    result = mint_identity(_regulation(LOVDATA_HEADER), OSLO, central)
    assert isinstance(result, MintedIdentity)


def test_fallback_id_is_never_a_central_collision() -> None:
    central = frozenset({"forskrift/2015-03-12-1"})
    regulation = _regulation(MUNICIPAL_TITLE_BLOCK, vedtaksdato=date(2015, 3, 12))
    assert isinstance(mint_identity(regulation, OSLO, central), MintedIdentity)


# --- deterministic fallback ---------------------------------------------------


def test_fallback_id_matches_the_adr_formula() -> None:
    regulation = _regulation(MUNICIPAL_TITLE_BLOCK, vedtaksdato=date(2015, 3, 12))
    result = mint_identity(regulation, OSLO)
    title = "forskrift om feiing og tilsyn med fyringsanlegg oslo kommune"
    seed = "\x1f".join(["0301", title, "2015-03-12"]).encode("utf-8")
    assert isinstance(result, MintedIdentity)
    assert result.normalised_title == title
    assert result.doc_id == "lk-0301-" + hashlib.sha256(seed).hexdigest()[:12]


def test_fallback_id_is_stable_across_runs_and_formatting() -> None:
    first = _regulation(MUNICIPAL_TITLE_BLOCK, vedtaksdato=date(2015, 3, 12))
    reformatted = ExtractedRegulation(
        title="  FORSKRIFT om feiing  og tilsyn med fyringsanlegg \u2013 Oslo kommune. ",
        identification_block=MUNICIPAL_TITLE_BLOCK,
        body=BODY,
        vedtaksdato=date(2015, 3, 12),
    )
    ids = {mint_identity(r, OSLO).doc_id for r in (first, first, reformatted)}  # type: ignore[union-attr]
    assert len(ids) == 1


def test_fallback_id_differs_by_authority_and_date() -> None:
    regulation = _regulation(MUNICIPAL_TITLE_BLOCK, vedtaksdato=date(2015, 3, 12))
    later = _regulation(MUNICIPAL_TITLE_BLOCK, vedtaksdato=date(2015, 3, 13))
    oslo = mint_identity(regulation, OSLO)
    vestland = mint_identity(regulation, VESTLAND)
    other_day = mint_identity(later, OSLO)
    assert isinstance(oslo, MintedIdentity) and isinstance(vestland, MintedIdentity)
    assert isinstance(other_day, MintedIdentity)
    assert vestland.doc_id.startswith("lk-46-")
    assert len({oslo.doc_id, vestland.doc_id, other_day.doc_id}) == 3


def test_no_identification_and_no_date_is_held_never_minted() -> None:
    result = mint_identity(_regulation(MUNICIPAL_TITLE_BLOCK), OSLO)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.NO_IDENTITY
    assert result.detail == (
        "no single lf- id in the identification block, and no stated vedtaksdato"
    )
    assert not hasattr(result, "doc_id")


def test_title_without_words_is_held_even_with_a_date() -> None:
    regulation = ExtractedRegulation(
        title=" \u2013 . ", identification_block="", body=BODY, vedtaksdato=date(2015, 3, 12)
    )
    result = mint_identity(regulation, OSLO)
    assert isinstance(result, HeldIdentity)
    assert result.reason is HoldReason.NO_IDENTITY
    assert result.detail == "no single lf- id in the identification block, and no title words"


# --- normalisation and content hash -------------------------------------------


def test_normalise_title_rule() -> None:
    raw = "Forskrift  om\tRenovasjon, «Oslo» kommune (§ 3)."
    assert normalise_title(raw) == "forskrift om renovasjon oslo kommune 3"


def test_normalise_title_is_nfc() -> None:
    assert normalise_title("Skåun") == normalise_title("Skåun") == "skåun"


def test_normalise_text_ignores_layout_whitespace() -> None:
    noisy = "\ufeff  § 1. Formål \r\n\r\n\r\nForskriften\u00a0skal\u00adsikre  renovasjon.\t\n\n"
    assert normalise_text(noisy) == "§ 1. Formål\n\nForskriften skalsikre renovasjon."


def test_normalise_text_keeps_lines_of_one_paragraph_on_their_own_lines() -> None:
    assert normalise_text("§ 1. Formål\nForskriften gjeld.\n\n§ 2") == (
        "§ 1. Formål\nForskriften gjeld.\n\n§ 2"
    )


def test_normalise_text_keeps_case_and_punctuation() -> None:
    assert normalise_text("Kommunen SKAL, jf. § 2.") == "Kommunen SKAL, jf. § 2."


def test_content_hash_is_sha256_of_normalised_text() -> None:
    text = "§ 1.  Formål\r\n"
    expected = hashlib.sha256("§ 1. Formål".encode()).hexdigest()
    assert content_hash(text) == expected


def test_content_hash_ignores_layout_but_not_wording() -> None:
    base = content_hash(LOVDATA_HEADER + "\n" + BODY)
    assert content_hash(LOVDATA_HEADER.replace("\n", "\r\n") + "\n \n\n" + BODY) == base
    assert content_hash(LOVDATA_HEADER.replace("\n", "\r") + "\r" + BODY) == base
    assert content_hash(LOVDATA_HEADER + "\n" + BODY.replace("alle", "noen")) != base


def test_minted_and_held_results_carry_the_content_hash() -> None:
    minted = mint_identity(_regulation(LOVDATA_HEADER), OSLO)
    held = mint_identity(_regulation(MUNICIPAL_TITLE_BLOCK), OSLO)
    assert minted.content_hash == content_hash(LOVDATA_HEADER + "\n" + BODY)
    assert held.content_hash == content_hash(MUNICIPAL_TITLE_BLOCK + "\n" + BODY)


def test_mint_identity_is_deterministic() -> None:
    regulation = _regulation(LOVDATA_HEADER)
    assert mint_identity(regulation, OSLO) == mint_identity(regulation, OSLO)


# --- authority block ------------------------------------------------------------


def test_authority_id_length_must_match_type() -> None:
    with pytest.raises(ValidationError):
        Authority(id="46", type=AuthorityType.KOMMUNE, name="Vestland", klass_version="2024")
    with pytest.raises(ValidationError):
        Authority(id="0301", type=AuthorityType.FYLKESKOMMUNE, name="Oslo", klass_version="2024")


@pytest.mark.parametrize("bad", ["301", "03011", "03O1", "", "0301 "])
def test_authority_id_must_be_a_klass_code(bad: str) -> None:
    with pytest.raises(ValidationError):
        Authority(id=bad, type=AuthorityType.KOMMUNE, name="Oslo", klass_version="2024")


def test_authority_requires_name_and_klass_version() -> None:
    with pytest.raises(ValidationError):
        Authority(id="0301", type=AuthorityType.KOMMUNE, name="", klass_version="2024")
    with pytest.raises(ValidationError):
        Authority(id="0301", type=AuthorityType.KOMMUNE, name="Oslo", klass_version="")


def test_result_round_trips_through_its_discriminated_union() -> None:
    adapter: TypeAdapter[IdentityResult] = TypeAdapter(IdentityResult)
    for result in (
        mint_identity(_regulation(LOVDATA_HEADER), OSLO),
        mint_identity(_regulation(MUNICIPAL_TITLE_BLOCK), VESTLAND),
    ):
        assert adapter.validate_json(adapter.dump_json(result)) == result


def test_held_result_refuses_a_doc_id() -> None:
    with pytest.raises(ValidationError):
        HeldIdentity.model_validate(
            {
                "reason": "no_identity",
                "detail": "x",
                "authority": OSLO.model_dump(),
                "content_hash": "0" * 64,
                "doc_id": "lk-0301-000000000000",
            }
        )
