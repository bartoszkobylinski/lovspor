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

from lovspor.publish import redirects as publish_redirects
from lovspor.publish.inventory import DocumentPlan, PublishError, build_inventory
from lovspor.publish.redirects import (
    RedirectMap,
    _Edge,
    _parse_edges,
    _terminal_plan,
    build_redirect_map,
    caddy_snippet,
    redirect_map_json,
    validate_redirect_targets,
)
from lovspor.snapshot import CorpusSnapshot
from lovspor.storage.manifest import Manifest

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

    @pytest.mark.parametrize("unsafe_slug", ("ikke trygg", 'ikke"trygg', "ikke{trygg}", "ikke*"))
    def test_an_unservable_historical_slug_emits_no_caddy_rule(
        self,
        tmp_path: Path,
        unsafe_slug: str,
    ) -> None:
        repo = _repo(tmp_path)
        old_path = f"lover/{unsafe_slug}.md"
        (repo / old_path).write_text(_doc("G", "lov/2020-01-01-1", ("1",)))
        _write_manifest(repo, {"doc-1": _record("lov", old_path, unsafe_slug)})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", old_path, "lover/trygg.md")
        _write_manifest(repo, {"doc-1": _record("lov", "lover/trygg.md", "trygg")})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)

        redirect_map = _map_at_head(repo)

        assert redirect_map.redirects == ()
        assert redirect_map.gone == ()
        assert unsafe_slug.encode() not in caddy_snippet(redirect_map)


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


class TestRedirectInternals:
    def test_lineage_git_walk_requests_rename_detection_full_shas_and_checked_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="")

        monkeypatch.setattr(publish_redirects.subprocess, "run", fake_run)
        assert publish_redirects._lineage_edges(tmp_path, "abc") == []
        command, kwargs = calls[0]
        assert command[0:2] == ["git", "-c"]
        assert command[2].lower() == "core.quotepath=false"
        assert "-M" in command
        assert "--first-parent" in command
        assert "-m" not in command
        assert "--format=%x00%H" in command
        assert kwargs["check"] is True

    def test_edge_parser_accepts_only_well_formed_rename_and_delete_records(self) -> None:
        parsed = publish_redirects._parse_edges(
            "\x00fullsha\n"
            "R100\tlover/a.md\tlover/b.md\n"
            "D\tlover/c.md\n"
            "X\tlover/ignored.md\n"
            "R100\tmissing-new.md\n"
            "D\ttoo\tmany.md\n"
        )
        assert [(edge.sha, edge.old, edge.new) for edge in parsed] == [
            ("fullsha", "lover/a.md", "lover/b.md"),
            ("fullsha", "lover/c.md", None),
        ]

    def test_newest_retirement_skips_unusable_and_live_edges_without_stopping(self) -> None:
        edge = publish_redirects._Edge(index=2, sha="s", old="lover/gone.md", new=None)
        retired = publish_redirects._newest_retirement_per_key(
            [
                publish_redirects._Edge(index=0, sha="s", old="not-a-route", new=None),
                publish_redirects._Edge(index=1, sha="s", old="lover/live.md", new=None),
                edge,
            ],
            {("lov", "live")},
        )
        assert retired == {("lov", "gone"): edge}

    def test_terminal_walk_chooses_the_nearest_later_event(self) -> None:
        start = publish_redirects._Edge(index=5, sha="s", old="lover/a.md", new="lover/b.md")
        older = publish_redirects._Edge(index=3, sha="s", old="lover/b.md", new="lover/c.md")
        nearer = publish_redirects._Edge(index=4, sha="s", old="lover/b.md", new="lover/d.md")
        plan = build_inventory(
            Manifest.model_validate(
                {
                    "generated_at": "2026-01-01T00:00:00Z",
                    "documents": {"doc": _record("lov", "lover/d.md", "d")},
                }
            ),
            lambda _path: _doc("D", "lov/2020-01-01-1", ("1",)),
        ).documents[0]
        assert (
            publish_redirects._terminal_plan(
                start, {"lover/b.md": [older, nearer]}, {"lover/d.md": plan}
            )
            == plan
        )

    def test_retired_pid_lookup_treats_an_unreadable_blob_as_no_evidence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 128, stdout="")

        monkeypatch.setattr(publish_redirects.subprocess, "run", fake_run)
        edge = publish_redirects._Edge(index=0, sha="abc", old="lover/gone.md", new=None)
        assert publish_redirects._retired_pids(tmp_path, edge) == set()
        command, kwargs = calls[0]
        assert command[0:2] == ["git", "-c"]
        assert command[2].lower() == "core.quotepath=false"
        assert command[3:] == ["show", "abc^:lover/gone.md"]
        assert kwargs["check"] is False

    def test_removed_manifest_scan_continues_past_current_unknown_and_live_records(self) -> None:
        manifest = Manifest.model_validate(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "documents": {
                    "current": _record("lov", "lover/current.md", "current"),
                    "unknown": _record("other", "other/x.md", "x", status="removed"),
                    "live": _record("lov", "lover/live.md", "live", status="removed"),
                    "fallback": _record("lov", "lover/fallback.md", "", status="removed"),
                    "gone": _record("lov", "lover/path.md", "explicit", status="removed"),
                },
            }
        )
        assert publish_redirects._removed_record_prefixes(manifest, {("lov", "live")}) == {
            "/lov/fallback/",
            "/lov/explicit/",
        }

    @pytest.mark.parametrize(
        "path",
        ("unknown/name.md", "lover/nested/name.md", "lover/name.txt", "lover/.md"),
    )
    def test_route_slug_rejects_noncanonical_paths(self, path: str) -> None:
        assert publish_redirects._route_slug(path) is None

    def test_emitted_urls_continue_after_a_duplicate_pid_document(self) -> None:
        duplicate = build_inventory(
            Manifest.model_validate(
                {
                    "generated_at": "2026-01-01T00:00:00Z",
                    "documents": {"dup": _record("lov", "lover/dup.md", "dup")},
                }
            ),
            lambda _path: _doc("D", "lov/2020-01-01-1", ("1", "1")),
        ).documents[0]
        ordinary = build_inventory(
            Manifest.model_validate(
                {
                    "generated_at": "2026-01-01T00:00:00Z",
                    "documents": {"ok": _record("lov", "lover/ok.md", "ok")},
                }
            ),
            lambda _path: _doc("O", "lov/2020-01-01-2", ("2",)),
        ).documents[0]
        urls = publish_redirects._emitted_urls(
            publish_redirects.PublishInventory(documents=(duplicate, ordinary))
        )
        assert "/lov/ok/paragraf/2/" in urls

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


