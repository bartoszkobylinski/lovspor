from dataclasses import FrozenInstanceError

import pytest

from lovspor.embeddings.sections import EmbeddingSection, iter_sections, strip_frontmatter


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


def test_iter_sections_returns_empty_when_no_section_headings() -> None:
    assert iter_sections("# Lov\n\n## Kapittel\n\nVanlig tekst.") == []


def test_embedding_section_is_immutable() -> None:
    section = EmbeddingSection(section_id="1", text="tekst")

    with pytest.raises(FrozenInstanceError):
        section.text = "ny tekst"
