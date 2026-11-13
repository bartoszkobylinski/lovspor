"""Apply an identity-migration plan to a corpus working tree (#871).

Not reachable from the CLI in this change: the command is dry-run only, and
applying the repair to lovverk is a separate owner decision (migration plan
§7). It exists so the plan's promises are tested on synthetic repositories:
the repair lands as one commit, a second plan on the result finds nothing to
do, and ``git revert`` of that commit restores the base tree exactly (the
reverted corpus is then refused by the downgrade guard, N1: a revert is not a
supported rollback once pushed).

It writes the planned ``manifest.json`` and ``slugs.json`` (through the same
guards the sync uses), then ``identity.json`` declaring the enforced identity
schema, last, so a corpus is never declared before its registry exists; and
it moves each renamed document's Markdown and
embedding sidecar to its new slug, rewriting the front matter ``slug``; and
it writes every repaired history file that differs from the base (the slug
histories rebuilt for their owner, renamed holders' new slug histories and
the by-id histories, ``lovspor.identity.repair_plan``). The planned by-id
files are already merged append-only with the base, so writing them adds and
never drops an event.
"""

import re
from pathlib import Path

from lovspor.atomic_io import atomic_write_text
from lovspor.identity.migration import Rename
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.records import replace_manifest
from lovspor.identity.registry import replace_registry
from lovspor.identity.schema import write_declaration

_SLUG_LINE = re.compile(r'^slug: ".*"$', re.MULTILINE)


def _move_markdown(repo: Path, rename: Rename) -> None:
    source = repo / rename.directory / f"{rename.from_slug}.md"
    text = _SLUG_LINE.sub(f'slug: "{rename.to_slug}"', source.read_text(encoding="utf-8"), count=1)
    (repo / rename.directory / f"{rename.to_slug}.md").write_text(text, encoding="utf-8")
    source.unlink()


def _move_sidecar(repo: Path, rename: Rename) -> None:
    source = repo / rename.directory / "embeddings" / f"{rename.from_slug}.bin"
    if source.exists():
        source.rename(source.with_name(f"{rename.to_slug}.bin"))


def _write_histories(repo: Path, plan: MigrationPlan) -> None:
    for rel_path in plan.history_repair.changed:
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, plan.history_repair.files[rel_path])


def apply_plan(repo: Path, plan: MigrationPlan) -> None:
    """Write ``plan``'s changes into ``repo``'s working tree; the caller commits."""
    replace_manifest(plan.planned_manifest, repo / "manifest.json")
    replace_registry(plan.planned_registry, repo)
    for rename in plan.renames:
        _move_markdown(repo, rename)
        _move_sidecar(repo, rename)
    _write_histories(repo, plan)
    write_declaration(repo)
