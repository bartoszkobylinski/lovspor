"""The nonce-normalized comparison digest (#738, FC1a; architecture §3.3).

Synthetic pages only. The rule replaces exactly two kinds of value with
``NONCE``: a strict ``nonce="…"`` attribute value, and the ``content`` of the
one ``<meta name="csp-nonce">``. Anything else, including any nonce outside
the grammar, is left as served, so a change there still changes the digest.
"""

import hashlib
import time

import pytest

from lovspor.regjeringen_fetch.normalize import (
    NORMALIZATION_RULE,
    normalize_regjeringen_html,
    normalized_sha256,
)


def _page(meta: str, script: str, body: str = "<p>Synthetic chapter.</p>") -> bytes:
    return (
        "<!DOCTYPE html><html><head>"
        f'<meta name="csp-nonce" content="{meta}">'
        f'<script nonce="{script}" src="/a.js"></script>'
        f'<style nonce="{script}">p{{}}</style>'
        f"</head><body>{body}</body></html>"
    ).encode()


FIRST = _page("QUJDRA==", "QUJDRA==")
SECOND = _page("WFlaVzEy&#x2B;/9", "WFlaVzEy&#x2B;/9")


def _norm_equal(a: bytes, b: bytes) -> bool:
    return normalized_sha256(a) == normalized_sha256(b)


def test_pages_differing_only_in_nonces_share_a_normalized_digest() -> None:
    assert hashlib.sha256(FIRST).hexdigest() != hashlib.sha256(SECOND).hexdigest()
    assert _norm_equal(FIRST, SECOND)
    assert normalize_regjeringen_html(FIRST) == _page("NONCE", "NONCE")


def test_the_normalized_digest_is_sha256_of_the_normalized_bytes() -> None:
    expected = hashlib.sha256(normalize_regjeringen_html(FIRST)).hexdigest()
    assert normalized_sha256(FIRST) == expected


def _value_bytes(page: bytes, value: bytes) -> set[int]:
    """Indices inside every occurrence of ``value``: changing those is a nonce change."""
    spans: set[int] = set()
    start = page.find(value)
    while start != -1:
        spans.update(range(start, start + len(value)))
        start = page.find(value, start + 1)
    return spans


def test_any_other_byte_change_changes_the_normalized_digest() -> None:
    normalized = normalize_regjeringen_html(FIRST)
    nonce = _value_bytes(FIRST, b"QUJDRA==")
    vanished = []
    for index in sorted(set(range(len(FIRST))) - nonce):
        for flip in (0x01, 0x20, 0x80):
            mutated = bytearray(FIRST)
            mutated[index] ^= flip
            if normalize_regjeringen_html(bytes(mutated)) == normalized:
                vanished.append((index, flip))
    assert vanished == []


def test_inserting_or_deleting_any_byte_outside_a_nonce_changes_the_digest() -> None:
    normalized = normalize_regjeringen_html(FIRST)
    nonce = _value_bytes(FIRST, b"QUJDRA==")
    vanished = []
    for index in range(len(FIRST) + 1):
        inserted = FIRST[:index] + b" " + FIRST[index:]
        if normalize_regjeringen_html(inserted) == normalized:
            vanished.append(("insert", index))
        if index < len(FIRST) and index not in nonce:
            deleted = FIRST[:index] + FIRST[index + 1 :]
            if normalize_regjeringen_html(deleted) == normalized:
                vanished.append(("delete", index))
    assert vanished == []


def test_a_body_change_beside_a_nonce_change_still_differs() -> None:
    other = _page("WFlaVzEy", "WFlaVzEy", body="<p>Synthetic chapter!</p>")
    assert not _norm_equal(FIRST, other)


def test_normalization_is_idempotent() -> None:
    once = normalize_regjeringen_html(SECOND)
    assert normalize_regjeringen_html(once) == once


def test_a_page_without_nonces_is_returned_unchanged() -> None:
    page = b"<!DOCTYPE html><html><body><p>plain</p></body></html>"
    assert normalize_regjeringen_html(page) == page


def test_the_rule_has_an_explicit_version() -> None:
    assert NORMALIZATION_RULE == "regjeringen-csp-nonce/1"


# Grammar: anything outside it is left untouched, so its change is visible.


