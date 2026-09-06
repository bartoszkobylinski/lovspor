"""The emitter: one pinned corpus commit in, one site tree out (ADR-0013).

Exercised against a real throwaway git corpus, because source_revision
and snapshot closure are git facts. The contracts under test: every
artifact set comes from the single pinned commit, source_revision is the
document's own last-touching commit (not HEAD), representation_hash in
each companion matches the emitted HTML bytes, duplicate-pid documents
publish no provision pages and land in the manifest's exclusions, and
two builds of the same snapshot are byte-identical.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from lovspor.publish.emit import _committer_time, _deep_url, _resolver, _source_revisions, emit_site
from lovspor.publish.inventory import PublishError, PublishInventory, build_inventory
from lovspor.snapshot import CorpusSnapshot

DOC = """---
title: "Testloven"
language: "nb"
ref_id: "lov/2020-01-01-1"
retrieved_at: "2026-01-01T00:00:00+00:00"
---

# Testloven

## Kapittel 1. Innledning

### § 1. Formål

Se [forskriften § 2](forskrift/2021-02-02-2/§2) og [direktivet](eu/32006L0123).

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

DUP = """---
title: "Dobbeltloven"
language: "nb"
ref_id: "lov/2022-03-03-3"
retrieved_at: "2026-01-03T00:00:00+00:00"
---

# Dobbeltloven

### § 1. En

A.

### § 1. To

B.
"""


def _run_git(repo: Path, *args: str) -> None:
    stamp = {
        "GIT_AUTHOR_DATE": "2026-02-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **stamp},
    )


def _record(doc_type: str, path: str, slug: str, ref: str) -> dict[str, object]:
    return {
        "doc_type": doc_type,
        "xml_hash": "a" * 64,
        "markdown_path": path,
        "source_dataset": "gjeldende-lover",
        "status": "current",
        "slug": slug,
        "title": ref,
        "renderer_version": 8,
        "last_seen": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "lovverk"
    (repo / "lover").mkdir(parents=True)
    (repo / "forskrifter").mkdir()
    _run_git(repo, "init", "-q")
    (repo / "lover/testloven.md").write_text(DOC, encoding="utf-8")
    manifest = {
        "version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "documents": {
            "doc-1": _record("lov", "lover/testloven.md", "testloven", "Testloven"),
        },
    }
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "one")
    (repo / "forskrifter/testforskriften.md").write_text(REGULATION, encoding="utf-8")
    nn_doc = DOC.replace('"Testloven"', '"Vimpelføringloven"').replace(
        "lov/2020-01-01-1",
        "lov/1933-04-04-4",
    )
    (repo / "lover/vimpel-føring.md").write_text(nn_doc, encoding="utf-8")
    (repo / "lover/dobbeltloven.md").write_text(DUP, encoding="utf-8")
    manifest["documents"] = {
        "doc-1": _record("lov", "lover/testloven.md", "testloven", "Testloven"),
        "doc-2": _record(
            "forskrift",
            "forskrifter/testforskriften.md",
            "testforskriften",
            "Testforskriften",
        ),
        "doc-3": _record("lov", "lover/dobbeltloven.md", "dobbeltloven", "Dobbelt"),
        "doc-4": _record("lov", "lover/vimpel-føring.md", "vimpel-føring", "Vimpel"),
    }
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "two")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, sha