class TestParserContracts:
    def test_an_orphan_rename_line_before_any_header_keeps_an_empty_sha(self) -> None:
        """git log always emits the commit header first; a headerless
        name-status line must parse fail-open with an empty sha, never
        crash and never inherit an invented commit."""
        edges = _parse_edges("R100\tlover/a.md\tlover/b.md\n\x00abc\nD\tlover/c.md")
        assert [(e.sha, e.old, e.new) for e in edges] == [
            ("", "lover/a.md", "lover/b.md"),
            ("abc", "lover/c.md", None),
        ]

    def test_an_event_at_the_same_index_is_never_its_own_successor(self) -> None:
        """The walk follows strictly later events only: an event at the
        same position must not be treated as the file's next hop."""
        plan = DocumentPlan.model_validate(
            {
                "doc_id": "doc-1",
                "slug": "a",
                "route": "lov",
                "title": "A",
                "markdown_path": "lover/a.md",
                "source_dataset": "gjeldende-lover",
                "xml_hash": "a" * 64,
                "renderer_version": 8,
                "language": "nb",
                "ref_id": "lov/2020-01-01-1",
                "retrieved_at": "2026-01-01T00:00:00+00:00",
                "date_in_force": None,
                "last_change_in_force": None,
                "provisions": (),
                "duplicate_pids": {},
            },
        )
        start = _Edge(index=1, sha="s1", old="lover/x.md", new="lover/a.md")
        same_index = _Edge(index=1, sha="s1", old="lover/a.md", new=None)
        result = _terminal_plan(start, {"lover/a.md": [same_index]}, {"lover/a.md": plan})
        assert result is plan
