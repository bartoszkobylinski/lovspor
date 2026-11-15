"""Document identity in the redirect map (#939).

Two gaps between the rename walk and document identity, each exercised
against a real throwaway git history:

- a document moved onto a path another document held, in one commit: git
  records a modification and a deletion, never a rename, yet the
  front-matter ``id`` on both sides of the commit proves the move;
- an identity-migrated corpus: a move the migration (or a later commit)
  made of a document off an address it does not own is no lineage of that
  address, because the address names its ``slugs.json`` owner (#921).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from lovspor.errors import CorpusStateError, IdentitySchemaError
from lovspor.publish.inventory import build_inventory
from lovspor.publish.redirect_identity import occupied_moves
from lovspor.publish.redirects import RedirectMap, build_redirect_map
from lovspor.snapshot import CorpusSnapshot

_STAMP = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str, day: int = 1) -> None:
    date = f"2026-02-{day:02d}T00:00:00Z"
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **_STAMP, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )


def _doc(doc_id: str, ref: str, pids: tuple[str, ...]) -> str:
    sections = "\n".join(f"### § {pid}. Tittel {doc_id}\n\nTekst {doc_id} {pid}.\n" for pid in pids)
    return (
        "---\n"
        f'id: "{doc_id}"\n'
        f'title: "Tittel {doc_id}"\n'
        'language: "nb"\n'
        f'ref_id: "{ref}"\n'
        'retrieved_at: "2026-01-03T00:00:00+00:00"\n'
        "---\n"
        "\n"
        f"# Tittel {doc_id}\n"
        "\n"
        f"{sections}"
    )


def _record(path: str, slug: str, status: str = "current") -> dict[str, object]:
    return {
        "doc_type": "lov",
        "xml_hash": "a" * 64,
        "markdown_path": path,
        "source_dataset": "gjeldende-lover",
        "status": status,
        "slug": slug,
        "title": slug.capitalize(),
        "renderer_version": 8,
        "last_seen": "2026-01-01T00:00:00Z",
    }


def _commit(repo: Path, documents: dict[str, dict[str, object]], day: int) -> None:
    manifest = {"version": 1, "generated_at": "2026-01-01T00:00:00Z", "documents": documents}
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"day {day}", day=day)


def _register(repo: Path, owners: dict[str, str]) -> None:
    """Write ``slugs.json`` (lover only) and declare the identity schema."""
    entries = {
        slug: {"owner": owner, "assigned_at": "2026-01-01T00:00:00Z", "basis": "first-holder"}
        for slug, owner in owners.items()
    }
    registry = {"schema_version": 1, "slugs": {"lover": entries}}
    (repo / "slugs.json").write_text(json.dumps(registry), encoding="utf-8")
    (repo / "identity.json").write_text('{"schema_version": 1}\n', encoding="utf-8")


def _migrate(repo: Path, owners: dict[str, str], documents: dict[str, dict[str, object]]) -> None:
    """Commit an identity migration: ``identity.json`` and ``slugs.json``."""
    _register(repo, owners)
    _commit(repo, documents, day=20)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lovverk"
    (repo / "lover").mkdir(parents=True)
    _git(repo, "init", "-q")
    return repo


def _map_at_head(repo: Path) -> RedirectMap:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    snapshot = CorpusSnapshot(repo, sha)
    inventory = build_inventory(snapshot.manifest, snapshot.read_text)
    return build_redirect_map(repo, sha, inventory, snapshot.manifest)


_REFS = ("doc-a", "doc-b", "doc-c", "doc-old", "doc-new", "doc-keep", "doc-y")


def _write(repo: Path, name: str, doc_id: str, pids: tuple[str, ...] = ("1", "2")) -> None:
    ref = f"lov/2020-01-01-{_REFS.index(doc_id) + 1}"
    (repo / "lover" / name).write_text(_doc(doc_id, ref, pids), encoding="utf-8")


def _occupied_move(tmp_path: Path, arriving: str = "doc-b") -> Path:
    """doc-a at a.md and doc-b at a-2.md; then, in one commit, a.md gets
    ``arriving``'s text, a-2.md is deleted and doc-a is removed."""
    repo = _repo(tmp_path)
    _write(repo, "a.md", "doc-a")
    _write(repo, "a-2.md", "doc-b", ("1", "3"))
    _commit(
        repo,
        {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/a-2.md", "a-2")},
        day=1,
    )
    _write(repo, "a.md", arriving, ("1", "3"))
    (repo / "lover/a-2.md").unlink()
    _commit(
        repo,
        {
            "doc-a": _record("lover/a.md", "a", status="removed"),
            arriving: _record("lover/a.md", "a"),
        },
        day=2,
    )
    return repo


class TestMoveOntoAnOccupiedPath:
    @pytest.mark.parametrize("prior_id", [None, "doc-b", "doc-a"])
    @pytest.mark.parametrize("arrival_id", ["doc-b", "doc-c"])
    def test_both_file_identities_must_support_the_manifest_move(
        self, tmp_path: Path, prior_id: str | None, arrival_id: str
    ) -> None:
        """URL contract §4 requires another ID before and the moving ID after.

        The manifest alone cannot prove either side of the occupied-path move.
        Include the valid pair as a control for the same real Git history.
        """
        repo = _repo(tmp_path)
        _write(repo, "a-2.md", "doc-b")
        if prior_id is None:
            (repo / "lover/a.md").write_text("# No document identity\n", encoding="utf-8")
        else:
            _write(repo, "a.md", prior_id)
        _commit(repo, {"doc-b": _record("lover/a-2.md", "a-2")}, day=1)

        _write(repo, "a.md", arrival_id)
        (repo / "lover/a-2.md").unlink()
        _commit(repo, {"doc-b": _record("lover/a.md", "a")}, day=2)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

        moves = occupied_moves(repo, [(sha, "lover/a-2.md")], frozenset({"doc-b"}))

        expected = (
            {(sha, "lover/a-2.md"): ("lover/a.md", "doc-b")}
            if prior_id == "doc-a" and arrival_id == "doc-b"
            else {}
        )
        assert moves == expected

    @pytest.mark.parametrize("evidence", ["no-manifest", "no-document", "wrong-path"])
    def test_the_move_requires_the_same_commits_manifest_evidence(
        self, tmp_path: Path, evidence: str
    ) -> None:
        """URL contract §4 requires the arrival path in the move commit's manifest."""
        repo = _occupied_move(tmp_path)
        path = repo / "manifest.json"
        if evidence == "no-manifest":
            path.unlink()
        else:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if evidence == "no-document":
                del manifest["documents"]["doc-b"]
            else:
                manifest["documents"]["doc-b"]["markdown_path"] = "lover/elsewhere.md"
            path.write_text(json.dumps(manifest), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--amend", "--no-edit", day=2)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

        assert occupied_moves(repo, [(sha, "lover/a-2.md")], frozenset({"doc-b"})) == {}

    def test_the_front_matter_id_proves_the_move_git_records_as_a_deletion(
        self, tmp_path: Path
    ) -> None:
        redirect_map = _map_at_head(_occupied_move(tmp_path))
        assert ("/lov/a-2/", "/lov/a/") in redirect_map.redirects
        assert ("/lov/a-2/index.json", "/lov/a/index.json") in redirect_map.redirects

    def test_only_the_pids_that_survive_the_move_redirect(self, tmp_path: Path) -> None:
        redirect_map = _map_at_head(_occupied_move(tmp_path))
        assert ("/lov/a-2/paragraf/3/", "/lov/a/paragraf/3/") in redirect_map.redirects
        assert "/lov/a-2/" in redirect_map.gone

    def test_another_document_arriving_on_the_path_proves_nothing(self, tmp_path: Path) -> None:
        redirect_map = _map_at_head(_occupied_move(tmp_path, arriving="doc-c"))
        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/a-2/")]
        assert "/lov/a-2/" in redirect_map.gone

    def test_the_document_reappearing_in_a_later_commit_proves_nothing(
        self, tmp_path: Path
    ) -> None:
        repo = _repo(tmp_path)
        _write(repo, "a.md", "doc-a")
        _write(repo, "b.md", "doc-b")
        _commit(repo, {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/b.md", "b")}, 1)
        (repo / "lover/b.md").unlink()
        _commit(
            repo,
            {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/b.md", "b", "removed")},
            day=2,
        )
        _write(repo, "a.md", "doc-b")
        _commit(
            repo,
            {"doc-a": _record("lover/a.md", "a", "removed"), "doc-b": _record("lover/a.md", "a")},
            3,
        )
        redirect_map = _map_at_head(repo)
        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/b/")]
        assert "/lov/b/" in redirect_map.gone


def _later_holder(tmp_path: Path) -> Path:
    """doc-old held x.md first and was removed; doc-new then took x.md."""
    repo = _repo(tmp_path)
    _write(repo, "x.md", "doc-old")
    _write(repo, "keep.md", "doc-keep")
    _commit(
        repo,
        {"doc-old": _record("lover/x.md", "x"), "doc-keep": _record("lover/keep.md", "keep")},
        1,
    )
    _write(repo, "x.md", "doc-new", ("1", "3"))
    _commit(
        repo,
        {
            "doc-old": _record("lover/x.md", "x", status="removed"),
            "doc-new": _record("lover/x.md", "x"),
            "doc-keep": _record("lover/keep.md", "keep"),
        },
        day=2,
    )
    return repo


def _moved_off(status: str = "removed") -> dict[str, dict[str, object]]:
    return {
        "doc-old": _record("lover/x.md", "x", status=status),
        "doc-new": _record("lover/x-2026.md", "x-2026"),
        "doc-keep": _record("lover/keep.md", "keep"),
    }


_OWNERS = {"x": "doc-old", "x-2026": "doc-new", "keep": "doc-keep"}


class TestAMigratedAddressNamesItsOwner:
    def test_a_restored_owner_keeps_its_live_address_after_the_later_holder_moves(
        self, tmp_path: Path
    ) -> None:
        repo = _later_holder(tmp_path)
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _write(repo, "x.md", "doc-old")
        _migrate(repo, _OWNERS, _moved_off(status="current"))

        redirect_map = _map_at_head(repo)

        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/x/")]
        assert "/lov/x/" not in redirect_map.gone

    def test_working_tree_edits_cannot_change_the_pinned_identity_verdict(
        self, tmp_path: Path
    ) -> None:
        repo = _later_holder(tmp_path)
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _migrate(repo, _OWNERS, _moved_off())
        before = _map_at_head(repo)
        registry_path = repo / "slugs.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["slugs"]["lover"]["x"]["owner"] = "doc-new"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        (repo / "identity.json").unlink()
        (repo / "manifest.json").write_text("{}", encoding="utf-8")
        _write(repo, "x-2026.md", "doc-c")

        assert _map_at_head(repo) == before
        assert "/lov/x/" in before.gone
        assert not [pair for pair in before.redirects if pair[0].startswith("/lov/x/")]

    def test_the_migration_moving_a_later_holder_off_the_address_is_no_lineage(
        self, tmp_path: Path
    ) -> None:
        repo = _later_holder(tmp_path)
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _migrate(repo, _OWNERS, _moved_off())
        redirect_map = _map_at_head(repo)
        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/x/")]
        assert "/lov/x/" in redirect_map.gone

    def test_a_move_after_the_migration_off_another_owners_address_is_no_lineage(
        self, tmp_path: Path
    ) -> None:
        repo = _later_holder(tmp_path)
        _migrate(
            repo,
            {**_OWNERS, "x": "doc-old"},
            _moved_off() | {"doc-new": _record("lover/x.md", "x")},
        )
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _commit(repo, _moved_off(), day=21)
        redirect_map = _map_at_head(repo)
        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/x/")]

    def test_a_rename_before_the_migration_keeps_its_301(self, tmp_path: Path) -> None:
        repo = _later_holder(tmp_path)
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _commit(repo, _moved_off(), day=3)
        _migrate(repo, _OWNERS, _moved_off())
        redirect_map = _map_at_head(repo)
        assert ("/lov/x/", "/lov/x-2026/") in redirect_map.redirects

    def test_the_owner_moved_after_the_migration_keeps_its_301(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write(repo, "y.md", "doc-y")
        _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 1)
        _migrate(repo, {"y": "doc-y"}, {"doc-y": _record("lover/y.md", "y")})
        _git(repo, "mv", "lover/y.md", "lover/z.md")
        _register(repo, {"y": "doc-y", "z": "doc-y"})
        _commit(repo, {"doc-y": _record("lover/z.md", "z")}, 21)
        redirect_map = _map_at_head(repo)
        assert ("/lov/y/", "/lov/z/") in redirect_map.redirects

    def test_a_legacy_corpus_keeps_the_rename_walk(self, tmp_path: Path) -> None:
        repo = _later_holder(tmp_path)
        _git(repo, "mv", "lover/x.md", "lover/x-2026.md")
        _commit(repo, _moved_off(), day=3)
        redirect_map = _map_at_head(repo)
        assert ("/lov/x/", "/lov/x-2026/") in redirect_map.redirects


def _move_onto(
    repo: Path, deleted: str, target: str, documents: dict[str, dict[str, object]], day: int
) -> None:
    """In one commit: ``target`` gets the deleted file's text and ``deleted`` goes."""
    (repo / "lover" / target).write_bytes((repo / "lover" / deleted).read_bytes())
    (repo / "lover" / deleted).unlink()
    _commit(repo, documents, day=day)


class TestTheProvenDocumentIsTheOneReached:
    def test_a_path_later_overwritten_by_another_document_is_no_successor(
        self, tmp_path: Path
    ) -> None:
        repo = _repo(tmp_path)
        _write(repo, "a.md", "doc-a")
        _write(repo, "p.md", "doc-b")
        _commit(repo, {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/p.md", "p")}, 1)
        _move_onto(
            repo,
            "a.md",
            "p.md",
            {"doc-a": _record("lover/p.md", "p"), "doc-b": _record("lover/p.md", "p", "removed")},
            day=2,
        )
        _write(repo, "p.md", "doc-c")
        _commit(
            repo,
            {"doc-a": _record("lover/p.md", "p", "removed"), "doc-c": _record("lover/p.md", "p")},
            3,
        )
        _write(repo, "q.md", "doc-a")
        _commit(repo, {"doc-a": _record("lover/q.md", "q"), "doc-c": _record("lover/p.md", "p")}, 4)
        redirect_map = _map_at_head(repo)
        assert not [pair for pair in redirect_map.redirects if pair[0].startswith("/lov/a/")]
        assert "/lov/a/" in redirect_map.gone

    def test_a_chain_of_moves_onto_occupied_paths_reaches_the_document(
        self, tmp_path: Path
    ) -> None:
        repo = _repo(tmp_path)
        _write(repo, "a.md", "doc-a")
        _write(repo, "p.md", "doc-b")
        _write(repo, "r.md", "doc-c")
        _commit(
            repo,
            {
                "doc-a": _record("lover/a.md", "a"),
                "doc-b": _record("lover/p.md", "p"),
                "doc-c": _record("lover/r.md", "r"),
            },
            day=1,
        )
        _move_onto(
            repo,
            "a.md",
            "p.md",
            {"doc-a": _record("lover/p.md", "p"), "doc-c": _record("lover/r.md", "r")},
            day=2,
        )
        _move_onto(repo, "p.md", "r.md", {"doc-a": _record("lover/r.md", "r")}, day=3)
        redirect_map = _map_at_head(repo)
        assert ("/lov/a/", "/lov/r/") in redirect_map.redirects
        assert ("/lov/p/", "/lov/r/") in redirect_map.redirects

    def test_two_deleted_copies_of_one_document_prove_no_move(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write(repo, "a.md", "doc-a")
        _write(repo, "b.md", "doc-a")
        _write(repo, "p.md", "doc-b")
        _commit(repo, {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/p.md", "p")}, 1)
        (repo / "lover/b.md").unlink()
        _move_onto(repo, "a.md", "p.md", {"doc-a": _record("lover/p.md", "p")}, day=2)
        redirect_map = _map_at_head(repo)
        froms = {pair[0] for pair in redirect_map.redirects}
        assert "/lov/a/" not in froms
        assert "/lov/b/" not in froms


def _migrated(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _write(repo, "y.md", "doc-y")
    _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 1)
    _migrate(repo, {"y": "doc-y"}, {"doc-y": _record("lover/y.md", "y")})
    return repo


class TestTheRegistryIsReadFailClosed:
    def test_a_corpus_whose_declaration_was_deleted_is_refused(self, tmp_path: Path) -> None:
        repo = _migrated(tmp_path)
        (repo / "identity.json").unlink()
        (repo / "slugs.json").unlink()
        _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 21)
        with pytest.raises(IdentitySchemaError, match="migrated"):
            _map_at_head(repo)

    def test_a_registry_no_declaration_covers_is_refused(self, tmp_path: Path) -> None:
        repo = _migrated(tmp_path)
        (repo / "identity.json").unlink()
        _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 21)
        with pytest.raises(IdentitySchemaError):
            _map_at_head(repo)

    def test_a_shallow_clone_of_a_migrated_corpus_is_refused(self, tmp_path: Path) -> None:
        repo = _migrated(tmp_path)
        shallow = tmp_path / "shallow"
        _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow))
        with pytest.raises(IdentitySchemaError, match="shallow"):
            _map_at_head(shallow)

    def test_a_registry_with_a_repeated_key_is_refused(self, tmp_path: Path) -> None:
        repo = _migrated(tmp_path)
        entry = '{"owner": "doc-y", "assigned_at": "2026-01-01T00:00:00Z", "basis": "first-holder"}'
        (repo / "slugs.json").write_text(
            f'{{"schema_version": 1, "slugs": {{"lover": {{"y": {entry}, "y": {entry}}}}}}}',
            encoding="utf-8",
        )
        _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 21)
        with pytest.raises(IdentitySchemaError, match="duplicate"):
            _map_at_head(repo)

    def test_a_registry_missing_a_manifest_address_is_refused(self, tmp_path: Path) -> None:
        repo = _migrated(tmp_path)
        _write(repo, "w.md", "doc-b")
        _commit(
            repo, {"doc-y": _record("lover/y.md", "y"), "doc-b": _record("lover/w.md", "w")}, 21
        )
        with pytest.raises(IdentitySchemaError, match="misses"):
            _map_at_head(repo)

    def test_an_unregistered_address_gets_no_301(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write(repo, "y.md", "doc-y")
        _commit(repo, {"doc-y": _record("lover/y.md", "y")}, 1)
        _git(repo, "mv", "lover/y.md", "lover/z.md")
        _migrate(repo, {"z": "doc-y"}, {"doc-y": _record("lover/z.md", "z")})
        redirect_map = _map_at_head(repo)
        assert ("/lov/y/", "/lov/z/") not in redirect_map.redirects
        assert "/lov/y/" in redirect_map.gone

    def test_an_unreadable_manifest_at_a_moving_commit_is_refused(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _write(repo, "a.md", "doc-a")
        _write(repo, "p.md", "doc-b")
        _commit(repo, {"doc-a": _record("lover/a.md", "a"), "doc-b": _record("lover/p.md", "p")}, 1)
        (repo / "lover/p.md").write_bytes((repo / "lover/a.md").read_bytes())
        (repo / "lover/a.md").unlink()
        (repo / "manifest.json").write_text('{"documents": {"doc-a": {}}}', encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "broken", day=2)
        _commit(repo, {"doc-a": _record("lover/p.md", "p")}, 3)
        with pytest.raises(CorpusStateError, match="manifest"):
            _map_at_head(repo)
