"""Guard grammar assumptions behind the registered start-tag equivalents."""

from lovspor.regjeringen_fetch import normalize


def test_tag_initial_letters_are_all_consumed_as_tag_name_bytes() -> None:
    # The opening + 2 and max() equivalences depend on every accepted first
    # letter being consumed by the tag-name scan.
    assert normalize._LETTERS <= normalize._TAG_NAME
    assert {bytes([byte]) for byte in normalize._LETTERS} == normalize._LETTER_BYTES


def test_less_than_stops_unquoted_values_and_attribute_names() -> None:
    # The registered backwards resume after '>' assumes neither kind of
    # final byte can be '<', which would make the scanner revisit a tag.
    assert ord("<") in normalize._NAME_STOP
    assert ord("<") in normalize._UNQUOTED_STOP


def test_valueless_name_does_not_count_as_a_csp_nonce_meta() -> None:
    page = b'<meta name content="AAAA"><meta name="csp-nonce" content="QUJD">'
    assert normalize.normalize_regjeringen_html(page) == (
        b'<meta name content="AAAA"><meta name="csp-nonce" content="NONCE">'
    )
