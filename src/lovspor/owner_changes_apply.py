"""Apply one pinned owner change to a lovverk clone, or refuse it.

The planned sync step (docs/proposals/owner-changes-sync-step.md) calls these
functions, through :mod:`lovspor.owner_changes_run`, for each pin in
``ops/lovverk-owner-changes``.

Every check runs local git only. ``plan`` refuses a pin before any write;
``merge_pin`` merges it and proves the merged change equals the pinned one,
undoing the merge otherwise; ``changed_since_pin`` is the run-end check that
the sync left every pinned file as pinned. Paths are compared without rename
detection everywhere, because a rename would let a merge move a pinned edit
onto a path the pin never named.
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lovspor.errors import LovsporError
from lovspor.owner_changes import Pin

SCOPE = "lokale-forskrifter/"
_MODES = frozenset({"100644", "000000"})


class PinRefusedError(LovsporError):
    """The pin is not applied. ``code`` names the rule; the run continues."""

    def __init__(self, pin: Pin, code: str, detail: str) -> None:
        super().__init__(f"lovverk#{pin.pull_request} {pin.commit}: {code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class Applied:
    """A pin merged in this run: the pin, its merge base and the merge commit."""

    pin: Pin
    base: str
    merge: str


def plan(repo: Path, pin: Pin, pr_head: str, attempt_used: bool) -> str | None:
    """The merge base to apply ``pin`` from, ``None`` when HEAD already holds it.

    ``pr_head`` is the commit ``refs/pull/<N>/head`` resolved to; ``attempt_used``
    says the attempt ref for (commit, attempt) already exists. Raises
    ``PinRefusedError`` on the first rule the pin breaks."""
    if git_ok(repo, "merge-base", "--is-ancestor", pin.commit, "HEAD"):
        return None
    if attempt_used:
        raise PinRefusedError(pin, "attempt-consumed", f"attempt {pin.attempt} was already tried")
    if pr_head != pin.commit:
        raise PinRefusedError(pin, "stale-pin", f"pull request head is {pr_head}")
    return _checked_base(repo, pin)


def _checked_base(repo: Path, pin: Pin) -> str:
    try:
        base = _git(repo, "merge-base", "HEAD", pin.commit)
    except LovsporError as exc:
        raise PinRefusedError(pin, "no-merge-base", "the pin shares no history with main") from exc
    if _git(repo, "rev-list", "--merges", f"{base}..{pin.commit}"):
        raise PinRefusedError(pin, "merge-commit", "the pinned range contains a merge commit")
    problems = scope_violations(repo, base, pin.commit)
    if problems:
        raise PinRefusedError(pin, "out-of-scope", "; ".join(problems))
    overlap = sorted(_names(repo, base, pin.commit) & _names(repo, base, "HEAD"))
    if overlap:
        raise PinRefusedError(pin, "overlap", f"also changed on main: {', '.join(overlap)}")
    return base


def scope_violations(repo: Path, base: str, tip: str) -> list[str]:
    """Each change in ``base..tip``, per commit and as a whole, that leaves the scope:
    a path outside ``lokale-forskrifter/``, a path component starting with a dot,
    or a mode other than a regular non-executable file."""
    commits = _git(repo, "rev-list", "--reverse", f"{base}..{tip}").split()
    ranges = [(f"{commit}^", commit) for commit in commits] + [(base, tip)]
    found: list[str] = []
    for old, new in ranges:
        for modes, path in _raw(repo, old, new):
            problem = _scope_problem(modes, path)
            if problem and problem not in found:
                found.append(problem)
    return found


def merge_pin(repo: Path, pin: Pin, base: str, message: str) -> Applied:
    """Merge ``pin`` into HEAD with ``message`` and prove the merge changed
    exactly what the pin changes; on a conflict or any difference, leave HEAD
    as it was and raise ``PinRefusedError``. A work tree with uncommitted
    changes is refused first, so undoing the merge cannot discard them."""
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise PinRefusedError(pin, "dirty", "the work tree has uncommitted changes")
    before = _git(repo, "rev-parse", "HEAD")
    if not git_ok(repo, "merge", "--no-ff", "--no-edit", "-m", message, pin.commit):
        code = "conflict" if _git(repo, "ls-files", "-u") else "merge-failed"
        git_ok(repo, "merge", "--abort")
        raise PinRefusedError(pin, code, "the merge did not apply cleanly")
    merged = _raw_text(repo, before, "HEAD")
    if merged != _raw_text(repo, base, pin.commit):
        _git(repo, "reset", "-q", "--hard", before)
        raise PinRefusedError(pin, "gate", "the merged change differs from the pinned change")
    return Applied(pin, base, _git(repo, "rev-parse", "HEAD"))


def changed_since_pin(repo: Path, applied: Sequence[Applied]) -> list[str]:
    """Paths of applied pins whose mode or blob at HEAD differs from the pin
    (present in one and absent in the other counts). Empty means intact."""
    changed = []
    for entry in applied:
        for path in sorted(_names(repo, entry.base, entry.pin.commit)):
            if _entry(repo, entry.pin.commit, path) != _entry(repo, "HEAD", path):
                changed.append(f"lovverk#{entry.pin.pull_request}: {path}")
    return changed


def _scope_problem(modes: tuple[str, str], path: str) -> str | None:
    if not path.startswith(SCOPE):
        return f"{path}: outside {SCOPE}"
    if any(part.startswith(".") for part in path.split("/")):
        return f"{path}: path component starts with a dot"
    if not set(modes) <= _MODES:
        return f"{path}: mode {modes[0]} -> {modes[1]} is not a regular file"
    return None


def _raw(repo: Path, old: str, new: str) -> list[tuple[tuple[str, str], str]]:
    out = git_bytes(repo, "diff", "--no-renames", "--no-abbrev", "--raw", "-z", old, new)
    fields = out.split(b"\0")
    entries = []
    for header, path in zip(fields[0:-1:2], fields[1::2], strict=True):
        old_mode, new_mode = header.decode().lstrip(":").split()[:2]
        entries.append(((old_mode, new_mode), path.decode("utf-8", "surrogateescape")))
    return entries


def _raw_text(repo: Path, old: str, new: str) -> bytes:
    return git_bytes(repo, "diff", "--no-renames", "--no-abbrev", "--raw", "-z", old, new)


def _names(repo: Path, old: str, new: str) -> set[str]:
    out = git_bytes(repo, "diff", "--no-renames", "--name-only", "-z", old, new)
    return {name.decode("utf-8", "surrogateescape") for name in out.split(b"\0") if name}


def _entry(repo: Path, commit: str, path: str) -> bytes:
    """``<mode> <type> <id>`` of ``path`` in ``commit``, empty when absent."""
    out = git_bytes(repo, "--literal-pathspecs", "ls-tree", "-z", commit, "--", path)
    return out.split(b"\t", 1)[0]


def _git(repo: Path, *args: str) -> str:
    return git_bytes(repo, *args).decode().strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    result = _run(repo, *args)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise LovsporError(f"git {args[0]} failed: {detail}")
    return result.stdout


def git_ok(repo: Path, *args: str) -> bool:
    return _run(repo, *args).returncode == 0


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    # Hooks off: the corpus clone's own hooks never run inside the sync job.
    # S603/S607: trusted git command, list args, no shell.
    return subprocess.run(  # noqa: S603
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
