#!/usr/bin/env python
"""Forward-only repair of the 2026-07-30 corpus-integrity defects.

One-shot tooling for the two corpus repairs mandated by
``docs/evidence/corpus-integrity-root-cause-2026-07-30.md`` (the RCA).
Both repairs land as ORDINARY FORWARD COMMITS in the lovverk checkout
given by ``--corpus-path`` — never force-push, never rebase, never
amend, and this script never pushes anything anywhere.

Repair 1 (RCA defects 1+2 — ``endr-i-økodesignforskriften``)
    Delete ``forskrifter/endr-i-økodesignforskriften.md`` and its
    embedding sidecar — the per-document remove commit the 2026-07-24
    sync should have produced but dropped (non-ASCII path staging bug,
    RCA §2.2). The manifest tombstone is already correct (ADR-0004
    authority) and stays byte-identical.

Repair 2 (RCA defect 3 — ``forskrift-om-omregningsfaktorer``)
    The 2009 act ``sf-20090520-0534`` keeps permanent ownership of the
    bare slug's path (tombstone evidence preserved, no file
    resurrection). The 2026 replacement act ``sf-20260710-1545`` moves
    to its deterministic identity slug — computed by
    ``lovspor.rendering.slug.evaluate_slug_ownership``, never
    hardcoded. The move is a ``git mv`` (lineage preserved) of the
    ``.md``, its embeddings ``.bin``, and its history files; the
    history is regenerated with the identity-boundary
    ``extract_history`` so every event belongs to ``sf-20260710-1545``
    only, ``total_changes``/``last_changed`` are recomputed through the
    engine's own record-update helper, and the manifest + INDEX are
    rewritten by the engine's deterministic writers.

Modes
    Default is ``--dry-run``: print the full pre-modification report
    (replacement slug, every record and artifact path that changes,
    full-corpus slug-ownership impact, additional collisions, exact
    planned commit sequence) without touching anything. ``--execute``
    performs the commits locally. Idempotent: a rerun after success is
    a clean no-op report.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lovspor.history import HistoryRecord, extract_history, write_history
from lovspor.rendering.slug import SlugOwnershipChange, evaluate_slug_ownership
from lovspor.storage.manifest import Manifest, read_manifest, write_manifest
from lovspor.sync.document_io import dataset_dir, delete_document, generate_index
from lovspor.sync.git_commit import add as git_add
from lovspor.sync.git_commit import commit as git_commit
from lovspor.sync.git_commit import has_staged_changes, has_uncommitted_changes
from lovspor.sync.orchestrator import _record_with_history

RCA_DOC = "docs/evidence/corpus-integrity-root-cause-2026-07-30.md"
MANIFEST_FILENAME = "manifest.json"

# RCA defects 1+2: tombstoned 2026-07-24 (commit 3baf017db) but never deleted.
REMOVED_DOC_ID = "sf-20260305-0354"
REMOVED_MD_FALLBACK = "forskrifter/endr-i-økodesignforskriften.md"
REMOVAL_SUBJECT = "remove(forskrift): endr-i-økodesignforskriften"

# RCA defect 3: two ids on forskrifter/forskrift-om-omregningsfaktorer.md.
TOMBSTONE_OWNER_ID = "sf-20090520-0534"
REPLACEMENT_DOC_ID = "sf-20260710-1545"


@dataclass(frozen=True)
class RemovalPlan:
    """Repair 1: the deferred per-document removal commit."""

    md_rel: str
    bin_rel: str
    to_delete: tuple[str, ...]
    blockers: tuple[str, ...] = ()

    @property
    def needed(self) -> bool:
        return bool(self.to_delete)


@dataclass(frozen=True)
class RenamePlan:
    """Repair 2: deterministic slug disambiguation for the 2026 act."""

    change: SlugOwnershipChange | None
    extra_changes: tuple[SlugOwnershipChange, ...]
    moves: tuple[tuple[str, str], ...] = ()
    history: HistoryRecord | None = None
    blockers: tuple[str, ...] = ()

    @property
    def needed(self) -> bool:
        return self.change is not None

    @property
    def subject(self) -> str:
        if self.change is None:  # pragma: no cover - guarded by ``needed``
            raise ValueError("no rename planned")
        return f"rename(forskrift): {self.change.new_slug}"


@dataclass(frozen=True)
class RepairPlan:
    removal: RemovalPlan
    rename: RenamePlan
    head: str

    @property
    def blockers(self) -> tuple[str, ...]:
        extra = tuple(
            f"unexpected slug-ownership change for {c.doc_id} "
            f"({c.markdown_path} -> {c.new_markdown_path}); manual review required"
            for c in self.rename.extra_changes
        )
        return self.removal.blockers + self.rename.blockers + extra


def _git(repo: Path, *args: str) -> str:
    """Run git with list args (shell injection structurally impossible)."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _bin_rel(md_rel: str) -> str:
    md = PurePosixPath(md_rel)
    return str(md.parent / "embeddings" / f"{md.stem}.bin")


