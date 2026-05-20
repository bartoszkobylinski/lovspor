from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from lovspor.rendering.frontmatter import serialize_frontmatter


class _Simple(BaseModel):
    title: str
    count: int


class _Full(BaseModel):
    title: str
    count: int
    ratio: float
    active: bool
    missing: str | None
    tags: list[str]
    empty_list: list[str]
    when: datetime


def test_serialize_delimits_with_triple_dash() -> None:
    out = serialize_frontmatter(_Simple(title="x", count=1))
    assert out.startswith("---\n")
    assert "\n---\n" in out


def test_serialize_ends_with_trailing_newline() -> None:
    out = serialize_frontmatter(_Simple(title="x", count=1))
    assert out.endswith("\n")


def test_serialize_preserves_pydantic_field_order() -> None:
    out = serialize_frontmatter(_Simple(title="Lov", count=42))
    expected = '---\ntitle: "Lov"\ncount: 42\n---\n'
    assert out == expected


def test_serialize_is_deterministic_across_calls() -> None:
    model = _Simple(title="same", count=7)
    assert serialize_frontmatter(model) == serialize_frontmatter(model)


def test_serialize_quotes_strings_with_double_quotes() -> None:
    out = serialize_frontmatter(_Simple(title="Hello", count=0))
    assert 'title: "Hello"' in out


def test_serialize_escapes_quote_and_backslash_in_string() -> None:
    out = serialize_frontmatter(_Simple(title='a"b\\c', count=0))
    assert r'title: "a\"b\\c"' in out


def test_serialize_escapes_control_characters_in_string() -> None:
    out = serialize_frontmatter(_Simple(title="line1\nline2\rTabbed\tEnd", count=0))

    assert out == '---\ntitle: "line1\\nline2\\rTabbed\\tEnd"\ncount: 0\n---\n'


def test_serialize_emits_int_without_quotes() -> None:
    out = serialize_frontmatter(_Simple(title="x", count=1234))
    assert "count: 1234" in out


def test_serialize_emits_bool_as_lowercase() -> None:
    class _B(BaseModel):
        flag_on: bool
        flag_off: bool

    out = serialize_frontmatter(_B(flag_on=True, flag_off=False))
    assert "flag_on: true" in out
    assert "flag_off: false" in out


def test_serialize_emits_none_as_null() -> None:
    class _N(BaseModel):
        missing: str | None

    out = serialize_frontmatter(_N(missing=None))
    assert "missing: null" in out


def test_serialize_emits_datetime_as_iso_string() -> None:
    class _D(BaseModel):
        when: datetime

    when = datetime(2026, 4, 22, 1, 31, 0, tzinfo=UTC)
    out = serialize_frontmatter(_D(when=when))
    assert 'when: "2026-04-22T01:31:00+00:00"' in out


def test_serialize_emits_list_of_strings_as_yaml_sequence() -> None:
    class _L(BaseModel):
        tags: list[str]

    out = serialize_frontmatter(_L(tags=["one", "two"]))
    assert 'tags:\n  - "one"\n  - "two"' in out


def test_serialize_emits_empty_list_inline() -> None:
    class _L(BaseModel):
        tags: list[str]

    out = serialize_frontmatter(_L(tags=[]))
    assert "tags: []" in out


def test_serialize_full_model_produces_expected_output() -> None:
    model = _Full(
        title="Skatteloven",
        count=1,
        ratio=0.5,
        active=True,
        missing=None,
        tags=["a", "b"],
        empty_list=[],
        when=datetime(2026, 4, 22, tzinfo=UTC),
    )
    out = serialize_frontmatter(model)
    expected = (
        "---\n"
        'title: "Skatteloven"\n'
        "count: 1\n"
        "ratio: 0.5\n"
        "active: true\n"
        "missing: null\n"
        "tags:\n"
        '  - "a"\n'
        '  - "b"\n'
        "empty_list: []\n"
        'when: "2026-04-22T00:00:00+00:00"\n'
        "---\n"
    )
    assert out == expected


def test_serialize_handles_norwegian_utf8_in_strings() -> None:
    out = serialize_frontmatter(_Simple(title="Skatteloven — skatt av formue og inntekt", count=0))
    assert "Skatteloven — skatt av formue og inntekt" in out


@pytest.mark.parametrize(
    "special",
    [" ", ":", "#", "-", "*"],
)
def test_serialize_preserves_problematic_characters_in_quoted_string(
    special: str,
) -> None:
    """Printable YAML-significant characters that are safe inside a
    double-quoted scalar must pass through unchanged. Control characters
    (\\n, \\r, \\t) are escaped and covered by dedicated tests above."""
    out = serialize_frontmatter(_Simple(title=f"pre{special}post", count=0))
    assert f"pre{special}post" in out
