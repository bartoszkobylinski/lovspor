"""``lovspor ops identity-apply``: write a reviewed #871 plan into a lovverk checkout (#910).

The owner runs it on a clean checkout of the plan's base, with the directory
``lovspor ops identity-plan`` wrote. It refuses, before writing anything:

- a plan directory inside the checkout, a file in it that ``SHA256SUMS`` does
  not list, a listed file that is missing, or one whose digest differs;
- a checkout that is not a repository root, whose ``HEAD`` is not the plan's
  ``base_commit``, or whose working tree is not clean (so a second apply, before
  or after the commit, is refused);
- a plan directory that is not, byte for byte, the plan this engine computes
  at that base and threshold (a forged plan with re-computed checksums);
- a blocked plan, a plan with nothing to do, and a rename whose Markdown the
  plan could not rewrite (``move_problems`` in the report).

It then writes through :func:`lovspor.identity.apply.apply_plan`, the one code
path the tests use, and verifies the working tree: every written file
against its planned digest (the renamed Markdown and the sidecars included),
every moved-away source gone, and ``git status`` naming exactly the plan's
``changed_paths``. It never stages, commits or pushes: the owner does.

Exit codes: 0 applied and verified; 2 refused, nothing written; 3 written but
not as planned (the problems are printed; inspect with ``git status``).
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import IdentityMigrationError
from lovspor.identity.apply import apply_plan, expected_digests, verify_applied
from lovspor.identity.commands import ensure_outside
from lovspor.identity.corpus_git import resolve_commit, status_paths, toplevel
from lovspor.identity.moves import sha256_bytes
from lovspor.identity.plan import MigrationPlan, plan_migration
from lovspor.identity.report import SUMS_FILENAME, report_sums, sha256_text
from lovspor.ops.commands import ops_app

EXIT_REFUSED = 2
EXIT_UNVERIFIED = 3


def _read_sums(plan_dir: Path) -> dict[str, str]:
    """``name -> sha256`` as ``SHA256SUMS`` lists them."""
    try:
        lines = (plan_dir / SUMS_FILENAME).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise IdentityMigrationError(f"{plan_dir}: no readable {SUMS_FILENAME}: {exc}") from exc
    sums: dict[str, str] = {}
    for line in lines:
        digest, sep, name = line.partition("  ")
        if not sep or not name or name in sums:
            raise IdentityMigrationError(f"{SUMS_FILENAME}: malformed line {line!r}")
        sums[name] = digest
    return sums


def verified_plan_files(plan_dir: Path) -> dict[str, str]:
    """``SHA256SUMS`` of ``plan_dir``, once every file in it matches it and no other exists."""
    sums = _read_sums(plan_dir)
    present = {
        path.relative_to(plan_dir).as_posix() for path in plan_dir.rglob("*") if path.is_file()
    } - {SUMS_FILENAME}
    if present != set(sums):
        unlisted, missing = sorted(present - set(sums)), sorted(set(sums) - present)
        raise IdentityMigrationError(f"plan files unlisted {unlisted}, missing {missing}")
    for name, digest in sorted(sums.items()):
        if sha256_bytes((plan_dir / name).read_bytes()) != digest:
            raise IdentityMigrationError(f"{name}: sha256 does not match {SUMS_FILENAME}")
    return sums


def _plan_refs(plan_dir: Path) -> tuple[str, str | None]:
    """The base commit and the U1 threshold ref the plan directory names.

    The ref as given, not its commit: the report records both, so a ref that
    moved since the plan changes the report and the plan is refused.
    """
    try:
        data = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
        return str(data["base_commit"]), data["u1_threshold"]["ref"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityMigrationError(f"{plan_dir}: unreadable report.json: {exc}") from exc


def require_clean_base(repo: Path, base: str) -> None:
    """Refuse a checkout that is not a clean work tree root at ``base``."""
    if toplevel(repo).resolve() != repo.resolve():
        raise IdentityMigrationError(f"{repo} is not the root of its git work tree")
    head = resolve_commit(repo, "HEAD")
    if head != base:
        raise IdentityMigrationError(f"HEAD {head} is not the planned base {base}")
    dirty = status_paths(repo)
    if dirty:
        raise IdentityMigrationError(f"the checkout is not clean: {dirty[:5]}")


def _require_applicable(plan: MigrationPlan) -> None:
    if plan.blocked:
        raise IdentityMigrationError("the plan is blocked; see report.json")
    if plan.nothing_to_do:
        raise IdentityMigrationError("the plan has nothing to do at this base")
    if plan.moves.problems:
        raise IdentityMigrationError(f"move problems: {plan.moves.problems}")


def reviewed_plan(repo: Path, plan_dir: Path) -> MigrationPlan:
    """The plan at the plan directory's base, once it is exactly what the directory holds."""
    ensure_outside(repo, plan_dir)
    sums = verified_plan_files(plan_dir)
    base, threshold = _plan_refs(plan_dir)
    require_clean_base(repo, base)
    plan = plan_migration(repo, base, threshold)
    if report_sums(plan) != sums:
        raise IdentityMigrationError(
            f"{plan_dir} is not the plan identity-plan computes at {base}, threshold {threshold}"
        )
    _require_applicable(plan)
    return plan


def applied_problems(repo: Path, plan: MigrationPlan) -> list[str]:
    """Every way ``repo``'s working tree differs from the applied ``plan``."""
    problems = verify_applied(repo, plan)
    changed = status_paths(repo)
    if changed != plan.changed_paths:
        extra = sorted(set(changed) - set(plan.changed_paths))
        absent = sorted(set(plan.changed_paths) - set(changed))
        problems.append(f"git status differs from changed_paths: extra {extra}, absent {absent}")
    return problems


def _apply_and_check(repo: Path, plan: MigrationPlan) -> list[str]:
    """Apply ``plan``; a refusal past the first write is a problem, not a clean refusal."""
    try:
        apply_plan(repo, plan)
    except IdentityMigrationError as exc:
        return [f"partially written: {exc}"]
    return applied_problems(repo, plan)


def identity_apply_impl(repo: Path, plan_dir: Path) -> tuple[int, str]:
    """Apply the reviewed plan and verify it; ``(exit code, summary line)``."""
    plan = reviewed_plan(repo, plan_dir)
    problems = _apply_and_check(repo, plan)
    summary = {
        "base_commit": plan.base.commit,
        "changed_paths": len(plan.changed_paths),
        "changed_paths_sha256": sha256_text("".join(f"{p}\n" for p in plan.changed_paths)),
        "verified_files": len(expected_digests(plan)),
        "problems": problems,
    }
    code = EXIT_UNVERIFIED if problems else 0
    return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)


@ops_app.command(name="identity-apply")
def identity_apply(
    repo: Annotated[Path, typer.Option(help="A clean lovverk checkout at the plan's base.")],
    plan: Annotated[Path, typer.Option(help="The directory lovspor ops identity-plan wrote.")],
) -> None:
    """Write a reviewed #871 identity plan into the checkout; never commits."""
    try:
        code, summary = identity_apply_impl(repo, plan)
    except IdentityMigrationError as error:
        typer.echo(f"identity-apply: {error}", err=True)
        raise typer.Exit(EXIT_REFUSED) from error
    typer.echo(summary)
    raise typer.Exit(code)
