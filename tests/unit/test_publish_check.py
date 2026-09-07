"""Unit tests for the pre-serve release check (ADR-0013 Decision 8, #241).

Every test builds a real release with ``emit_site`` from a throwaway git corpus
and then breaks exactly one thing, because the check's job is to name the one
thing that is wrong in a tree of ninety thousand files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.publish.check import check_release
from lovspor.publish.emit import emit_site
from lovspor.publish.inventory import PublishError

DOC = """---
title: "Testloven"
language: "nb"
ref_id: "lov/2020-01-01-1"
retrieved_at: "2026-01-01T00:00:00+00:00"
---

# Testloven

### § 1. Formål

Se [forskriften § 2](forskrift/2021-02-02-2/§2).

### § 2. Virkeområde

Tekst to.
"""

REGULATION = """---
title: "Testforskriften"
language: "nn"
ref_id: "forskrift/2021-02-02-2"
retrieved_at: "2026-01-02T00:00:00+00:00"
---

# Testforskriften

### § 2. Krav

Krav her.
"""


def _git(repo: Path, *args: str) -> str:
    stamp = {
        "GIT_AUTHOR_DATE": "2026-02-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **stamp},
    ).stdout.strip()


def _record(doc_type: str, path: str, slug: str, title: str) -> dict[str, object]:
    return {
        "doc_type": doc_type,
        "xml_hash": "a" * 64,
        "markdown_path": path,
        "source_dataset": "gjeldende-lover",
        "status": "current",
        "slug": slug,
        "title": title,
        "renderer_version": 8,
        "last_seen": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """A complete, valid release tree built from a two-document corpus, after a
    rename so the redirect map is non-empty."""
    repo = tmp_path / "lovverk"
    (repo / "lover").mkdir(parents=True)
    (repo / "forskrifter").mkdir()
    _git(repo, "init", "-q")
    (repo / "lover/gammelloven.md").write_text(DOC, encoding="utf-8")
    manifest = {
        "version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "documents": {"doc-1": _record("lov", "lover/gammelloven.md", "gammelloven", "Old")},
    }
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    # rename → a 301 from the old slug; add the regulation
    _git(repo, "mv", "lover/gammelloven.md", "lover/testloven.md")
    (repo / "forskrifter/testforskriften.md").write_text(REGULATION, encoding="utf-8")
    manifest["documents"] = {
        "doc-1": _record("lov", "lover/testloven.md", "testloven", "Testloven"),
        "doc-2": _record(
            "forskrift", "forskrifter/testforskriften.md", "testforskriften", "Testforskriften"
        ),
    }
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two")
    out = tmp_path / "release"
    emit_site(repo, _git(repo, "rev-parse", "HEAD"), out)
    return out


def test_a_freshly_emitted_release_passes(release: Path) -> None:
    report = check_release(release)

    assert report.documents == 2
    assert report.pages > 2  # provision pages count too
    assert report.sitemap_urls >= report.pages
    assert report.redirects >= 1
    assert "release ok" in report.summary()


def test_a_directory_that_is_not_a_release_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PublishError, match="site-manifest.json .* missing"):
        check_release(tmp_path)


def test_a_manifest_from_another_schema_is_refused(release: Path) -> None:
    """An older or newer generator's tree must not be served by this engine's
    Caddy config: the URL grammar and the companion contract are versioned."""
    manifest = release / "site-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["site_schema_version"] = "0"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="site_schema_version '0'"):
        check_release(release)


def test_a_manifest_without_a_full_commit_is_refused(release: Path) -> None:
    manifest = release / "site-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["corpus_commit"] = "abc123"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="names no full corpus commit"):
        check_release(release)


def test_a_missing_document_page_is_caught_by_the_count(release: Path) -> None:
    """The manifest's document count is the closure proof; a tree with fewer
    pages than it promises is a partial copy."""
    page = release / "forskrift" / "testforskriften" / "index.html"
    page.unlink()
    page.with_name("index.json").unlink()

    with pytest.raises(PublishError, match="promises 2 documents, the tree has 1"):
        check_release(release)


def test_a_page_whose_twin_hashes_differently_is_refused(release: Path) -> None:
    """A twin from one build beside HTML from another is the mixed snapshot the
    atomic switch exists to prevent; the hash is what tells them apart."""
    page = release / "lov" / "testloven" / "index.html"
    page.write_bytes(page.read_bytes() + b"<!-- tampered -->")

    with pytest.raises(PublishError, match="lov/testloven/index.html: twin records"):
        check_release(release)


def test_a_page_without_a_twin_is_refused(release: Path) -> None:
    (release / "lov" / "testloven" / "index.json").unlink()

    with pytest.raises(PublishError, match="has no index.json twin"):
        check_release(release)


def test_a_sitemap_naming_an_absent_page_is_refused(release: Path) -> None:
    """The sitemap is what crawlers read first; a URL it advertises that the
    tree cannot serve is a mixed snapshot in its most public form."""
    provision = next((release / "lov" / "testloven" / "paragraf").iterdir())
    for file in provision.iterdir():
        file.unlink()
    provision.rmdir()

    with pytest.raises(PublishError, match="lists https://lovspor.no/lov/testloven/paragraf/"):
        check_release(release)


def test_a_redirect_to_an_absent_page_is_refused(release: Path) -> None:
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["redirects"].append({"from": "/lov/borte/", "to": "/lov/finnes-ikke/"})
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="redirect target /lov/finnes-ikke/ is not in the tree"):
        check_release(release)


def test_the_cli_reports_and_exits_nonzero_on_refusal(release: Path, tmp_path: Path) -> None:
    runner = CliRunner()

    ok = runner.invoke(app, ["publish-check", str(release)])
    bad = runner.invoke(app, ["publish-check", str(tmp_path / "nothing")])

    assert ok.exit_code == 0, ok.output
    assert "release ok" in ok.output
    assert bad.exit_code == 1
    assert "release refused" in bad.output


# Authored by the CI test author on PR #257 and adopted verbatim: the resolver
# joined the sitemap/redirect path onto the root without confining the result,
# so a release could point at — and the check be satisfied by — a file beside
# the tree rather than inside it.


def test_a_sitemap_path_cannot_escape_the_release_tree(release: Path) -> None:
    outside = release.parent / "outside.xml"
    outside.write_text("<urlset />", encoding="utf-8")
    (release / "sitemap.xml").write_text(
        "<sitemapindex><sitemap><loc>https://lovspor.no/../outside.xml</loc></sitemap>"
        "</sitemapindex>",
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="which is not in the tree"):
        check_release(release)


def test_a_redirect_target_cannot_escape_the_release_tree(release: Path) -> None:
    outside = release.parent / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("not part of the release", encoding="utf-8")
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["redirects"].append({"from": "/lov/borte/", "to": "/../outside/"})
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="is not in the tree"):
        check_release(release)


def test_a_page_url_inside_a_sitemap_cannot_escape_either(release: Path) -> None:
    """The per-page URLs are the larger surface: ninety thousand of them, all
    read straight from XML the build wrote."""
    outside = release.parent / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("x", encoding="utf-8")
    sitemap = next((release / "sitemaps").iterdir())
    sitemap.write_text(
        "<urlset><url><loc>https://lovspor.no/../outside/</loc></url></urlset>",
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="which is not in the tree"):
        check_release(release)
