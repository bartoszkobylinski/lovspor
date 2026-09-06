"""Lineage redirect/410 maps (ADR-0013 Decision 6).

A 301 is an identity claim, so every rule here is exercised against a
real throwaway git history: document 301 only on proven continuity
through the rename walk, provision 301 only where the pid survives in
the successor at the pinned snapshot, 410 for everything retired that
proves nothing.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from lovspor.publish.inventory import PublishError, build_inventory
from lovspor.publish.redirects import (
    RedirectMap,
    build_redirect_map,
    caddy_snippet,
    redirect_map_json,
    validate_redirect_targets,
)
from lovspor.snapshot import CorpusSnapshot

_STAMP = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str, day: int = 1) -> None:
    date = {
        "GIT_AUTHOR_DATE": f"2026-02-{day:02d}T00:00:00Z",
        "GIT_COMMITTER_DATE": f"2026-02-{day:02d}T00:00:00Z",
    }
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **_STAMP, **date},
    )


def _doc(title: str, ref: str, pids: tuple[str, ...]) -> str:
    sections = "\n".join(f"### § {pid}. Tittel\n\nTekst.\n" for pid in pids)
    return (
        "---\n"
        f'title: "{title}"\n'
        'language: "nb"\n'
        f'ref_id: "{ref}"\n'
        'retrieved_at: "2026-01-03T00:00:00+00:00"\n'
        "---\n"
        "\n"
        f"# {title}\n"
        "\n"
        f"{sections}"
    )


def _record(doc_type: str, path: str, slug: str, status: str = "current") -> dict[str, object]:
    return {
        "doc_type": doc_type,
        "xml_hash": "a" * 64,
        "markdown_path": path,
        "source_dataset": "gjeldende-lover",
        "status": status,
        "slug": slug,
        "title": slug.capitalize(),
        "renderer_version": 8,
        "last_seen": "2026-01-01T00:00:00Z",
    }


def _write_manifest(repo: Path, documents: dict[str, dict[str, object]]) -> None:
    manifest = {"version": 1, "generated_at": "2026-01-01T00:00:00Z", "documents": documents}
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lovverk"
    (repo / "lover").mkdir(parents=True)
    (repo / "forskrifter").mkdir()
    _git(repo, "init", "-q")
    return repo


def _head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _map_at_head(repo: Path) -> RedirectMap:
    sha = _head(repo)
    snapshot = CorpusSnapshot(repo, sha)
    inventory = build_inventory(snapshot.manifest, snapshot.read_text)
    return build_redirect_map(repo, sha, inventory, snapshot.manifest)


@pytest.fixture
def renamed(tmp_path: Path) -> Path:
    """gammel.md (§1 §2) renamed to ny.md (§1 §3) in one later commit."""
    repo = _repo(tmp_path)
    (repo / "lover/gammel.md").write_text(_doc("G", "lov/2020-01-01-1", ("1", "2")))
    _write_manifest(repo, {"doc-1": _record("lov", "lover/gammel.md", "gammel")})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one", day=1)
    _git(repo, "mv", "lover/gammel.md", "lover/ny.md")
    (repo / "lover/ny.md").write_text(_doc("G", "lov/2020-01-01-1", ("1", "3")))
    _write_manifest(repo, {"doc-1": _record("lov", "lover/ny.md", "ny")})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two", day=2)
    return repo


class TestDocumentLineage:
    def test_renamed_document_gets_a_301_and_a_gone_namespace(self, renamed: Path) -> None:
        redirect_map = _map_at_head(renamed)
        assert ("/lov/gammel/", "/lov/ny/") in redirect_map.redirects
        assert "/lov/gammel/" in redirect_map.gone

    def test_only_surviving_pids_get_provision_301s(self, renamed: Path) -> None:
        redirect_map = _map_at_head(renamed)
        assert ("/lov/gammel/paragraf/1/", "/lov/ny/paragraf/1/") in redirect_map.redirects
        froms = [pair[0] for pair in redirect_map.redirects]
        assert "/lov/gammel/paragraf/2/" not in froms
        assert "/lov/gammel/paragraf/3/" not in froms

    def test_a_rename_chain_maps_every_old_slug_to_the_terminal(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1",)))
        _write_manifest(repo, {"doc-1": _record("lov", "lover/a.md", "a")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", "lover/a.md", "lover/b.md")
        _write_manifest(repo, {"doc-1": _record("lov", "lover/b.md", "b")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)
        _git(repo, "mv", "lover/b.md", "lover/c.md")
        _write_manifest(repo, {"doc-1": _record("lov", "lover/c.md", "c")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "three", day=3)
        redirect_map = _map_at_head(repo)
        assert ("/lov/a/", "/lov/c/") in redirect_map.redirects
        assert ("/lov/b/", "/lov/c/") in redirect_map.redirects
        assert {"/lov/a/", "/lov/b/"} <= set(redirect_map.gone)

    def test_a_deleted_document_is_gone_with_no_redirect(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/borte.md").write_text(_doc("B", "lov/2020-01-01-1", ("1",)))
        (repo / "lover/igjen.md").write_text(_doc("I", "lov/2020-01-01-2", ("1",)))
        _write_manifest(
            repo,
            {
                "doc-1": _record("lov", "lover/borte.md", "borte"),
                "doc-2": _record("lov", "lover/igjen.md", "igjen"),
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "rm", "-q", "lover/borte.md")
        _write_manifest(
            repo,
            {
                "doc-1": _record("lov", "lover/borte.md", "borte", status="removed"),
                "doc-2": _record("lov", "lover/igjen.md", "igjen"),
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)
        redirect_map = _map_at_head(repo)
        assert "/lov/borte/" in redirect_map.gone
        assert redirect_map.redirects == ()

    def test_a_removed_record_with_the_file_kept_is_gone(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/beholdt.md").write_text(_doc("B", "lov/2020-01-01-1", ("1",)))
        (repo / "lover/aktiv.md").write_text(_doc("A", "lov/2020-01-01-2", ("1",)))
        _write_manifest(
            repo,
            {
                "doc-1": _record("lov", "lover/beholdt.md", "beholdt", status="removed"),
                "doc-2": _record("lov", "lover/aktiv.md", "aktiv"),
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        redirect_map = _map_at_head(repo)
        assert "/lov/beholdt/" in redirect_map.gone
        assert redirect_map.redirects == ()

    def test_a_reused_slug_that_is_live_again_gets_no_entry(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/navn.md").write_text(_doc("N", "lov/2020-01-01-1", ("1",)))
        _write_manifest(repo, {"doc-1": _record("lov", "lover/navn.md", "navn")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", "lover/navn.md", "lover/annet.md")
        (repo / "lover/navn.md").write_text(_doc("N2", "lov/2020-01-01-2", ("1",)))
        _write_manifest(
            repo,
            {
                "doc-1": _record("lov", "lover/annet.md", "annet"),
                "doc-2": _record("lov", "lover/navn.md", "navn"),
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)
        redirect_map = _map_at_head(repo)
        assert "/lov/navn/" not in redirect_map.gone
        assert all(pair[0] != "/lov/navn/" for pair in redirect_map.redirects)

    def test_a_duplicate_pid_successor_gets_no_provision_301s(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/gammel.md").write_text(_doc("G", "lov/2020-01-01-1", ("1",)))
        _write_manifest(repo, {"doc-1": _record("lov", "lover/gammel.md", "gammel")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", "lover/gammel.md", "lover/ny.md")
        (repo / "lover/ny.md").write_text(_doc("G", "lov/2020-01-01-1", ("1", "1")))
        _write_manifest(repo, {"doc-1": _record("lov", "lover/ny.md", "ny")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)
        redirect_map = _map_at_head(repo)
        assert ("/lov/gammel/", "/lov/ny/") in redirect_map.redirects
        assert all("paragraf" not in pair[0] for pair in redirect_map.redirects)

    def test_a_cross_route_rename_redirects_across_prefixes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/regel.md").write_text(_doc("R", "lov/2020-01-01-1", ("1",)))
        _write_manifest(repo, {"doc-1": _record("lov", "lover/regel.md", "regel")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", "lover/regel.md", "forskrifter/regel.md")
        (repo / "forskrifter/regel.md").write_text(
            _doc("R", "forskrift/2020-01-01-1", ("1",)),
        )
        _write_manifest(repo, {"doc-1": _record("forskrift", "forskrifter/regel.md", "regel")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)
        redirect_map = _map_at_head(repo)
        assert ("/lov/regel/", "/forskrift/regel/") in redirect_map.redirects
        assert "/lov/regel/" in redirect_map.gone


class TestArtifacts:
    MAP = RedirectMap(
        redirects=(
            ("/lov/gammel/", "/lov/ny/"),
            ("/lov/gammel/paragraf/1/", "/lov/ny/paragraf/1/"),
        ),
        gone=("/forskrift/borte/", "/lov/gammel/"),
    )

    def test_json_artifact_is_byte_exact(self) -> None:
        assert redirect_map_json(self.MAP) == (
            json.dumps(
                {
                    "gone": ["/forskrift/borte/", "/lov/gammel/"],
                    "redirects": [
                        {"from": "/lov/gammel/", "to": "/lov/ny/"},
                        {"from": "/lov/gammel/paragraf/1/", "to": "/lov/ny/paragraf/1/"},
                    ],
                    "schema_version": "1",
                },
                sort_keys=True,
                ensure_ascii=False,
                indent=1,
            )
            + "\n"
        ).encode("utf-8")

    def test_caddy_snippet_is_byte_exact(self) -> None:
        assert caddy_snippet(self.MAP) == (
            b"# Generated by lovspor publish-site (ADR-0013 Decision 6). Do not edit.\n"
            b"# Relies on Caddy's default directive order: redir runs before respond,\n"
            b"# so an explicit 301 wins over its namespace's 410 catch-all.\n"
            b"redir /lov/gammel/ /lov/ny/ 301\n"
            b"redir /lov/gammel/paragraf/1/ /lov/ny/paragraf/1/ 301\n"
            b"@lovspor_gone path /forskrift/borte/ /forskrift/borte/* "
            b"/lov/gammel/ /lov/gammel/*\n"
            b"respond @lovspor_gone 410\n"
        )

    def test_an_empty_map_emits_only_the_header(self) -> None:
        snippet = caddy_snippet(RedirectMap(redirects=(), gone=()))
        assert not any(
            line.startswith((b"redir ", b"respond ", b"@")) for line in snippet.splitlines()
        )
        assert snippet.startswith(b"# Generated by lovspor publish-site")


class TestValidation:
    def test_a_dangling_redirect_target_is_a_build_failure(self, renamed: Path) -> None:
        sha = _head(renamed)
        snapshot = CorpusSnapshot(renamed, sha)
        inventory = build_inventory(snapshot.manifest, snapshot.read_text)
        dangling = RedirectMap(
            redirects=(("/lov/gammel/", "/lov/finnes-ikke/"),),
            gone=("/lov/gammel/",),
        )
        with pytest.raises(PublishError, match="dangling redirect target"):
            validate_redirect_targets(dangling, inventory)

    def test_the_built_map_passes_its_own_target_validation(self, renamed: Path) -> None:
        sha = _head(renamed)
        snapshot = CorpusSnapshot(renamed, sha)
        inventory = build_inventory(snapshot.manifest, snapshot.read_text)
        validate_redirect_targets(
            build_redirect_map(renamed, sha, inventory, snapshot.manifest), inventory
        )

    def test_a_dangling_provision_target_is_caught_too(self, renamed: Path) -> None:
        sha = _head(renamed)
        snapshot = CorpusSnapshot(renamed, sha)
        inventory = build_inventory(snapshot.manifest, snapshot.read_text)
        dangling = RedirectMap(
            redirects=(("/lov/gammel/paragraf/2/", "/lov/ny/paragraf/2/"),),
            gone=(),
        )
        with pytest.raises(PublishError, match="dangling redirect target"):
            validate_redirect_targets(dangling, inventory)
