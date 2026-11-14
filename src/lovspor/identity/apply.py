"""Apply an identity-migration plan to a corpus working tree (#871, #910).

The one code path that writes a plan: ``lovspor ops identity-apply`` runs it
after its own checks (``lovspor.identity.apply_commands``), and the tests run
it on synthetic repositories. It never commits: the repair lands as one
commit the owner makes, a second plan on the result finds nothing to do, and
``git revert`` of that commit restores the base tree exactly (the reverted
corpus is then refused by the downgrade guard, N1: a revert is not a
supported rollback once pushed).

It writes the planned ``manifest.json`` and ``slugs.json`` (through the same
guards the sync uses); moves each renamed document's Markdown to its new
slug, writing the planned text with its front-matter ``slug`` already
rewritten, and its embedding sidecar, refusing a planned sidecar that is not
there; writes every repaired history file that differs from the base (the
slug histories rebuilt for their owner, renamed holders' new slug histories
and the by-id histories, ``lovspor.identity.repair_plan``); and writes
``identity.json`` declaring the enforced identity schema last, so a corpus is
never declared before its registry exists. The planned by-id files are
already merged append-only with the base, so writing them adds and never
drops an event.

``verify_applied`` then compares the working tree with the plan: every
written file against its planned digest, every moved-away source gone.
"""

from pathlib import Path

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import IdentityMigrationError
from lovspor.identity.moves import SIDECAR, FileMove, sha256_bytes
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.records import replace_manifest
from lovspor.identity.registry import replace_registry
from lovspor.identity.report import planned_files, sha256_text
from lovspor.identity.schema import write_declaration


def _move(repo: Path, move: FileMove, plan: MigrationPlan) -> None:
    """Move one file as planned; refuse a missing source, an existing target, or
    Markdown the plan could not rewrite."""
    source, target = repo / move.source, repo / move.target
    if not source.is_file():
        raise IdentityMigrationError(f"{move.source}: planned move source is missing")
    if target.exists():
        raise IdentityMigrationError(f"{move.target}: planned move target already exists")
    if move.kind == SIDECAR:
        source.rename(target)
        return
    text = plan.moves.markdown.get(move.target)
    if text is None:
        raise IdentityMigrationError(f"{move.source}: no planned slug rewrite; see the report")
    atomic_write_text(target, text)
    source.unlink()


def _write_histories(repo: Path, plan: MigrationPlan) -> None:
    for rel_path in plan.history_repair.changed:
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, plan.history_repair.files[rel_path])


def apply_plan(repo: Path, plan: MigrationPlan) -> None:
    """Write ``plan``'s changes into ``repo``'s working tree; the caller commits."""
    replace_manifest(plan.planned_manifest, repo / "manifest.json")
    replace_registry(plan.planned_registry, repo)
    for move in plan.moves.moves:
        _move(repo, move, plan)
    _write_histories(repo, plan)
    write_declaration(repo)


def _digest(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def expected_digests(plan: MigrationPlan) -> dict[str, str]:
    """``corpus path -> sha256`` of every file the applied plan leaves, sidecars included."""
    digests = {path: sha256_text(text) for path, text in planned_files(plan).items()}
    digests.update({m.target: m.sha256 for m in plan.moves.sidecars if m.sha256 is not None})
    return dict(sorted(digests.items()))


def verify_applied(repo: Path, plan: MigrationPlan) -> list[str]:
    """Every difference between ``repo``'s working tree and the applied ``plan``."""
    problems = [
        f"{path}: sha256 {_digest(repo / path)} != planned {digest}"
        for path, digest in expected_digests(plan).items()
        if _digest(repo / path) != digest
    ]
    problems += [f"{path}: still present" for path in plan.moves.sources if (repo / path).exists()]
    return problems