@pytest.mark.parametrize(
    "template",
    [
        "<script nonce='{v}'></script>",  # single quotes
        "<script nonce={v}></script>",  # unquoted
        '<script NONCE="{v}"></script>',  # name not lowercase
        '<script nonce = "{v}"></script>',  # spaces around =
        '<script data-nonce="{v}"></script>',  # another attribute
        '<script nonce="{v}%"></script>',  # value outside the alphabet
        '<script nonce="{v}&amp;"></script>',  # another character reference
        '<script nonce="=={v}"></script>',  # padding not at the end
        '<script nonce="{v}===="></script>',  # too much padding
        '<script nonce="{v}" nonce="{v}"></script>',  # duplicate attribute
        '<script nonce="{v}"class="x"></script>',  # no whitespace between attributes
        "<script title='a nonce=\"{v}\"'></script>",  # text inside another value
        '<p>see nonce="{v}" here</p>',  # text, not a tag
        '<!-- <script nonce="{v}"></script> -->',  # inside a comment
        '<!-- <script nonce="{v}"></script>',  # inside an unterminated comment
        "<script>var s = '<b nonce=\"{v}\">';</script>",  # inside script text
        '<textarea><b nonce="{v}"></b></textarea>',  # inside raw text
        '</script nonce="{v}">',  # an end tag
        "</p title='<a nonce=\"{v}\">'>",  # inside a value of an end tag
        '<!x <a nonce="{v}">',  # inside a bogus comment
        '<?x <a nonce="{v}">',  # inside a processing-instruction-like bogus comment
        '<!DOCTYPE html <a nonce="{v}">',  # inside a doctype
        '<script>a</scriptx><b nonce="{v}">',  # "</scriptx" does not close a script
        '<script nonce="{v}"',  # an unterminated tag
    ],
)
def test_a_nonce_outside_the_grammar_is_left_untouched(template: str) -> None:
    first = template.format(v="QUJD").encode()
    second = template.format(v="WFla").encode()
    assert normalize_regjeringen_html(first) == first
    assert not _norm_equal(first, second)


def test_an_empty_nonce_is_left_untouched() -> None:
    page = b'<script nonce=""></script>'
    assert normalize_regjeringen_html(page) == page


@pytest.mark.parametrize(
    "tag",
    [
        '<script\tnonce="{v}">',
        '<script\nnonce="{v}" async>',
        '<script src="/x.js" nonce="{v}"/>',
        '<link rel="preload" nonce="{v}">',
        '<script type="a>b" nonce="{v}">',  # ">" inside another quoted value
    ],
)
def test_a_grammatical_nonce_attribute_is_normalized(tag: str) -> None:
    first = tag.format(v="QUJD").encode()
    assert normalize_regjeringen_html(first) == tag.format(v="NONCE").encode()
    assert _norm_equal(first, tag.format(v="WFla&#x2B;").encode())


def test_script_text_after_a_normalized_tag_is_kept_as_served() -> None:
    page = b'<script nonce="QUJD">var x = "nonce=\\"QUJD\\"";</script><p>after</p>'
    expected = b'<script nonce="NONCE">var x = "nonce=\\"QUJD\\"";</script><p>after</p>'
    assert normalize_regjeringen_html(page) == expected


def test_a_tag_after_a_closed_raw_text_element_is_normalized() -> None:
    page = b'<STYLE>p{}</style><script nonce="QUJD"></script>'
    assert normalize_regjeringen_html(page) == b'<STYLE>p{}</style><script nonce="NONCE"></script>'


def test_an_unclosed_script_stops_normalization_for_the_rest() -> None:
    page = b'<script>never closed <script nonce="QUJD">'
    assert normalize_regjeringen_html(page) == page


# The csp-nonce meta element: exactly one, strict form, or nothing changes.


def test_a_missing_csp_meta_changes_nothing_else() -> None:
    page = b'<html><head><meta name="viewport" content="QUJD"></head></html>'
    assert normalize_regjeringen_html(page) == page


@pytest.mark.parametrize(
    "meta",
    [
        "<meta name='csp-nonce' content=\"{v}\">",  # name not double-quoted
        '<meta name="CSP-NONCE" content="{v}">',  # name value not exact
        "<meta name=\"csp-nonce\" content='{v}'>",  # content not double-quoted
        '<meta name="csp-nonce" content="{v}!">',  # content outside the alphabet
        '<meta name="csp-nonce" content="{v}" content="{v}">',  # duplicate content
        '<meta name="csp-nonce" name="csp-nonce" content="{v}">',  # duplicate name
        '<meta name="csp-nonce">',  # no content
        '<META name="csp-nonce" content="{v}">',  # tag name not lowercase
    ],
)
def test_a_malformed_csp_meta_is_left_untouched(meta: str) -> None:
    first = meta.format(v="QUJD").encode()
    second = meta.format(v="WFla").encode()
    assert normalize_regjeringen_html(first) == first
    if first != second:
        assert not _norm_equal(first, second)