class TestEmitSite:
    def test_emits_document_and_provision_pages_with_twins(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        assert (out / "lov/testloven/index.html").is_file()
        assert (out / "lov/testloven/index.json").is_file()
        assert (out / "lov/testloven/paragraf/1/index.html").is_file()
        assert (out / "forskrift/testforskriften/paragraf/2/index.json").is_file()

    def test_non_ascii_markdown_path_gets_its_source_revision(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        # git octal-escapes non-ASCII paths in --name-only output unless
        # core.quotePath=false; a quoted path never matches the manifest
        # and the whole build dies on the lookup.
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        doc = json.loads((out / "lov/vimpel-føring/index.json").read_text())
        assert doc["provenance"]["source_revision"] == sha

    def test_duplicate_pid_document_publishes_no_provision_pages(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        assert (out / "lov/dobbeltloven/index.html").is_file()
        assert not (out / "lov/dobbeltloven/paragraf").exists()
        manifest = json.loads((out / "site-manifest.json").read_text())
        assert manifest["exclusions"] == [
            {"doc_id": "doc-3", "slug": "dobbeltloven", "duplicate_pids": {"1": 2}},
        ]

    def test_source_revision_is_the_documents_own_commit(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        doc = json.loads((out / "lov/testloven/index.json").read_text())
        reg = json.loads((out / "forskrift/testforskriften/index.json").read_text())
        assert reg["provenance"]["source_revision"] == sha
        assert doc["provenance"]["source_revision"] != sha
        assert "corpus_commit" not in doc["provenance"]

    def test_site_manifest_carries_the_closure_proof(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        manifest = json.loads((out / "site-manifest.json").read_text())
        assert manifest["corpus_commit"] == sha
        assert manifest["corpus_commit_time"] == "2026-02-01T00:00:00+00:00"
        assert manifest["site_schema_version"] == "1"
        assert manifest["engine_version"]
        assert manifest["documents"] == 4
        assert set(manifest) == {
            "corpus_commit",
            "corpus_commit_time",
            "engine_version",
            "site_schema_version",
            "documents",
            "exclusions",
        }
        assert "build_timestamp" not in manifest

    def test_representation_hash_matches_emitted_html_bytes(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        for json_path in sorted(out.rglob("index.json")):
            html_path = json_path.with_name("index.html")
            envelope = json.loads(json_path.read_text())
            digest = hashlib.sha256(html_path.read_bytes()).hexdigest()
            assert envelope["provenance"]["representation_hash"] == digest

    def test_cross_reference_resolves_to_the_target_provision(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)

        html = (out / "lov/testloven/index.html").read_text()
        assert '<a href="/forskrift/testforskriften/paragraf/2/">' in html
        assert "direktivet" in html
        assert 'href="eu/' not in html

    def test_resolver_uses_document_for_base_and_non_section_deep_links(
        self, corpus: tuple[Path, str]
    ) -> None:
        repo, sha = corpus
        snapshot = CorpusSnapshot(repo, sha)
        plan = build_inventory(snapshot.manifest, snapshot.read_text).documents[0]
        resolve = _resolver(PublishInventory(documents=(plan,)))
        assert resolve(plan.ref_id) == "/lov/testloven/"
        assert _deep_url(plan, "x1") == "/lov/testloven/"
        assert _deep_url(plan, "§999") == "/lov/testloven/"

    def test_rebuild_drops_artifacts_of_retired_documents(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        stale = out / "lov" / "opphevet-lov" / "index.html"
        stale.parent.mkdir(parents=True)
        stale.write_text("gammel", encoding="utf-8")
        stale_regulation = out / "forskrift" / "opphevet" / "index.html"
        stale_regulation.parent.mkdir(parents=True)
        stale_regulation.write_text("gammel", encoding="utf-8")
        stale_manifest = out / "site-manifest.json"
        stale_manifest.write_text("gammel", encoding="utf-8")
        emit_site(repo, sha, out)

        assert not stale.exists()
        assert not stale_regulation.exists()
        assert stale_manifest.read_text(encoding="utf-8") != "gammel"
        assert (out / "lov/testloven/index.html").is_file()

    def test_companions_preserve_exact_document_and_section_text(
        self, corpus: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        emit_site(repo, sha, out)
        document = json.loads((out / "lov/testloven/index.json").read_text())
        section = json.loads((out / "lov/testloven/paragraf/1/index.json").read_text())
        assert "### § 1. Formål\n\nSe [forskriften" in document["text"]
        assert section["text"].startswith("### § 1. Formål\n\nSe [forskriften")
        assert "XX\nXX" not in document["text"] + section["text"]
        assert document["provenance"]["renderer_version"] == 8

    def test_invalid_git_revision_fails_loudly(self, corpus: tuple[Path, str]) -> None:
        repo, _ = corpus
        with pytest.raises(subprocess.CalledProcessError):
            _source_revisions(repo, "not-a-revision")
        with pytest.raises(subprocess.CalledProcessError):
            _committer_time(repo, "not-a-revision")

    def test_rebuild_preserves_assets_outside_generated_namespaces(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        asset = out / "assets" / "site.css"
        asset.parent.mkdir(parents=True)
        asset.write_text("body { color: black; }", encoding="utf-8")

        emit_site(repo, sha, out)

        assert asset.read_text(encoding="utf-8") == "body { color: black; }"

    def test_invalid_snapshot_does_not_erase_the_last_good_build(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, good_sha = corpus
        out = tmp_path / "site"
        emit_site(repo, good_sha, out)
        before = {
            path.relative_to(out): path.read_bytes() for path in out.rglob("*") if path.is_file()
        }

        manifest = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))
        manifest["documents"]["doc-1"]["slug"] = "../outside"
        (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        _run_git(repo, "add", "manifest.json")
        _run_git(repo, "commit", "-q", "-m", "invalid snapshot")
        bad_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        with pytest.raises(PublishError, match="canonical URL segment"):
            emit_site(repo, bad_sha, out)

        after = {
            path.relative_to(out): path.read_bytes() for path in out.rglob("*") if path.is_file()
        }
        assert after == before

    def test_two_builds_are_byte_identical(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        first = tmp_path / "one"
        second = tmp_path / "two"
        emit_site(repo, sha, first)
        emit_site(repo, sha, second)

        first_files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
        second_files = sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
        assert first_files == second_files
        for rel in first_files:
            assert (first / rel).read_bytes() == (second / rel).read_bytes()

    def test_pinned_build_ignores_working_tree_changes(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        clean = tmp_path / "clean"
        dirty = tmp_path / "dirty"
        emit_site(repo, sha, clean)

        (repo / "lover/testloven.md").write_text(
            DOC.replace("Tekst to.", "UREGISTRERT ENDRING"),
            encoding="utf-8",
        )
        emit_site(repo, sha, dirty)

        clean_files = {
            path.relative_to(clean): path.read_bytes()
            for path in clean.rglob("*")
            if path.is_file()
        }
        dirty_files = {
            path.relative_to(dirty): path.read_bytes()
            for path in dirty.rglob("*")
            if path.is_file()
        }
        assert dirty_files == clean_files
        assert b"UREGISTRERT ENDRING" not in b"".join(dirty_files.values())

    def test_pinned_build_ignores_later_committed_changes(
        self,
        corpus: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo, pinned_sha = corpus
        (repo / "lover/testloven.md").write_text(
            DOC.replace("Tekst to.", "SENERE COMMIT"),
            encoding="utf-8",
        )
        _run_git(repo, "add", "lover/testloven.md")
        _run_git(repo, "commit", "-q", "-m", "later corpus state")
        later_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        out = tmp_path / "site"

        emit_site(repo, pinned_sha, out)

        html = (out / "lov/testloven/index.html").read_text(encoding="utf-8")
        companion = json.loads(
            (out / "lov/testloven/index.json").read_text(encoding="utf-8"),
        )
        manifest = json.loads((out / "site-manifest.json").read_text(encoding="utf-8"))
        assert "Tekst to." in html
        assert "SENERE COMMIT" not in html
        assert companion["provenance"]["source_revision"] != later_sha
        assert manifest["corpus_commit"] == pinned_sha
