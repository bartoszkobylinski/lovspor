"""No local regulation reaches the static site (ADR-0016 S11, guard half).

ADR-0016 publishes ``lokale-forskrifter/`` through ``lovverk`` and MCP only,
under åndsverkloven § 14; its publication on lovspor.no is guarded off. The
site enumerates documents from the root ``manifest.json``, so the guarantee
holds only while nothing in ``lovspor.publish`` reads the local manifest.

The corpus here is the one an operator produces: a regulation captured by the
observatory, approved and backfilled as three committed versions
(``backfilled_corpus``), beside one central forskrift. The whole site is
emitted from that commit, and every byte of every emitted file — pages, JSON
twins, sitemaps, the companion index, the redirect map, the Caddy snippet,
``site-manifest.json`` — is searched for every identifier and every line of
text the local dataset holds. ``scripts/quality/check_boundaries.py``
(``local-dataset-unpublished``) is the structural half of the same guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lovspor.publish.emit import emit_site
from lovspor.snapshot import CorpusSnapshot
from tests.unit.backfilled_corpus_fixtures import LOCAL, backfilled_corpus, dated_git
from tests.unit.promotion_cli_fixtures import PAGE_URL

CENTRAL_PATH = "forskrifter/eksempelforskrift.md"
CENTRAL_SLUG = "eksempelforskrift"
CENTRAL_BODY = """---
title: "Sentralforskriften om vimpler"
language: "nb"
ref_id: "forskrift/2020-01-14-63"
retrieved_at: "2026-04-29T11:20:30+00:00"
---

# Sentralforskriften om vimpler

### § 1. Formål

Vimpler heises bare på helligdager.
"""
SHORTEST_NEEDLE = 12
"""Shorter body lines (``§ 1 Formål``) are shared with any regulation, so
they would match the central page and prove nothing about the local one."""


def _site_from_mixed_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    corpus = backfilled_corpus(tmp_path, monkeypatch)
    (corpus / CENTRAL_PATH).write_text(CENTRAL_BODY, encoding="utf-8")
    date = "2026-10-01T12:00:00Z"
    dated_git(corpus, date, "add", "--", CENTRAL_PATH)
    dated_git(corpus, date, "commit", "-q", "-m", "chore: give the central forskrift a body")
    sha = dated_git(corpus, date, "rev-parse", "HEAD").strip()
    out = tmp_path / "site"
    emit_site(corpus, sha, out)
    return corpus, out


def _local_records(corpus: Path) -> dict[str, dict[str, object]]:
    snapshot = CorpusSnapshot(corpus, dated_git(corpus, "2026-10-01", "rev-parse", "HEAD").strip())
    text = snapshot.read_text(f"{LOCAL}/manifest.json")
    assert text is not None
    documents: dict[str, dict[str, object]] = json.loads(text)["documents"]
    return documents


def _body_lines(markdown: str) -> list[str]:
    _, _, body = markdown.partition("\n---\n")
    lines = (line.lstrip("#> ").strip() for line in body.splitlines())
    return [line for line in lines if len(line) >= SHORTEST_NEEDLE]


def _needles(corpus: Path) -> set[str]:
    """Every identifier and text line by which a local document could be recognised."""
    needles = {LOCAL, PAGE_URL}
    for doc_id, record in _local_records(corpus).items():
        needles.update({doc_id, str(record["markdown_path"]), str(record["content_hash"])})
        needles.update({str(record["slug"]), str(record["title"])})
        markdown = (corpus / str(record["markdown_path"])).read_text(encoding="utf-8")
        needles.update(_body_lines(markdown))
    return needles


def _emitted(out: Path) -> dict[str, str]:
    return {
        path.relative_to(out).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def mixed_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    corpus, out = _site_from_mixed_corpus(tmp_path, monkeypatch)
    return corpus, _emitted(out)


def test_the_corpus_really_holds_a_published_local_regulation(
    mixed_site: tuple[Path, dict[str, str]],
) -> None:
    corpus, _ = mixed_site
    records = _local_records(corpus)
    assert [record["status"] for record in records.values()] == ["current"]
    assert len(_needles(corpus)) > 8


def test_the_central_document_is_published(mixed_site: tuple[Path, dict[str, str]]) -> None:
    _, files = mixed_site
    assert f"forskrift/{CENTRAL_SLUG}/index.html" in files
    assert "Vimpler heises bare på helligdager." in files[f"forskrift/{CENTRAL_SLUG}/index.html"]
    assert json.loads(files["site-manifest.json"])["documents"] == 1


def test_no_emitted_path_names_the_local_dataset(mixed_site: tuple[Path, dict[str, str]]) -> None:
    corpus, files = mixed_site
    slugs = {str(record["slug"]) for record in _local_records(corpus).values()}
    leaked = [path for path in files if LOCAL in path or slugs & set(path.split("/"))]
    assert leaked == []


def test_no_emitted_file_carries_a_local_identifier_or_line(
    mixed_site: tuple[Path, dict[str, str]],
) -> None:
    corpus, files = mixed_site
    needles = _needles(corpus)
    leaked = sorted(
        (path, needle) for path, text in files.items() for needle in needles if needle in text
    )
    assert leaked == []
    assert {"sitemap.xml", "redirect-map.json", "redirects.caddy"} <= set(files)