def test_the_meta_content_is_normalized_in_any_attribute_order() -> None:
    page = b'<meta content="QUJD" name="csp-nonce">'
    assert normalize_regjeringen_html(page) == b'<meta content="NONCE" name="csp-nonce">'


def test_duplicate_csp_metas_are_both_left_untouched() -> None:
    page = (
        b'<meta name="csp-nonce" content="QUJD">'
        b'<meta name="csp-nonce" content="WFla">'
        b'<script nonce="QUJD"></script>'
    )
    expected = (
        b'<meta name="csp-nonce" content="QUJD">'
        b'<meta name="csp-nonce" content="WFla">'
        b'<script nonce="NONCE"></script>'
    )
    assert normalize_regjeringen_html(page) == expected


def test_a_second_csp_meta_in_any_spelling_makes_the_first_ambiguous() -> None:
    page = b'<meta name="csp-nonce" content="QUJD"><meta name=\'CSP-Nonce\' content="x">'
    assert normalize_regjeringen_html(page) == page


def test_a_csp_meta_inside_a_comment_is_not_counted() -> None:
    page = b'<!-- <meta name="csp-nonce" content="AAAA"> --><meta name="csp-nonce" content="QUJD">'
    expected = (
        b'<!-- <meta name="csp-nonce" content="AAAA"> --><meta name="csp-nonce" content="NONCE">'
    )
    assert normalize_regjeringen_html(page) == expected


def test_non_ascii_and_invalid_utf8_bytes_are_preserved() -> None:
    page = b'<p>\xc3\xa6\xff\xfe</p><script nonce="QUJD"></script>'
    expected = b'<p>\xc3\xa6\xff\xfe</p><script nonce="NONCE"></script>'
    assert normalize_regjeringen_html(page) == expected


@pytest.mark.parametrize(
    "unit",
    [
        b"<a b=c ",  # tags that never close, each starting inside the last
        b'<a x="<a x=" ',  # "<" inside quoted values
        b"</a b=c ",  # end tags that never close
        b" x",  # one tag with endless attributes
        b"<!--",
        b"<",
    ],
)
def test_adversarial_markup_is_scanned_in_linear_time(unit: bytes) -> None:
    """A regex scanner backtracked here: 28 kB took 3.8 s, so 56 kB would take ~15 s."""
    page = b"<a" + unit * (56_000 // len(unit))
    started = time.perf_counter()
    assert normalize_regjeringen_html(page) == page
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize(
    "element",
    ["script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript"],
)
def test_raw_text_preserves_embedded_markup_and_resumes_after_closing_tag(element: str) -> None:
    """The rule excludes raw text, including apparent CSP metadata inside it."""
    embedded = b'<meta name="csp-nonce" content="QUJD"><b nonce="QUJD">'
    prefix = f'<{element.upper()} nonce="QUJD">'.encode()
    closing = f"</{element.capitalize()} >".encode()
    tail = b'<meta name="csp-nonce" content="WFla"><b nonce="WFla">'
    page = prefix + embedded + closing + tail
    expected = (
        prefix.replace(b'nonce="QUJD"', b'nonce="NONCE"')
        + embedded
        + closing
        + b'<meta name="csp-nonce" content="NONCE"><b nonce="NONCE">'
    )
    assert normalize_regjeringen_html(page) == expected
    assert normalize_regjeringen_html(expected) == expected


def test_plaintext_keeps_all_remaining_markup_as_served() -> None:
    page = b'<PLAINTEXT nonce="QUJD"><b nonce="WFla"></plaintext><b nonce="WFla">'
    expected = b'<PLAINTEXT nonce="NONCE"><b nonce="WFla"></plaintext><b nonce="WFla">'
    assert normalize_regjeringen_html(page) == expected


@pytest.mark.parametrize("duplicate", [b'NONCE="WFla"', b"Nonce='WFla'", b"nonce=WFla", b"NoNcE"])
def test_duplicate_nonce_in_any_spelling_prevents_replacement(duplicate: bytes) -> None:
    page = b'<b nonce="QUJD" ' + duplicate + b'><i nonce="WFla">'
    expected = b'<b nonce="QUJD" ' + duplicate + b'><i nonce="NONCE">'
    assert normalize_regjeringen_html(page) == expected


def test_meta_nonce_and_content_are_replaced_without_losing_intervening_bytes() -> None:
    page = b'<meta nonce="QUJDRA==" data-x="keep" content="WFla&#x2B;" name="csp-nonce">'
    expected = b'<meta nonce="NONCE" data-x="keep" content="NONCE" name="csp-nonce">'
    assert normalize_regjeringen_html(page) == expected
    assert normalize_regjeringen_html(expected) == expected
