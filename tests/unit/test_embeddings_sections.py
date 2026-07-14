from dataclasses import FrozenInstanceError

import pytest

from lovspor.embeddings.sections import (
    _SECTION_HEADING,
    EmbeddingSection,
    iter_sections,
    strip_frontmatter,
)
from lovspor.mcp import _parse_sections as parse_mcp_sections


def test_strip_frontmatter_removes_yaml_block() -> None:
    text = "---\ntitle: Test\n---\n\n# Test\nBody\n"

    assert strip_frontmatter(text) == "# Test\nBody\n"


def test_strip_frontmatter_returns_input_when_no_closed_block() -> None:
    text = "---\ntitle: Test\n# Test\n"

    assert strip_frontmatter(text) == text


def test_strip_frontmatter_only_strips_leading_newlines_after_closing_marker() -> None:
    text = "---\ntitle: Test\n---\n\nX-body\n"

    assert strip_frontmatter(text) == "X-body\n"


def test_iter_sections_extracts_section_heading_and_body() -> None:
    body = "\n".join(
        [
            "# Arbeidsmiljoloven",
            "## Kapittel 15",
            "### § 15-3. Oppsigelsesfrister",
            "Første ledd.",
            "Andre ledd.",
            "### § 15-4. Formkrav",
            "Skriftlig oppsigelse.",
        ],
    )

    sections = iter_sections(body)

    assert sections == [
        EmbeddingSection(
            section_id="15-3",
            text="### § 15-3. Oppsigelsesfrister\nFørste ledd.\nAndre ledd.",
        ),
        EmbeddingSection(
            section_id="15-4",
            text="### § 15-4. Formkrav\nSkriftlig oppsigelse.",
        ),
    ]


def test_iter_sections_closes_current_section_on_non_section_heading() -> None:
    body = "\n".join(
        [
            "### § 1. Første",
            "Tekst som skal beholdes.",
            "### Merknad",
            "Tekst som ikke hører til noen paragraf.",
            "### § 2. Andre",
            "Ny paragraf.",
            "## Nytt kapittel",
            "Kapitteltekst som ignoreres.",
        ],
    )

    sections = iter_sections(body)

    assert sections == [
        EmbeddingSection(
            section_id="1",
            text="### § 1. Første\nTekst som skal beholdes.",
        ),
        EmbeddingSection(section_id="2", text="### § 2. Andre\nNy paragraf."),
    ]


def test_iter_sections_accepts_bare_and_lettered_section_ids() -> None:
    body = "\n".join(
        [
            "### § 5",
            "Ingen tittel.",
            "### § 5a. Bokstavparagraf",
            "Med tittel.",
        ],
    )

    sections = iter_sections(body)

    assert sections == [
        EmbeddingSection(section_id="5", text="### § 5\nIngen tittel."),
        EmbeddingSection(section_id="5a", text="### § 5a. Bokstavparagraf\nMed tittel."),
    ]


def test_iter_sections_extracts_h2_flat_law_sections() -> None:
    """Flat acts with no chapter level render sections at H2 (``## § N.``), not
    H3. Without matching these, ~18% of acts produce zero embeddings and are
    invisible to semantic_search (parity with mcp._parse_sections)."""
    body = "\n".join(
        [
            "# Vrakloven",
            "## § 1. Formål",
            "Loven gjelder berging.",
            "## § 14.",
            "Titlelaus paragraf.",
        ],
    )

    sections = iter_sections(body)

    assert sections == [
        EmbeddingSection(
            section_id="1",
            text="## § 1. Formål\nLoven gjelder berging.",
        ),
        EmbeddingSection(section_id="14", text="## § 14.\nTitlelaus paragraf."),
    ]


def test_iter_sections_h2_chapter_still_closes_section() -> None:
    """Regression: ``## Kapittel`` (an H2 line without ``§``) is still a chapter
    boundary that closes the current section, not a section itself."""
    body = "## § 1. A\nTekst.\n## Kapittel 2\nIgnorert."

    assert iter_sections(body) == [EmbeddingSection(section_id="1", text="## § 1. A\nTekst.")]


def test_iter_sections_h3_non_section_heading_closes_without_opening() -> None:
    body = "### § 1. A\nTekst.\n### Merknad\nIgnorert.\n### § 2. B\nMer."

    assert iter_sections(body) == [
        EmbeddingSection(section_id="1", text="### § 1. A\nTekst."),
        EmbeddingSection(section_id="2", text="### § 2. B\nMer."),
    ]


@pytest.mark.parametrize(
    ("line", "section_id", "title"),
    [
        ("## § 1. Formål", "1", "Formål"),
        ("## § 14.", "14", None),
        ("## § 13. (Opphevet)", "13", "(Opphevet)"),
        ("### § 5-12. Title", "5-12", "Title"),
        ("### § 5", "5", None),
    ],
)
def test_embedding_section_heading_regex_matches_real_heading_shapes(
    line: str,
    section_id: str,
    title: str | None,
) -> None:
    match = _SECTION_HEADING.match(line)

    assert match is not None
    assert match.group(1) == section_id
    assert match.group(2) == title


def test_embedding_section_heading_regex_does_not_match_chapter_heading() -> None:
    assert _SECTION_HEADING.match("## Kapittel 1.") is None


@pytest.mark.parametrize(
    "line",
    [
        "## § 1. Formål",
        "## § 14.",
        "## § 13. (Opphevet)",
        "### § 5-12. Title",
        "### § 5",
        "## Kapittel 1.",
        "### Merknad",
    ],
)
def test_embedding_and_mcp_splitters_agree_on_section_heading_matrix(line: str) -> None:
    embedding_match = _SECTION_HEADING.match(line)
    mcp_sections = parse_mcp_sections(f"{line}\n\nBody.\n")

    if embedding_match is None:
        assert mcp_sections == []
    else:
        assert {s["section_id"] for s in mcp_sections} == {embedding_match.group(1)}


def test_iter_sections_returns_empty_when_no_section_headings() -> None:
    assert iter_sections("# Lov\n\n## Kapittel\n\nVanlig tekst.") == []


def test_iter_sections_continues_after_non_section_heading() -> None:
    body = "\n".join(
        [
            "### § 1. Første",
            "Første tekst.",
            "### Merknad",
            "Ignored.",
            "### § 2. Andre",
            "Andre tekst.",
            "### Annet",
            "Ignored again.",
            "### § 3. Tredje",
            "Tredje tekst.",
        ],
    )

    assert [section.section_id for section in iter_sections(body)] == ["1", "2", "3"]


def test_embedding_section_is_immutable() -> None:
    section = EmbeddingSection(section_id="1", text="tekst")

    with pytest.raises(FrozenInstanceError):
        section.text = "ny tekst"
