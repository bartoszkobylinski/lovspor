"""Tests for scripts/repair_corpus_integrity_20260730.py.

Each test builds a miniature lovverk-shaped git repo in ``tmp_path``
reproducing the exact defect shapes of the 2026-07-30 root-cause
analysis (docs/evidence/corpus-integrity-root-cause-2026-07-30.md):

- defects 1+2: ``sf-20260305-0354`` tombstoned in the manifest while
  ``forskrifter/endr-i-økodesignforskriften.md`` and its embeddings
  sidecar are still on disk;
- defect 3: tombstone ``sf-20090520-0534`` and current record
  ``sf-20260710-1545`` sharing ``forskrift-om-omregningsfaktorer.md``,
  with the file overwritten in place (M, not A — the ce3df5a13 shape)
  and fused history files stamped with the new act's doc_id.

The script module is loaded directly from ``scripts/`` (not a package)
and driven through ``main()`` — no subprocess.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lovspor.history import HistoryEvent, HistoryRecord, write_history
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_corpus_integrity_20260730.py"
_spec = importlib.util.spec_from_file_location("repair_corpus_integrity_20260730", _SCRIPT)
assert _spec is not None and _spec.loader is not None
repair = importlib.util.module_from_spec(_spec)
# Registered before exec: the dataclass machinery resolves the defining
# module through sys.modules while the class body is being processed.
sys.modules[_spec.name] = repair
_spec.loader.exec_module(repair)

OLD_ID = "sf-20090520-0534"
NEW_ID = "sf-20260710-1545"
GONE_ID = "sf-20260305-0354"
DATASET = "gjeldende-sentrale-forskrifter"
BASE_SLUG = "forskrift-om-omregningsfaktorer"
GONE_SLUG = "endr-i-økodesignforskriften"
SHARED_MD = f"forskrifter/{BASE_SLUG}.md"
SHARED_BIN = f"forskrifter/embeddings/{BASE_SLUG}.bin"
GONE_MD = f"forskrifter/{GONE_SLUG}.md"
GONE_BIN = f"forskrifter/embeddings/{GONE_SLUG}.bin"
EXPECTED_SLUG = f"{BASE_SLUG}-2026"
EXPECTED_MD = f"forskrifter/{EXPECTED_SLUG}.md"
EXPECTED_BIN = f"forskrifter/embeddings/{EXPECTED_SLUG}.bin"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _doc(doc_id: str, body: str) -> str:
    return f'---\nid: "{doc_id}"\nstatus: "current"\n---\n\n# Act {doc_id}\n\n{body}\n'


def _record(doc_id: str, slug: str, md: str, status: str, **extra: object) -> ManifestRecord:
    return ManifestRecord(
        doc_type="forskrift",
        xml_hash=f"hash-{doc_id}",
        markdown_path=md,
        source_dataset=DATASET,
        last_seen=datetime(2026, 7, 1, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=f"Tittel {doc_id}",
        **extra,  # type: ignore[arg-type]
    )


def _write_fused_history(repo: Path) -> None:
    """The defect-3 history shape: NEW_ID stamped over fused events
    including the OLD act's provenance (its rename from the id path)."""
    fused = HistoryRecord(
        slug=BASE_SLUG,
        doc_id=NEW_ID,
        events=[
            HistoryEvent(
                date=date(2026, 7, 14),
                commit="abc1234",
                type="added",
                subject=f"add(forskrift): {BASE_SLUG}",
                lines_added=51,
                lines_removed=25,
            ),
            HistoryEvent(
                date=date(2026, 4, 27),
                commit="def5678",
                type="renamed",
                subject="migration: rename 4522 documents to slug-based filenames",
                from_path=f"forskrifter/{OLD_ID}.md",
                to_path=SHARED_MD,
            ),
        ],
    )
    write_history(fused, repo / "forskrifter")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Miniature lovverk repo reproducing both RCA defect shapes."""
    repo = tmp_path / "lovverk"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "forskrifter" / "embeddings").mkdir(parents=True)

    # Commit 1: the 2009 act at the slug path + the økodesign endring.
    (repo / SHARED_MD).write_text(_doc(OLD_ID, "Old act body."), encoding="utf-8")
    (repo / GONE_MD).write_text(_doc(GONE_ID, "Endring body."), encoding="utf-8")
    (repo / GONE_BIN).write_bytes(b"LSPE-gone")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sync: 2 new, 0 changed, 0 removed")

    # Commit 2: the 2026 act overwrites the file in place (M, not A).
    (repo / SHARED_MD).write_text(_doc(NEW_ID, "New act body, replacing."), encoding="utf-8")
    (repo / SHARED_BIN).write_bytes(b"LSPE-new")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"add(forskrift): {BASE_SLUG}")

    # Commit 3: manifest tail — tombstones + fused history files.
    _write_fused_history(repo)
    manifest = Manifest(
        generated_at=datetime(2026, 7, 30, tzinfo=UTC),
        documents={
            GONE_ID: _record(GONE_ID, GONE_SLUG, GONE_MD, "removed"),
            OLD_ID: _record(OLD_ID, BASE_SLUG, SHARED_MD, "removed"),
            NEW_ID: _record(
                NEW_ID,
                BASE_SLUG,
                SHARED_MD,
                "current",
                total_changes=4,
                last_changed="2026-07-14",
            ),
        },
    )
    write_manifest(manifest, repo / "manifest.json")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sync: update manifest, index, and history")
    return repo


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _subjects(repo: Path, count: int) -> list[str]:
    return _git(repo, "log", f"-{count}", "--format=%s").strip().split("\n")


def _name_status(repo: Path, ref: str) -> list[tuple[str, ...]]:
    lines = _git(repo, "diff-tree", "--name-status", "-M", "-r", "--root", ref).strip().split("\n")
    return [tuple(line.split("\t")) for line in lines[1:] if line]


def test_dry_run_reports_plan_without_touching_corpus(
    corpus: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    head_before = _git(corpus, "rev-parse", "HEAD")
    assert repair.main(["--corpus-path", str(corpus)]) == 0
    out = capsys.readouterr().out
    # Mandated report content: replacement slug, changed paths, ownership
    # impact, planned commit sequence with subjects.
    assert f"replacement slug: {EXPECTED_SLUG}" in out
    assert GONE_MD in out
    assert GONE_BIN in out
    assert f"git mv: {SHARED_MD} -> {EXPECTED_MD}" in out
    assert f"git mv: {SHARED_BIN} -> {EXPECTED_BIN}" in out
    assert f"remove(forskrift): {GONE_SLUG}" in out
    assert f"rename(forskrift): {EXPECTED_SLUG}" in out
    assert f"{NEW_ID}: {SHARED_MD} -> {EXPECTED_MD} (owner {OLD_ID})" in out
    assert "additional collisions beyond the RCA defect: none" in out
    assert "total_changes -> 1" in out
    # Nothing was touched: same HEAD, clean worktree, files still present.
    assert _git(corpus, "rev-parse", "HEAD") == head_before
    assert _git(corpus, "status", "--porcelain").strip() == ""
    assert (corpus / GONE_MD).exists()
    assert (corpus / SHARED_MD).exists()


def test_execute_creates_exactly_two_conformant_commits(corpus: Path) -> None:
    before = _commit_count(corpus)
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 0
    assert _commit_count(corpus) - before == 2
    rename_subject, remove_subject = _subjects(corpus, 2)
    assert remove_subject == f"remove(forskrift): {GONE_SLUG}"
    assert rename_subject == f"rename(forskrift): {EXPECTED_SLUG}"

    # Repair 1 commit: exactly the two deferred deletions, nothing else.
    remove_ops = _name_status(corpus, "HEAD~1")
    assert sorted(remove_ops) == [("D", GONE_BIN), ("D", GONE_MD)]

    # Repair 2 commit: md + bin moved as renames (lineage preserved),
    # manifest and INDEX rewritten.
    rename_ops = _name_status(corpus, "HEAD")
    by_kind = {(op[0][0], *op[1:]) for op in rename_ops}
    assert ("R", SHARED_MD, EXPECTED_MD) in by_kind
    assert ("R", SHARED_BIN, EXPECTED_BIN) in by_kind
    touched = {op[-1] for op in rename_ops}
    assert "manifest.json" in touched
    assert "forskrifter/INDEX.md" in touched
    assert f"forskrifter/history/{EXPECTED_SLUG}.json" in touched
    assert f"forskrifter/history/{EXPECTED_SLUG}.md" in touched

    # Filesystem end state.
    assert not (corpus / GONE_MD).exists()
    assert not (corpus / GONE_BIN).exists()
    assert not (corpus / SHARED_MD).exists()
    assert (corpus / EXPECTED_MD).exists()
    assert f'id: "{NEW_ID}"' in (corpus / EXPECTED_MD).read_text(encoding="utf-8")
    assert not (corpus / f"forskrifter/history/{BASE_SLUG}.json").exists()
    assert _git(corpus, "status", "--porcelain").strip() == ""


def test_execute_updates_manifest_and_preserves_tombstones(corpus: Path) -> None:
    before = read_manifest(corpus / "manifest.json")
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 0
    after = read_manifest(corpus / "manifest.json")

    # Defect-1 tombstone byte-identical: the removal is file-only.
    assert after.documents[GONE_ID] == before.documents[GONE_ID]
    # Defect-3 tombstone keeps its original path — evidence preserved,
    # no file resurrection.
    assert after.documents[OLD_ID] == before.documents[OLD_ID]
    assert after.documents[OLD_ID].markdown_path == SHARED_MD
    assert after.documents[OLD_ID].status == "removed"

    updated = after.documents[NEW_ID]
    assert updated.markdown_path == EXPECTED_MD
    assert updated.slug == EXPECTED_SLUG
    assert updated.status == "current"
    # Recomputed from the identity-bounded history: only the overwrite
    # commit belongs to the 2026 act (RCA: 4 -> 1).
    assert updated.total_changes == 1
    assert updated.last_changed == datetime.now(UTC).date().isoformat()


def test_regenerated_history_contains_only_same_id_events(corpus: Path) -> None:
    overwrite_sha = _git(corpus, "rev-parse", "--short=7", "HEAD~1").strip()
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 0
    payload = json.loads(
        (corpus / f"forskrifter/history/{EXPECTED_SLUG}.json").read_text(encoding="utf-8"),
    )
    assert payload["doc_id"] == NEW_ID
    assert payload["slug"] == EXPECTED_SLUG
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event["type"] == "added"
    assert event["commit"] == overwrite_sha
    # No trace of the 2009 act's provenance in the 2026 act's history.
    assert OLD_ID not in json.dumps(payload)
    assert payload["events"][0]["from_path"] is None


def test_execute_is_idempotent(corpus: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 0
    count_after_first = _commit_count(corpus)
    manifest_after_first = (corpus / "manifest.json").read_bytes()
    capsys.readouterr()

    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 0
    out = capsys.readouterr().out
    assert _commit_count(corpus) == count_after_first
    assert (corpus / "manifest.json").read_bytes() == manifest_after_first
    assert "already applied" in out
    assert "none — corpus already repaired (clean no-op)" in out
    assert _git(corpus, "status", "--porcelain").strip() == ""


def test_execute_refuses_dirty_worktree(corpus: Path) -> None:
    (corpus / "stray.txt").write_text("uncommitted", encoding="utf-8")
    before = _commit_count(corpus)
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 1
    assert _commit_count(corpus) == before
    assert (corpus / GONE_MD).exists()


def test_execute_refuses_unexpected_ownership_collisions(corpus: Path) -> None:
    """A collision the RCA did not analyze blocks execution for review."""
    manifest = read_manifest(corpus / "manifest.json")
    extra_path = "forskrifter/annen-forskrift.md"
    documents = {
        **manifest.documents,
        "sf-20200101-0001": _record("sf-20200101-0001", "annen-forskrift", extra_path, "removed"),
        "sf-20260101-0002": _record("sf-20260101-0002", "annen-forskrift", extra_path, "current"),
    }
    write_manifest(manifest.model_copy(update={"documents": documents}), corpus / "manifest.json")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-q", "-m", "sync: update manifest, index, and history")

    before = _commit_count(corpus)
    assert repair.main(["--corpus-path", str(corpus), "--execute"]) == 1
    assert _commit_count(corpus) == before