def _history_rels(md_rel: str, slug: str) -> tuple[str, str]:
    parent = PurePosixPath(md_rel).parent / "history"
    return str(parent / f"{slug}.json"), str(parent / f"{slug}.md")


def _plan_removal(corpus: Path, manifest: Manifest) -> RemovalPlan:
    record = manifest.documents.get(REMOVED_DOC_ID)
    md_rel = record.markdown_path if record is not None else REMOVED_MD_FALLBACK
    bin_rel = _bin_rel(md_rel)
    to_delete = tuple(rel for rel in (md_rel, bin_rel) if (corpus / rel).exists())
    blockers: list[str] = []
    if to_delete and record is None:
        blockers.append(f"manifest has no record for {REMOVED_DOC_ID}; refusing to delete files")
    if to_delete and record is not None and record.status != "removed":
        blockers.append(
            f"manifest record {REMOVED_DOC_ID} has status {record.status!r}, expected 'removed'",
        )
    return RemovalPlan(
        md_rel=md_rel,
        bin_rel=bin_rel,
        to_delete=to_delete,
        blockers=tuple(blockers),
    )


def _plan_rename(corpus: Path, manifest: Manifest) -> RenamePlan:
    changes = evaluate_slug_ownership(manifest.documents)
    ours = tuple(c for c in changes if c.doc_id == REPLACEMENT_DOC_ID)
    extra = tuple(c for c in changes if c.doc_id != REPLACEMENT_DOC_ID)
    if not ours:
        return RenamePlan(change=None, extra_changes=extra)
    change = ours[0]
    blockers = _rename_blockers(corpus, manifest, change)
    if blockers:
        return RenamePlan(change=change, extra_changes=extra, blockers=blockers)
    moves = _rename_moves(corpus, change)
    history = extract_history(
        repo_path=corpus,
        current_path=change.markdown_path,
        doc_id=REPLACEMENT_DOC_ID,
        slug=change.new_slug,
    )
    return RenamePlan(change=change, extra_changes=extra, moves=moves, history=history)


