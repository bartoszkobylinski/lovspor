"""Apply an identity-migration plan to a corpus working tree (#871).

Not reachable from the CLI in this change: the command is dry-run only, and
applying the repair to lovverk is a separate owner decision (migration plan
§7). It exists so the plan's promises are tested on synthetic repositories:
the repair lands as one commit, a second plan on the result finds nothing to
do, and ``git revert`` of that commit restores the base tree exactly.

It writes the planned ``manifest.json`` and ``slugs.json`` (through the same
guards the sync uses) and moves each renamed document's Markdown and
embedding sidecar to its new slug, rewriting the front matter ``slug``. It
does not regenerate history; that is the follow-up rebuild the report lists.
"""

import re
from pathlib import Path

from lovspor.identity.migration import Rename
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.records import replace_manifest
from lovspor.identity.registry import replace_registry

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


def apply_plan(repo: Path, plan: MigrationPlan) -> None:
    """Write ``plan``'s changes into ``repo``'s working tree; the caller commits."""
    replace_manifest(plan.planned_manifest, repo / "manifest.json")
    replace_registry(plan.planned_registry, repo)
    for rename in plan.renames:
        _move_markdown(repo, rename)
        _move_sidecar(repo, rename)
