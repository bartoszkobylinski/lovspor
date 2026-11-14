"""Input pins and path confinement for ``lovspor ops identity-apply`` (#910).

The owner pins the plan they reviewed by the sha256 of its ``SHA256SUMS``: a
plan directory swapped for another plan this engine would also compute (an
other threshold, say) is then refused, not applied. Every path the command
reads from the plan directory or writes into the checkout is a relative POSIX
path with no ``..`` part, and no path it writes may lead out of the checkout
through a symlink: the plan's paths come from corpus content, which is input.
"""

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from lovspor.errors import IdentityMigrationError
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.report import SUMS_FILENAME

_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_pinned(plan_dir: Path, expected: str) -> None:
    """Refuse a plan directory whose ``SHA256SUMS`` is not the reviewed one, ``expected``."""
    if not _SHA256.fullmatch(expected):
        raise IdentityMigrationError(f"--expect-sha256 {expected!r} is not a sha256 hex digest")
    try:
        actual = hashlib.sha256((plan_dir / SUMS_FILENAME).read_bytes()).hexdigest()
    except OSError as exc:
        raise IdentityMigrationError(f"{plan_dir}: no readable {SUMS_FILENAME}: {exc}") from exc
    if actual != expected:
        raise IdentityMigrationError(
            f"{plan_dir}/{SUMS_FILENAME} has sha256 {actual}, not the reviewed {expected}"
        )


def require_safe_names(names: Iterable[str]) -> None:
    """Refuse an absolute path, a ``.``, ``..`` or empty part, a backslash, or ``.git``."""
    for name in names:
        parts = name.split("/")
        unsafe = {"", ".", ".."} & set(parts) or parts[0] == ".git"
        if name.startswith("/") or "\\" in name or unsafe:
            raise IdentityMigrationError(f"unsafe path {name!r}: refusing it")


def _escapes(root: Path, path: Path) -> bool:
    """True when ``path``, or the deepest of its parents that exists, resolves out of ``root``."""
    probe = path
    while not probe.exists() and not probe.is_symlink() and probe != root:
        probe = probe.parent
    return probe.is_symlink() or not probe.resolve().is_relative_to(root)


def require_confined(repo: Path, names: Iterable[str]) -> None:
    """Refuse any of ``names`` that is unsafe, or leads out of ``repo`` through a symlink."""
    listed = sorted(set(names))
    require_safe_names(listed)
    root = repo.resolve()
    escaping = [name for name in listed if _escapes(root, root / PurePosixPath(name))]
    if escaping:
        raise IdentityMigrationError(f"paths leading out of {repo}: {escaping[:5]}")


def written_paths(plan: MigrationPlan) -> list[str]:
    """Every checkout path applying ``plan`` writes, moves or removes."""
    moves = plan.moves.moves
    return sorted(
        {
            *plan.changed_paths,
            *plan.history_repair.changed,
            *(move.source for move in moves),
            *(move.target for move in moves),
        }
    )
