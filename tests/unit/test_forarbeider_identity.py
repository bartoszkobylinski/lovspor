"""The identity a regjeringen.no URL and an official reference carry (#738)."""

import pytest

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import (
    OfficialReference,
    chapter_of,
    parse_reference,
    parse_source_url,
)

CANONICAL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
REFERENCE = OfficialReference(document_type="prop-l", session="2099-2100", proposition_number=901)


def test_canonical_url_gives_identity_and_no_chapter() -> None:
    locator = parse_source_url(CANONICAL)
    assert locator.reference == REFERENCE
    assert locator.document_id == 9000001
    assert locator.chapter is None
    assert locator.canonical_url == CANONICAL
    assert locator.page_url == CANONICAL


def test_chapter_url_keeps_the_page_and_the_canonical_apart() -> None:
    locator = parse_source_url(f"{CANONICAL}?ch=12")
    assert locator.chapter == 12
    assert locator.canonical_url == CANONICAL
    assert locator.page_url == f"{CANONICAL}?ch=12"


def test_prop_ls_is_its_own_type() -> None:
    url = "https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/"
    assert parse_source_url(url).reference.document_type == "prop-ls"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://www.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1/", "invalid_source_url"),
        ("https://regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1/", "host_not_allowed"),
        (
            "https://www.regjeringen.no.evil.test/no/dokumenter/prop.-1-l-20252026/id1/",
            "host_not_allowed",
        ),
        (
            "https://user@www.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1/",
            "host_not_allowed",
        ),
        (
            "https://www.regjeringen.no:8443/no/dokumenter/prop.-1-l-20252026/id1/",
            "host_not_allowed",
        ),
        ("https://WWW.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1/", "host_not_allowed"),
        ("https://[bad/no/dokumenter/prop.-1-l-20252026/id1/", "invalid_source_url"),
        (f"{CANONICAL}#kap1", "invalid_source_url"),
        (f"{CANONICAL}\n", "invalid_source_url"),
        ("https://www.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1", "invalid_source_url"),
        ("https://www.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id0/", "invalid_source_url"),
        ("https://www.regjeringen.no/no/tema/prop.-1-l-20252026/id1/", "invalid_source_url"),
        ("https://www.regjeringen.no/nn/dokumenter/prop.-1-l-20252026/id1/", "invalid_source_url"),
        ("https://www.regjeringen.no/no/dokumenter/prop.-1-l-20252027/id1/", "invalid_source_url"),
        (f"{CANONICAL}?ch=0", "unexpected_query"),
        (f"{CANONICAL}?ch=x", "unexpected_query"),
        (f"{CANONICAL}?ch=1&utm=2", "unexpected_query"),
        (f"{CANONICAL}?q=1", "unexpected_query"),
        (
            "https://www.regjeringen.no/no/dokumenter/prop.-1-s-20252026/id1/",
            "unsupported_document_type",
        ),
        (
            "https://www.regjeringen.no/no/dokumenter/prop.-1-20252026/id1/",
            "unsupported_document_type",
        ),
        ("https://www.regjeringen.no/no/dokumenter/nou-2024-14/id1/", "unsupported_document_type"),
        (
            "https://www.regjeringen.no/no/dokumenter/otprp-nr-1-20072008-/id1/",
            "unsupported_document_type",
        ),
        (
            "https://www.regjeringen.no/no/dokumenter/rundskriv-h-1/id1/",
            "unsupported_document_type",
        ),
    ],
)
def test_url_refusals(url: str, reason: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_source_url(url)
    assert caught.value.reason == RefusalReason(reason)


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("http://www.regjeringen.no/no/dokumenter/prop.-1-l-20252026/id1/", None),
        (f"{CANONICAL}#kap1", None),
        ("https://[bad/no/dokumenter/prop.-1-l-20252026/id1/", None),
        ("https://www.regjeringen.no/no/tema/x/id1/", None),
        ("https://www.regjeringen.no/no/dokumenter/prop.-1-l-20252027/id1/", None),
        (f"{CANONICAL}?ch=x", "ch=x"),
        ("https://www.regjeringen.no/no/dokumenter/nou-2024-14/id1/", "nou-2024-14"),
    ],
)
def test_url_refusal_details(url: str, detail: str | None) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_source_url(url)
    assert caught.value.detail == (url if detail is None else detail)


def test_refusal_message_names_reason_and_detail() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_source_url("https://example.org/x")
    assert str(caught.value) == "host_not_allowed: example.org"
    assert caught.value.detail == "example.org"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prop. 901 L (2099\u20132100)", REFERENCE),
        (
            "Prop. 54 LS (2025\u20132026)",
            OfficialReference(document_type="prop-ls", session="2025-2026", proposition_number=54),
        ),
    ],
)
def test_reference_reads(text: str, expected: OfficialReference) -> None:
    assert parse_reference(text) == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Prop. 1 S (2025\u20132026)", "unsupported_document_type"),
        ("Prop. 1 LP (2025\u20132026)", "unsupported_document_type"),
        ("NOU 2024: 14", "reference_missing"),
        ("Ot.prp. nr. 1 (2007\u20132008)", "reference_missing"),
        ("Prop. 1 (2025\u20132026)", "reference_missing"),
        ("Prop. 0 L (2025\u20132026)", "reference_missing"),
        ("Prop. 1 L (2025-2026)", "reference_missing"),
        ("Prop. 1 L (2025\u20132027)", "conflicting_identity"),
        ("Se Prop. 1 L (2025\u20132026)", "reference_missing"),
        ("", "reference_missing"),
    ],
)
def test_reference_refusals(text: str, reason: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_reference(text)
    assert caught.value.reason == RefusalReason(reason)


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("Prop. 1 S (2025\u20132026)", "S"),
        ("NOU 2024: 14", "official reference"),
        ("Prop. 1 L (2025\u20132027)", "2025-2027"),
    ],
)
def test_reference_refusal_details(text: str, detail: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_reference(text)
    assert caught.value.detail == detail


def test_chapter_of_accepts_a_relative_link_of_the_same_document() -> None:
    parent = parse_source_url(f"{CANONICAL}?ch=9")
    href = "/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=3"
    assert chapter_of(href, parent) == (3, f"{CANONICAL}?ch=3")


@pytest.mark.parametrize(
    "href",
    [
        "/no/dokumenter/prop.-901-l-20992100/id9000009/?ch=3",
        "/no/dokumenter/prop.-902-l-20992100/id9000001/?ch=3",
        "https://evil.test/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=3",
        "/no/dokumenter/prop.-901-l-20992100/id9000001/",
        "/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=3#kap3",
        "javascript:void(0)",
    ],
)
def test_chapter_of_refuses_anything_else(href: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        chapter_of(href, parse_source_url(f"{CANONICAL}?ch=1"))
    assert caught.value.reason is RefusalReason.CHAPTER_FOREIGN
    assert caught.value.detail == href
