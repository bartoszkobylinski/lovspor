"""The local-regulation renderer (ADR-0016 Decision 3, slice S2).

The front-matter checks mirror lovverk's ``scripts/check_corpus_integrity.py``
(lovverk PR #11, ``LOCAL_FRONT_MATTER_KEYS`` and the fixed values). When a
lovverk checkout is named by ``LOVVERK_CHECKOUT``, the last test also runs that
script itself against a temporary corpus holding the rendered fixture.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from pydantic import ValidationError

from lovspor.errors import PromotionRenderError
from lovspor.promotion import (
    Authority,
    AuthorityType,
    ExtractedDocument,
    LocalDocument,
    MintedIdentity,
    ObservedSource,
    mint_identity,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from lovspor.promotion.render import LOCAL_RENDERER_VERSION, render_local_regulation
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

OSLO = Authority(id="0301", type=AuthorityType.KOMMUNE, name="Oslo", klass_version="2024")
FRONT_MATTER_KEYS = (
    "id",
    "slug",
    "type",
    "ref_id",
    "title",
    "authority",
    "hjemmel",
    "vedtatt",
    "vedtatt_av",
    "ikraft",
    "ikraft_text",
    "version",
    "content_hash",
    "observed_at_first",
    "source_url",
    "source_sha256",
    "source_provider",
    "source_license",
    "basis",
    "asserted",
    "language",
)
SOURCE = ObservedSource(
    observed_at_first=datetime(2026, 8, 19, 17, 17, 23, tzinfo=timezone(timedelta(hours=2))),
    source_url="https://www.eksempel.kommune.no/forskrifter/renovasjon",
    source_sha256="ab" * 32,
)


def _document(lines: tuple[str, ...] = REGULATION_LINES, version: int = 1) -> LocalDocument:
    extracted = extract_regulation(html_page(lines), "text/html; charset=utf-8")
    assert isinstance(extracted, ExtractedDocument), extracted
    identity = mint_identity(extracted.regulation, OSLO)
    assert isinstance(identity, MintedIdentity), identity
    return LocalDocument(
        identity=identity,
        extracted=extracted,
        slug="renovasjon-og-slam",
        version=version,
        source=SOURCE,
    )


def _front_matter(markdown: str) -> tuple[list[str], dict[str, object]]:
    head = markdown.split("---\n")[1]
    keys = [line.split(":", 1)[0] for line in head.splitlines() if not line.startswith(" ")]
    return keys, yaml.safe_load(head)


def test_rendering_is_byte_identical_across_runs() -> None:
    first = render_local_regulation(_document())
    assert render_local_regulation(_document()) == first
    assert first.encode("utf-8") == render_local_regulation(_document()).encode("utf-8")


def test_front_matter_keys_are_the_fixed_order() -> None:
    keys, _ = _front_matter(render_local_regulation(_document()))
    assert tuple(keys) == FRONT_MATTER_KEYS


def test_front_matter_values() -> None:
    document = _document()
    _, values = _front_matter(render_local_regulation(document))
    assert values == {
        "id": document.identity.doc_id,
        "slug": "renovasjon-og-slam",
        "type": "lokal-forskrift",
        "ref_id": None,
        "title": "Forskrift om renovasjon og slam, Eksempel kommune",
        "authority": {"id": "0301", "type": "kommune", "name": "Oslo", "klass_version": "2024"},
        "hjemmel": [
            "lov 13. mars 1981 nr. 6 om vern mot forurensninger og om avfall "
            "(forurensningsloven) § 30"
        ],
        "vedtatt": "2019-12-12",
        "vedtatt_av": "kommunestyret",
        "ikraft": "2020-01-01",
        "ikraft_text": None,
        "version": 1,
        "content_hash": document.identity.content_hash,
        "observed_at_first": "2026-08-19T15:17:23Z",
        "source_url": "https://www.eksempel.kommune.no/forskrifter/renovasjon",
        "source_sha256": "ab" * 32,
        "source_provider": "Oslo kommune (observed)",
        "source_license": "åndsverkloven § 14",
        "basis": "observed",
        "asserted": False,
        "language": "no",
    }


def test_lf_identity_renders_its_ref_id() -> None:
    lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
    _, values = _front_matter(render_local_regulation(_document(lines)))
    assert (values["id"], values["ref_id"]) == ("lf-20191212-2077", "forskrift/2019-12-12-2077")


def test_no_hjemmel_and_a_verbatim_ikraft_phrase() -> None:
    lines = (
        REGULATION_LINES[0],
        "Vedtatt av kommunestyret 12.12.2019.",
        *REGULATION_LINES[2:-1],
        "Forskriften trer i kraft straks.",
    )
    _, values = _front_matter(render_local_regulation(_document(lines)))
    assert (values["hjemmel"], values["ikraft"], values["ikraft_text"]) == ([], None, "straks")


def test_no_nlod_and_no_lovdata_axes_anywhere() -> None:
    markdown = render_local_regulation(_document())
    assert "nlod" not in markdown.casefold()
    assert "retrieved_at" not in markdown
    assert "observed_at_last" not in markdown


def test_body_renders_title_block_and_sections() -> None:
    body = render_local_regulation(_document()).split("---\n", 2)[2]
    assert body == (
        "\n# Forskrift om renovasjon og slam, Eksempel kommune\n\n"
        f"{REGULATION_LINES[1]}\n\n"
        "## § 1 Formål\n\n"
        f"{REGULATION_LINES[3]}\n\n"
        "## § 2 Virkeområde\n\n"
        f"{REGULATION_LINES[5]}\n\n"
        "## § 3 Ikrafttredelse\n\n"
        "Forskriften trer i kraft 1. januar 2020.\n"
    )


def test_chapters_put_sections_one_level_down() -> None:
    lines = (*REGULATION_LINES[:2], "Kapittel 1. Innledende bestemmelser", *REGULATION_LINES[2:])
    body = render_local_regulation(_document(lines)).split("---\n", 2)[2]
    assert "\n## Kapittel 1. Innledende bestemmelser\n" in body
    assert "\n### § 1 Formål\n" in body


def test_a_long_or_sentence_section_line_stays_a_paragraph() -> None:
    lines = (*REGULATION_LINES[:-1], "§ 4 Forskriften trer i kraft 1. januar 2020.")
    body = render_local_regulation(_document(lines)).split("---\n", 2)[2]
    assert body.endswith("\n\n§ 4 Forskriften trer i kraft 1. januar 2020.\n")


def test_markdown_syntax_at_a_line_start_is_escaped() -> None:
    lines = (*REGULATION_LINES[:4], "- avfall skal sorteres", "# ikke en overskrift")
    lines = (*lines, *REGULATION_LINES[4:])
    body = render_local_regulation(_document(lines)).split("---\n", 2)[2]
    assert "\n\n\\- avfall skal sorteres\n\n\\# ikke en overskrift\n\n" in body


def test_a_content_hash_that_is_not_the_texts_is_refused() -> None:
    document = _document()
    forged = document.identity.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(PromotionRenderError, match="content_hash"):
        render_local_regulation(document.model_copy(update={"identity": forged}))


def test_text_that_mentions_nlod_is_refused() -> None:
    lines = (*REGULATION_LINES, "Data er lisensiert under NLOD 2.0.")
    with pytest.raises(PromotionRenderError, match="NLOD"):
        render_local_regulation(_document(lines))


def test_observation_time_without_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ObservedSource(
            observed_at_first=datetime(2026, 8, 19, 15, 17, 23),
            source_url=SOURCE.source_url,
            source_sha256=SOURCE.source_sha256,
        )


@pytest.mark.parametrize("slug", ["", "a/b", "../x", ".hidden", "two words"])
def test_slug_that_is_not_one_file_name_is_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        LocalDocument.model_validate(_document().model_dump() | {"slug": slug})


def test_versions_are_recorded() -> None:
    assert (LOCAL_RENDERER_VERSION, EXTRACTOR_VERSION) == (1, 2)
    _, values = _front_matter(render_local_regulation(_document(version=3)))
    assert values["version"] == 3


def _corpus(root: Path, document: LocalDocument) -> None:
    """A minimal lovverk tree: an empty root manifest and one local record (S3's job)."""
    path = f"lokale-forskrifter/{document.identity.authority.id}/{document.slug}.md"
    record = {
        "doc_type": "lokal-forskrift",
        "source_dataset": "lokale-forskrifter",
        "status": "current",
        "slug": document.slug,
        "title": document.extracted.fields.title,
        "markdown_path": path,
        "renderer_version": LOCAL_RENDERER_VERSION,
        "last_seen": "2026-10-03",
        "removed_reason": None,
        "authority_id": document.identity.authority.id,
        "authority_type": document.identity.authority.type.value,
        "content_hash": document.identity.content_hash,
        "version": document.version,
        "extractor_version": EXTRACTOR_VERSION,
    }
    empty = {"documents": {}, "generated_at": None, "version": 1}
    local = {"documents": {document.identity.doc_id: record}, "generated_at": None, "version": 1}
    (root / path).parent.mkdir(parents=True)
    (root / path).write_text(render_local_regulation(document), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(empty), encoding="utf-8")
    (root / "lokale-forskrifter" / "manifest.json").write_text(json.dumps(local), encoding="utf-8")


def _integrity_script() -> ModuleType | None:
    checkout = os.environ.get("LOVVERK_CHECKOUT")
    script = Path(checkout) / "scripts" / "check_corpus_integrity.py" if checkout else None
    if script is None or not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("lovverk_integrity", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(_integrity_script() is None, reason="LOVVERK_CHECKOUT names no lovverk")
def test_rendered_fixture_passes_lovverk_integrity_check(tmp_path: Path) -> None:
    module = _integrity_script()
    assert module is not None
    for lines in (
        REGULATION_LINES,
        (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:]),
    ):
        root = tmp_path / str(len(lines))
        _corpus(root, _document(lines))
        assert module.check(root) == []


def test_observed_at_first_is_written_in_utc() -> None:
    assert SOURCE.observed_at_first_utc == "2026-08-19T15:17:23Z"
    assert SOURCE.observed_at_first.astimezone(UTC).hour == 15


def _front_matter_lines(markdown: str) -> list[str]:
    return markdown.split("---\n")[1].splitlines()


def test_front_matter_writes_non_ascii_as_itself_not_as_escapes() -> None:
    """lovverk reads the scalar bytes; ``\\u00e5`` would load equal but is not what is written."""
    lines = _front_matter_lines(render_local_regulation(_document()))
    assert 'source_license: "åndsverkloven § 14"' in lines


def test_hjemmel_flow_list_separates_references_with_a_comma() -> None:
    lines = (
        REGULATION_LINES[0],
        "Hjemmel: LOV-1981-03-13-6-§30, LOV-2018-06-22-83-§8-1",
        *REGULATION_LINES[1:],
    )
    rendered = _front_matter_lines(render_local_regulation(_document(lines)))
    assert 'hjemmel: ["LOV-1981-03-13-6-§30", "LOV-2018-06-22-83-§8-1", ' in rendered[6]


def test_no_enactment_renders_vedtatt_as_null() -> None:
    lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[2:])
    rendered = _front_matter_lines(render_local_regulation(_document(lines)))
    assert (rendered[7], rendered[8]) == ("vedtatt: null", "vedtatt_av: null")


def test_a_section_line_of_exactly_the_heading_limit_is_a_heading() -> None:
    section = "§ 4 Særlige regler for hytter og fritidsboliger i områder uten fast bosetting " + "x"
    section = section.ljust(100, "x")
    assert len(section) == 100
    lines = (*REGULATION_LINES[:-1], section, REGULATION_LINES[-1])
    body = render_local_regulation(_document(lines)).split("---\n", 2)[2]
    assert f"\n\n## {section}\n\n" in body


def test_content_hash_refusal_message_names_the_mismatch() -> None:
    document = _document()
    forged = document.identity.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(PromotionRenderError) as caught:
        render_local_regulation(document.model_copy(update={"identity": forged}))
    assert str(caught.value) == (
        "identity content_hash is not the hash of the extracted text it names"
    )


def test_nlod_refusal_message_cites_the_decision() -> None:
    lines = (*REGULATION_LINES, "Data er lisensiert under NLOD 2.0.")
    with pytest.raises(PromotionRenderError) as caught:
        render_local_regulation(_document(lines))
    assert str(caught.value) == (
        "a local regulation's rendering may not mention NLOD (ADR-0016 Decision 3)"
    )
