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


# Authored by the CI test author on PR #257 and adopted verbatim: the checks
# read redirect-map.json, but Caddy imports redirects.caddy. Validating one
# while serving the other is validating the wrong artifact.


def test_the_caddy_redirect_map_is_required(release: Path) -> None:
    """Caddy serves the generated snippet rather than redirect-map.json, so a
    release without that snippet has silently lost all of its redirects."""
    (release / "redirects.caddy").unlink()

    with pytest.raises(PublishError, match="redirects.caddy .* missing"):
        check_release(release)


def test_the_caddy_redirect_map_cannot_disagree_with_the_json_map(release: Path) -> None:
    """The JSON map is not the runtime artifact.  Checking it must not bless a
    different Caddy map, or pages from one build can be served with redirects
    from another build after the atomic switch."""
    snippet = release / "redirects.caddy"
    snippet.write_text(
        snippet.read_text(encoding="utf-8").replace("/lov/testloven/ 301", "/lov/finnes-ikke/ 301"),
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="redirects.caddy"):
        check_release(release)


def test_a_gone_prefix_dropped_from_the_snippet_is_caught(release: Path) -> None:
    """The 410 half of the map has no per-line target to check, so it is
    compared as a set of prefixes; losing one would resurrect a retired
    namespace as a 404 instead of the documented 410."""
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gone"].append("/lov/utgaatt/")
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="gone prefixes"):
        check_release(release)


# Authored by the CI test author on PR #257 (third round) and adopted verbatim:
# three inputs the checker handled with a traceback instead of a refusal.


def test_a_boolean_manifest_document_count_is_refused(release: Path) -> None:
    """JSON booleans are Python integers, but cannot describe a release size."""
    manifest = release / "site-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["documents"] = True
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="has no document count: True"):
        check_release(release)


def test_a_sitemap_with_malformed_utf8_fails_as_a_publish_error(release: Path) -> None:
    (release / "sitemap.xml").write_bytes(b"\xff")

    with pytest.raises(PublishError, match="sitemap.xml.*unreadable"):
        check_release(release)


def test_a_redirect_map_that_is_not_an_object_is_refused(release: Path) -> None:
    (release / "redirect-map.json").write_text("[]", encoding="utf-8")

    with pytest.raises(PublishError, match="redirect-map.json is not a JSON object"):
        check_release(release)


def test_every_artifact_read_reports_corruption_as_a_refusal(release: Path) -> None:
    """The contract is one named refusal for any broken input. Each artifact the
    checker opens is corrupted in turn; none may escape as a raw exception."""
    for name in (
        "site-manifest.json",
        "redirect-map.json",
        "redirects.caddy",
        "lov/testloven/index.json",
        "sitemaps/" + next((release / "sitemaps").iterdir()).name,
    ):
        broken = release / name
        original = broken.read_bytes()
        broken.write_bytes(b"\xff\xfe not utf-8")
        with pytest.raises(PublishError, match="unreadable"):
            check_release(release)
        broken.write_bytes(original)
    check_release(release)  # and the tree is whole again


# Authored by the CI test author on PR #257 (fourth round) and adopted verbatim.
# Closed structurally this time: the redirect map's shape is validated once, by
# one parser, and both consumers take typed values from it.


def test_a_redirect_entry_without_a_source_is_refused_not_crashed(release: Path) -> None:
    """The target-only validation must not let malformed entries reach the
    Caddy-map comparison as a raw ``KeyError``."""
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["redirects"][0]["from"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="redirect-map.json"):
        check_release(release)


def test_a_non_string_gone_prefix_is_refused_not_crashed(release: Path) -> None:
    """Every redirect-map field is untrusted release data; an unhashable
    prefix must produce the same named refusal as malformed redirect entries."""
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gone"].append({"path": "/lov/utgaatt/"})
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="redirect-map.json"):
        check_release(release)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["redirects"].append("not-an-object"),
        lambda d: d["redirects"].append({"from": 1, "to": "/lov/testloven/"}),
        lambda d: d["redirects"].append({"from": "/x/", "to": ["/lov/testloven/"]}),
        lambda d: d.__setitem__("redirects", {"from": "/x/", "to": "/y/"}),
        lambda d: d.__setitem__("gone", "/lov/"),
    ],
)
def test_every_malformed_redirect_map_shape_is_one_named_refusal(
    release: Path, mutate: object
) -> None:
    path = release / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)  # type: ignore[operator]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PublishError, match="redirect-map.json"):
        check_release(release)


# Authored by the CI test author on PR #257 (fifth round) and adopted verbatim:
# the sitemap check ran in one direction and matched by regex.


def test_a_sitemap_omitting_an_emitted_page_is_refused(release: Path) -> None:
    """The release gate must check sitemap/page equality in both directions.

    Merely checking that listed URLs exist accepts a partially copied sitemap
    whose remaining URLs all happen to name valid pages.
    """
    sitemap = release / "sitemaps" / "lover-1.xml"
    text = sitemap.read_text(encoding="utf-8")
    start = text.index("<url>")
    end = text.index("</url>", start) + len("</url>")
    sitemap.write_text(text[:start] + text[end:], encoding="utf-8")

    with pytest.raises(PublishError, match="sitemap"):
        check_release(release)


def test_a_truncated_sitemap_is_refused_even_if_its_urls_survive(release: Path) -> None:
    """A partial copy can end after a complete ``loc`` and still match the
    checker's URL regex; malformed XML must not pass the pre-serve gate."""
    sitemap = release / "sitemaps" / "lover-1.xml"
    text = sitemap.read_text(encoding="utf-8")
    sitemap.write_text(text.removesuffix("</urlset>\n"), encoding="utf-8")

    with pytest.raises(PublishError, match="sitemap"):
        check_release(release)


def test_the_browse_indexes_count_as_pages_the_sitemap_must_list(release: Path) -> None:
    """`/lov/` and `/forskrift/` are pages too; dropping them from indexes.xml
    would leave the entry points unadvertised while every document passed."""
    indexes = release / "sitemaps" / "indexes.xml"
    indexes.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n',
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="appear in no sitemap"):
        check_release(release)


# Authored by the CI test author on PR #257 (sixth round) and adopted verbatim.
# Caddy `import`s the snippet, so a directive in it runs with Caddy's authority;
# the check now admits exactly the three shapes the generator writes.


def test_the_caddy_map_cannot_contain_unmodelled_runtime_directives(release: Path) -> None:
    """The served map must not do more than its validated JSON representation.

    An extra valid directive is accepted by ``caddy validate`` but can change
    live routing, so ignoring it would validate a different map from the one
    Caddy actually imports.
    """
    snippet = release / "redirects.caddy"
    snippet.write_text(
        snippet.read_text(encoding="utf-8") + "respond /lov/testloven/* 503\n",
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="redirects.caddy"):
        check_release(release)


def test_comments_and_blank_lines_in_the_caddy_map_are_not_directives(release: Path) -> None:
    snippet = release / "redirects.caddy"
    snippet.write_text(
        "\n# a comment\n\n" + snippet.read_text(encoding="utf-8") + "\n   \n",
        encoding="utf-8",
    )

    check_release(release)