def _rename_blockers(
    corpus: Path,
    manifest: Manifest,
    change: SlugOwnershipChange,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if change.owner_doc_id != TOMBSTONE_OWNER_ID:
        blockers.append(
            f"path owner is {change.owner_doc_id}, expected tombstone {TOMBSTONE_OWNER_ID}",
        )
    record = manifest.documents.get(REPLACEMENT_DOC_ID)
    if record is None or record.status != "current":
        blockers.append(f"manifest record {REPLACEMENT_DOC_ID} is missing or not 'current'")
    owner = manifest.documents.get(change.owner_doc_id)
    if owner is None or owner.status != "removed":
        blockers.append(f"owner record {change.owner_doc_id} is missing or not a tombstone")
    if not (corpus / change.markdown_path).exists():
        blockers.append(f"{change.markdown_path} does not exist on disk")
    if (corpus / change.new_markdown_path).exists():
        blockers.append(f"target path {change.new_markdown_path} already exists on disk")
    return tuple(blockers)


def _rename_moves(corpus: Path, change: SlugOwnershipChange) -> tuple[tuple[str, str], ...]:
    """(old, new) repo-relative pairs for every artifact that moves.

    The ``.md`` always moves. The ``.bin`` and the two history files
    move only when present on disk — a missing sidecar is not an error,
    the regenerated history files are (re)created by ``write_history``.
    """
    moves = [(change.markdown_path, change.new_markdown_path)]
    optional = [(_bin_rel(change.markdown_path), _bin_rel(change.new_markdown_path))]
    optional.extend(
        zip(
            _history_rels(change.markdown_path, change.slug),
            _history_rels(change.new_markdown_path, change.new_slug),
            strict=True,
        ),
    )
    moves.extend((old, new) for old, new in optional if (corpus / old).exists())
    return tuple(moves)


def _build_plan(corpus: Path, manifest: Manifest) -> RepairPlan:
    return RepairPlan(
        removal=_plan_removal(corpus, manifest),
        rename=_plan_rename(corpus, manifest),
        head=_git(corpus, "rev-parse", "--short", "HEAD"),
    )


def _removal_message() -> str:
    return "\n".join(
        [
            REMOVAL_SUBJECT,
            "",
            "Deferred removal from the 2026-07-24 sync: commit 3baf017db",
            f"tombstoned {REMOVED_DOC_ID} in manifest.json, but the",
            "per-document remove commit was skipped — git ls-files C-quoted",
            "the non-ASCII path, so the staged deletion was misclassified as",
            "an orphan and dropped (engine bug, fixed in lovspor git_commit).",
            "The manifest tombstone is already correct and is not touched.",
            "",
            f"See {RCA_DOC} (defects 1-2).",
        ],
    )


def _rename_message(plan: RenamePlan) -> str:
    if plan.change is None:  # pragma: no cover - guarded by ``needed`` at call site
        raise ValueError("no rename planned")
    return "\n".join(
        [
            plan.subject,
            "",
            f"Slug-ownership disambiguation (RCA defect 3): {plan.change.markdown_path}",
            f"is permanently owned by {plan.change.owner_doc_id} (tombstoned 2026-07-14,",
            "evidence preserved, no file resurrection). The replacement act",
            f"{plan.change.doc_id} moves to its deterministic identity slug",
            f"{plan.change.new_slug}. History regenerated at the identity boundary so",
            f"events belong to {plan.change.doc_id} only; total_changes and",
            "last_changed recomputed from that history; manifest and INDEX",
            "rewritten deterministically.",
            "",
            f"See {RCA_DOC} (defect 3).",
        ],
    )


@dataclass
class _Report:
    lines: list[str] = field(default_factory=list)

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)


def _report_removal(report: _Report, plan: RemovalPlan) -> None:
    report.add(f"Repair 1 (defects 1+2) — {REMOVAL_SUBJECT}")
    if not plan.needed:
        report.add("  status: already applied — no files on disk, nothing to do", "")
        return
    report.add("  status: pending")
    for rel in plan.to_delete:
        report.add(f"  delete: {rel}")
    report.add(f"  manifest: untouched (tombstone {REMOVED_DOC_ID} already correct)", "")


def _report_rename(report: _Report, plan: RenamePlan) -> None:
    report.add(f"Repair 2 (defect 3) — slug ownership for {REPLACEMENT_DOC_ID}")
    if plan.change is None:
        report.add("  status: already applied — no duplicate markdown_path, nothing to do", "")
        return
    change = plan.change
    report.add(
        "  status: pending" if not plan.blockers else "  status: BLOCKED",
        f"  replacement slug: {change.new_slug}",
        f"  bare-slug owner: {change.owner_doc_id} (tombstone, keeps {change.markdown_path})",
    )
    for old, new in plan.moves:
        report.add(f"  git mv: {old} -> {new}")
    if plan.history is not None:
        events = plan.history.events
        last_changed = events[0].date.isoformat() if events else None
        report.add(
            f"  manifest {change.doc_id}: markdown_path -> {change.new_markdown_path}, "
            f"slug -> {change.new_slug}, "
            f"total_changes -> {len(events)}, last_changed -> {last_changed}",
            f"  regenerated history: {len(events)} event(s), doc_id {plan.history.doc_id} only",
        )
    report.add("  index: regenerated from the updated manifest", "")


def _report_ownership(report: _Report, plan: RenamePlan) -> None:
    changes = (() if plan.change is None else (plan.change,)) + plan.extra_changes
    report.add("Full-corpus slug-ownership impact (evaluate_slug_ownership over manifest.json):")
    if not changes:
        report.add("  no record occupies a markdown_path owned by a different doc_id")
    for c in sorted(changes, key=lambda c: c.doc_id):
        report.add(
            f"  {c.doc_id}: {c.markdown_path} -> {c.new_markdown_path} (owner {c.owner_doc_id})",
        )
    if plan.extra_changes:
        report.add(
            f"  WARNING: {len(plan.extra_changes)} collision(s) beyond the RCA defect —"
            " execute is blocked pending manual review",
        )
    else:
        report.add("  additional collisions beyond the RCA defect: none")
    report.add("")


