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

from lovspor.publish.emit import emit_site

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
        assert manifest["corpus_commit_time"] == "2026-02-01T00:00:00Z"
        assert manifest["site_schema_version"] == "1"
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

        first_files = sorted(p.relative_to(first) for p in first.rglob("index.*"))
        second_files = sorted(p.relative_to(second) for p in second.rglob("index.*"))
        assert first_files == second_files
        for rel in first_files:
            assert (first / rel).read_bytes() == (second / rel).read_bytes()
