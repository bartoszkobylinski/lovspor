"""AR4a: the isolated, temporary release clone a preparation writes in (#738; §9.3 step 3).

The source (a local ``lovverk-forarbeider`` repository) is only **read**: Git
clones it with ``--no-local --no-hardlinks`` into a private work directory
that is neither inside nor around it, checks out the expected base commit
detached, and removes the clone's only remote. Nothing a preparation does can
then reach the source or any remote, and the clone shares no object file
with it. The source's ``main`` must be exactly the expected base commit; any
other answer refuses before anything is cloned.

An existing clone is reused only when it is still what this module made: its
own ``.git`` directory, no remote, ``HEAD`` detached at the base.

The clone's status is read with the untracked *and* ignored files listed, so a
``.gitignore`` in the base tree can hide nothing a preparation must account for.
"""

import os
import stat
from pathlib import Path

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import checked_directory
from lovspor.git_prepare import GitPrepareError, run_git

_STATUS = ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
_XY = 2


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CHECKOUT_UNVERIFIABLE, detail)


def _git(directory: Path, argv: tuple[str, ...]) -> bytes:
    """The output of a command that must succeed.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: <command>``.
    """
    try:
        answer = run_git(directory, argv)
    except GitPrepareError as error:
        raise _refused(argv[0]) from error
    if answer.code != 0:
        raise _refused(argv[0])
    return answer.out


def _commit_of(directory: Path, ref: str) -> str | None:
    try:
        answer = run_git(directory, ("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"))
    except GitPrepareError as error:
        raise _refused("rev-parse") from error
    if answer.code != 0:
        return None
    return answer.out.decode("ascii", "replace").strip()


def main_of(source: Path) -> str | None:
    """The commit the source's ``main`` names, ``None`` when it names none."""
    return _commit_of(source, "refs/heads/main")


def head_of(clone: Path) -> str | None:
    return _commit_of(clone, "HEAD")


def work_directory(raw: Path, source: Path) -> Path:
    """The private work directory, proved outside every repository and apart from the source.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path`` or ``checkout_forbidden``.
    """
    work = checked_directory(raw, (source,))
    resolved = source.resolve()
    if resolved == work or resolved.is_relative_to(work) or work.is_relative_to(resolved):
        raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
    return work


def make_clone(source: Path, clone: Path, base_commit: str) -> None:
    """Clone ``source`` to ``clone``, detached at ``base_commit``, with no remote.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable`` when any step fails.
    """
    argv = ("clone", "--no-local", "--no-hardlinks", "--no-checkout", "--template=", "--quiet")
    _git(clone.parent, (*argv, "--", str(source.resolve()), str(clone)))
    _git(clone, ("remote", "remove", "origin"))
    _git(clone, ("checkout", "--quiet", "--detach", base_commit))
    require_clone(clone, base_commit)


def require_clone(clone: Path, base_commit: str) -> None:
    """``clone`` is still what :func:`make_clone` made: own ``.git``, no remote, at the base.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: clone``.
    """
    try:
        status = os.lstat(clone / ".git")
    except OSError as error:
        raise _refused("clone") from error
    if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / ".git/objects/info/alternates"):
        raise _refused("clone")
    if _git(clone, ("remote",)).strip() or head_of(clone) != base_commit:
        raise _refused("clone")


def changed_paths(clone: Path) -> tuple[tuple[str, str], ...]:
    """Every entry Git reports changed, untracked or ignored, as ``(XY, path)``.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: status``.
    """
    out = _git(clone, _STATUS)
    found: list[tuple[str, str]] = []
    for record in out.split(b"\0"):
        if not record:
            continue
        try:
            text = record.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _refused("status") from error
        found.append((text[:_XY], text[_XY + 1 :]))
    return tuple(found)
