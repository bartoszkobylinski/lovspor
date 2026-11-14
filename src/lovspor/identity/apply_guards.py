"""Input pins and path confinement for ``lovspor ops identity-apply`` (#910).

The owner pins the plan they reviewed by the sha256 of its ``SHA256SUMS``: a
plan directory swapped for another plan this engine would also compute (an
other threshold, say) is then refused, not applied. Every path the command
reads from the plan directory or writes into the checkout is a relative POSIX
path with no ``..`` or ``.git`` part, and no path it writes may pass through a
symlink at all: the plan's paths come from corpus content, which is input.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from lovspor.errors import IdentityMigrationError
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.report import SUMS_FILENAME

_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_pinned(plan_dir: Path, expected: str) -> bytes:
    """The bytes of ``SHA256SUMS``, once their sha256 is the reviewed one, ``expected``.

    The caller parses these bytes, never a second read: a file swapped between
    a hash and a re-read would otherwise be applied under the reviewed pin.
    """
    if not _SHA256.fullmatch(expected):
        raise IdentityMigrationError(f"--expect-sha256 {expected!r} is not a sha256 hex digest")
    try:
        raw = (plan_dir / SUMS_FILENAME).read_bytes()
    except OSError as exc:
        raise IdentityMigrationError(f"{plan_dir}: no readable {SUMS_FILENAME}: {exc}") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise IdentityMigrationError(
            f"{plan_dir}/{SUMS_FILENAME} has sha256 {actual}, not the reviewed {expected}"
        )
    return raw


def _folded(part: str) -> str:
    """``part`` casefolded without Unicode format characters (HFS+ ignores them, CVE-2014-9390)."""
    return "".join(ch for ch in part if unicodedata.category(ch) != "Cf").casefold()


def require_safe_names(names: Iterable[str]) -> None:
    """Refuse an absolute path, a ``.``, ``..`` or empty part, a backslash, or a ``.git``
    part anywhere, in any case (default APFS is case-insensitive: ``.GIT`` is ``.git``)."""
    for name in names:
        parts = name.split("/")
        unsafe = {"", ".", ".."} & set(parts) or ".git" in {_folded(part) for part in parts}
        if name.startswith("/") or "\\" in name or unsafe:
            raise IdentityMigrationError(f"unsafe path {name!r}: refusing it")


def _escapes(root: Path, name: str) -> bool:
    """True when any existing part of ``name`` under ``root`` is a symlink.

    Every part is checked, not only the deepest that exists: a symlink part-way
    down leads elsewhere even when its target is inside the checkout (``.git``).
    """
    probe = root
    for part in PurePosixPath(name).parts:
        probe = probe / part
        if probe.is_symlink():
            return True
    return not probe.resolve().is_relative_to(root)


def require_confined(repo: Path, names: Iterable[str]) -> None:
    """Refuse any of ``names`` that is unsafe, or leads out of ``repo`` through a symlink."""
    listed = sorted(set(names))
    require_safe_names(listed)
    root = repo.resolve()
    escaping = [name for name in listed if _escapes(root, name)]
    if escaping:
        raise IdentityMigrationError(
            f"paths through a symlink or leading out of {repo}: {escaping[:5]}"
        )


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
