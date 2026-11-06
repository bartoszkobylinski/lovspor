"""Official Norwegian ELI for an act, from Lovdata's ref_id (#819).

Every positive row is an attested URI from an official source, recorded in
docs/research/eli-assessment-2026-11-06.md with its source, commit and line.
Every negative row is a ref_id shape that no permitted source verifies: the
function must return None for it with a named reason, never a guess.
"""

import re
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lovspor.eli import ELI_ACT_PATTERN, EliOutcome, act_eli, audit_ref_ids, official_act_eli

ATTESTED = [
    # Digdir DCAT-AP-NO, Klasse_Datasett.adoc:311 (and three sibling files).
    ("forskrift/2016-12-15-1600", "https://lovdata.no/eli/forskrift/2016/12/15/1600"),
    # Digdir CPSV-AP-NO, TemaKnytteTilRegelverk.adoc:23 (attested with a
    # trailing slash; the act URI is emitted without one, as every other
    # attested act URI is written).
    ("lov/1989-06-02-27", "https://lovdata.no/eli/lov/1989/06/02/27"),
    # Digdir SKOS-AP-NO, To-måter-definisjon.adoc:30, act part of a chapter URI.
    ("lov/1997-02-28-19", "https://lovdata.no/eli/lov/1997/02/28/19"),
    # Wayback Machine capture index: HTTP 200 on 2021-10-02.
    ("lov/2005-05-20-28", "https://lovdata.no/eli/lov/2005/05/20/28"),
]


class TestAttestedUris:
    @pytest.mark.parametrize(("ref_id", "uri"), ATTESTED)
    def test_an_attested_act_maps_to_its_official_uri(self, ref_id: str, uri: str) -> None:
        assert official_act_eli(ref_id) == uri
        assert act_eli(ref_id) == EliOutcome(ref_id=ref_id, uri=uri, reason="mapped")

    def test_a_three_digit_regulation_number_stays_unpadded(self) -> None:
        """DFØ eForms SDK attests forskrift/…/974, not 0974."""
        assert official_act_eli("forskrift/2016-08-12-974") == (
            "https://lovdata.no/eli/forskrift/2016/08/12/974"
        )


NOT_VERIFIED = [
    # No serial number: the eight pre-1850 acts and Grunnloven's two editions.
    ("lov/1687-04-15", "no_serial_number"),
    ("lov/1814-05-17", "no_serial_number"),
    ("forskrift/1900-01-01", "no_serial_number"),
    # Impossible calendar dates.
    ("lov/2005-02-30-1", "invalid_date"),
    ("lov/2005-13-01-1", "invalid_date"),
    ("lov/2005-00-10-1", "invalid_date"),
    ("forskrift/2005-04-31-9", "invalid_date"),
    # Number shapes no source attests.
    ("lov/2005-05-20-028", "unsupported_shape"),
    ("lov/2005-05-20-0", "unsupported_shape"),
    ("lov/2005-05-20-28x", "unsupported_shape"),
    ("lov/2005-05-20-28-1", "unsupported_shape"),
    # Other types and prefixes: Lovtidend, local regulations, file ids.
    ("lokal-forskrift/2020-01-01-1", "unsupported_shape"),
    ("LOV/2005-05-20-28", "unsupported_shape"),
    ("LTI/lov/2005-05-20-28", "unsupported_shape"),
    ("nl-20050520-028", "unsupported_shape"),
    # Malformed dates and whitespace.
    ("lov/2005-5-20-28", "unsupported_shape"),
    ("lov/05-05-20-28", "unsupported_shape"),
    ("", "unsupported_shape"),
    (" lov/2005-05-20-28", "unsupported_shape"),
    ("lov/2005-05-20-28 ", "unsupported_shape"),
    ("lov/2005-05-20-28\n", "unsupported_shape"),
    # Non-ASCII digits that str.isdigit() and \d would accept.
    ("lov/\uff12\uff10\uff10\uff15-05-20-28", "unsupported_shape"),  # fullwidth
    ("lov/2005-05-20-\u0662\u0668", "unsupported_shape"),  # Arabic-Indic
    ("lov/2005-05-20-\U0001d7d0\U0001d7d6", "unsupported_shape"),  # math bold
    # Subdivisions, languages and fragments: act level only.
    ("lov/2005-05-20-28/§3", "unsupported_shape"),
    ("lov/2005-05-20-28/kapittel/14", "unsupported_shape"),
    ("lov/2005-05-20-28/nor", "unsupported_shape"),
    ("lov/2005-05-20-28#paragraf-3", "unsupported_shape"),
    ("nl-20050520-028#paragraf-3", "unsupported_shape"),
]