def _report_commits(report: _Report, plan: RepairPlan) -> None:
    report.add("Planned commit sequence (forward-only, local, never pushed):")
    step = 0
    if plan.removal.needed:
        step += 1
        report.add(f"  {step}. {REMOVAL_SUBJECT}")
        for rel in plan.removal.to_delete:
            report.add(f"     D {rel}")
    if plan.rename.needed and not plan.rename.blockers:
        step += 1
        report.add(f"  {step}. {plan.rename.subject}")
        for old, new in plan.rename.moves:
            report.add(f"     R {old} -> {new}")
        report.add(f"     M {MANIFEST_FILENAME}", "     M INDEX.md (affected dataset)")
    if step == 0:
        report.add("  none — corpus already repaired (clean no-op)")
    report.add("")


def _print_report(corpus: Path, plan: RepairPlan, *, execute: bool) -> None:
    report = _Report()
    mode = "EXECUTE (local commits, no push)" if execute else "DRY RUN — nothing will be modified"
    report.add(f"corpus: {corpus} (HEAD {plan.head})", f"mode: {mode}", "")
    _report_removal(report, plan.removal)
    _report_rename(report, plan.rename)
    _report_ownership(report, plan.rename)
    _report_commits(report, plan)
    for blocker in plan.blockers:
        report.add(f"BLOCKER: {blocker}")
    print("\n".join(report.lines))


def _execute_removal(corpus: Path, plan: RemovalPlan) -> None:
    """Mirror of the sync removal loop: unlink, stage, per-doc commit."""
    for rel in plan.to_delete:
        delete_document(corpus / rel)
    git_add(corpus, [corpus / plan.md_rel, corpus / plan.bin_rel])
    if has_staged_changes(corpus):
        git_commit(corpus, _removal_message())


def _execute_rename(corpus: Path, manifest: Manifest, plan: RenamePlan) -> None:
    if plan.change is None or plan.history is None:  # pragma: no cover - guarded at call site
        raise ValueError("no rename planned")
    for old, new in plan.moves:
        _git(corpus, "mv", "--", old, new)
    record = manifest.documents[REPLACEMENT_DOC_ID]
    json_path, md_path = write_history(plan.history, dataset_dir(corpus, record.source_dataset))
    moved = record.model_copy(
        update={"markdown_path": plan.change.new_markdown_path, "slug": plan.change.new_slug},
    )
    repaired = _record_with_history(moved, plan.history)
    documents = {**manifest.documents, REPLACEMENT_DOC_ID: repaired}
    updated = manifest.model_copy(update={"documents": documents})
    manifest_path = corpus / MANIFEST_FILENAME
    write_manifest(updated, manifest_path)
    index_path = generate_index(corpus, record.source_dataset, updated)
    new_paths = [corpus / new for _, new in plan.moves]
    git_add(corpus, [*new_paths, json_path, md_path, manifest_path, index_path])
    if has_staged_changes(corpus):
        git_commit(corpus, _rename_message(plan))


def _execute(corpus: Path, manifest: Manifest, plan: RepairPlan) -> int:
    if plan.blockers:
        print("refusing to execute; blockers reported above", file=sys.stderr)
        return 1
    if not plan.removal.needed and not plan.rename.needed:
        return 0
    if has_uncommitted_changes(corpus):
        print(f"refusing to execute: {corpus} has uncommitted changes", file=sys.stderr)
        return 1
    if plan.removal.needed:
        _execute_removal(corpus, plan.removal)
    if plan.rename.needed:
        _execute_rename(corpus, manifest, plan.rename)
    print(f"done; new HEAD {_git(corpus, 'rev-parse', '--short', 'HEAD')} (nothing pushed)")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument(
        "--corpus-path",
        type=Path,
        required=True,
        help="path to the lovverk checkout to repair",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="print the pre-modification report without touching anything (default)",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="perform the repairs as local forward commits (never pushes)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    corpus = args.corpus_path.resolve()
    if not (corpus / ".git").exists():
        print(f"not a git repository: {corpus}", file=sys.stderr)
        return 1
    manifest_path = corpus / MANIFEST_FILENAME
    if not manifest_path.exists():
        print(f"no {MANIFEST_FILENAME} in {corpus}", file=sys.stderr)
        return 1
    manifest = read_manifest(manifest_path)
    plan = _build_plan(corpus, manifest)
    _print_report(corpus, plan, execute=args.execute)
    if not args.execute:
        return 0
    return _execute(corpus, manifest, plan)


if __name__ == "__main__":
    raise SystemExit(main())