class TestUnverifiedShapesReturnNone:
    @pytest.mark.parametrize(("ref_id", "reason"), NOT_VERIFIED)
    def test_an_unverified_shape_has_no_uri_and_a_named_reason(
        self, ref_id: str, reason: str
    ) -> None:
        assert official_act_eli(ref_id) is None
        assert act_eli(ref_id) == EliOutcome(ref_id=ref_id, uri=None, reason=reason)


_VALID_REFS = st.builds(
    lambda kind, day, number: f"{kind}/{day.isoformat()}-{number}",
    st.sampled_from(["lov", "forskrift"]),
    st.dates(min_value=date(1850, 1, 1), max_value=date(2099, 12, 31)),
    st.integers(min_value=1, max_value=99_999),
)


def _ref_from_uri(uri: str) -> str:
    """The inverse of the mapping, as a test oracle."""
    kind, year, month, day, number = uri.removeprefix("https://lovdata.no/eli/").split("/")
    return f"{kind}/{year}-{month}-{day}-{number}"


class TestProperties:
    @given(_VALID_REFS)
    def test_every_valid_ref_maps_and_round_trips(self, ref_id: str) -> None:
        uri = official_act_eli(ref_id)
        assert uri is not None
        assert _ref_from_uri(uri) == ref_id

    @given(_VALID_REFS)
    def test_an_act_uri_never_names_a_provision(self, ref_id: str) -> None:
        """Exactly type/yyyy/mm/dd/number: no level, language or version segment."""
        uri = official_act_eli(ref_id)
        assert uri is not None
        assert ELI_ACT_PATTERN.fullmatch(uri)
        assert not re.search(r"§|paragraf|section|chapter|kapittel|/nor\b|#", uri)

    @given(_VALID_REFS, st.text(min_size=1, max_size=12))
    def test_anything_appended_to_a_ref_is_not_mapped(self, ref_id: str, tail: str) -> None:
        assert official_act_eli(ref_id + tail) is None or (tail.isdecimal() and tail.isascii())

    @given(st.text(max_size=40))
    def test_arbitrary_text_is_never_a_lovspor_or_pseudo_eli(self, text: str) -> None:
        uri = official_act_eli(text)
        assert uri is None or uri.startswith("https://lovdata.no/eli/")

    def test_the_source_ref_id_is_preserved_unchanged(self) -> None:
        outcome = act_eli("lov/2005-05-20-28")
        assert outcome.ref_id == "lov/2005-05-20-28"


class TestAudit:
    def test_counts_reasons_and_samples_deterministically(self) -> None:
        refs = [
            "lov/2005-05-20-28",
            "forskrift/2016-12-15-1600",
            "lov/1687-04-15",
            "lov/1814-05-17",
            "lov/1814-05-17",
            "lov/2005-02-30-1",
            "lov/2005-05-20-28/§3",
        ]
        audit = audit_ref_ids(refs, sample_size=1)
        again = audit_ref_ids(list(reversed(refs)), sample_size=1)

        assert audit == again
        assert audit.total == 7
        assert audit.distinct == 6
        assert audit.by_reason == {
            "invalid_date": 1,
            "mapped": 2,
            "no_serial_number": 3,
            "unsupported_shape": 1,
        }
        assert audit.padding_sensitive == 1
        assert audit.samples["mapped"] == [
            EliOutcome(
                ref_id="forskrift/2016-12-15-1600",
                uri="https://lovdata.no/eli/forskrift/2016/12/15/1600",
                reason="mapped",
            )
        ]
        assert audit.samples["no_serial_number"][0].ref_id == "lov/1687-04-15"

    def test_an_empty_corpus_audits_to_zero(self) -> None:
        audit = audit_ref_ids([], sample_size=3)
        assert (audit.total, audit.distinct, audit.padding_sensitive) == (0, 0, 0)
        assert audit.by_reason == {}
        assert audit.samples == {}

    def test_the_audit_says_constructed_never_resolved(self) -> None:
        assert "not resolved" in audit_ref_ids(["lov/2005-05-20-28"], sample_size=1).note
