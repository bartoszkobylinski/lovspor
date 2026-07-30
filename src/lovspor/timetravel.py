"""Read past versions of corpus documents from the lovverk git history.

Sprint 10 PR-A (B1) Time-machine: given a slug and an ISO date, return
the document text as it was at the close of that day. The mechanism is
``git log --follow`` to walk the file's full lineage (including
predecessors across renames) and ``git show <sha>:<path>`` to read
the blob at the latest commit whose author date is ≤ the cutoff.

End-of-day semantics:
    target_date = 2026-04-15  ->  cutoff = 2026-04-15T23:59:59Z

This matches user intuition for "what did the law say on April 15?"
— the answer is the version that was current at the close of that
calendar day in UTC, which is the ``last_changed`` semantic the
manifest already uses.

Why filter the date in Python rather than via ``git log --before``:
``--follow`` works as a post-walk rename filter, so commits
``--before`` excludes are invisible to follow. If the cutoff falls
before a rename commit, follow loses the trail and reports "no
history for this file" even though earlier (pre-rename) commits
exist that are valid revisions of the doc. Walking the full follow
log and filtering author dates in-process avoids that interaction.

This module has no orchestrator or MCP dependency: ``get_law_at_revision``
only needs a path to a git repository and a path within it. The MCP
``CorpusReader`` wraps it with manifest lookup, future-date refusal,
and the user-facing ``CorpusNotFoundError`` translation.
"""

import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from lovspor.errors import LovsporError

_LOG_RECORD_SEP = "__COMMIT__"
"""Sentinel that separates per-commit blocks in the log output. Same
strategy as ``lovspor.history`` — ``%H``/``%aI``/``%s``/numstat lines
are not unambiguously delimited otherwise (subject lines may contain
anything, including SHAs)."""

_FOLLOW_BLOCK_MIN_LINES = 3  # SHA + ISO date + path. Fewer means a
# malformed block we should skip rather
# than mis-parse.


class RevisionNotFoundError(LovsporError):
    """No commit on or before the target date touches the requested path."""


class ShallowHistoryError(LovsporError):
    """The target date precedes this shallow checkout's locally available history.

    Deliberately distinct from :class:`RevisionNotFoundError`: a shallow clone
    cannot distinguish "the document entered the corpus later" from "this
    clone's history starts later", so callers must not translate this into a
    claim that the document did not exist in the corpus (ADR-0003 named
    defect: the clone boundary must never be conflated with corpus history).
    """


@dataclass(frozen=True)
class _RevisionEntry:
    sha: str
    commit_date: datetime
    path: str


@dataclass(frozen=True)
class RevisionResult:
    """A resolved past version: the blob text plus which commit produced it.

    The diff tool reports ``sha`` and ``commit_date`` so a caller can see that
    e.g. ``2026-03-01`` actually resolved to the January commit — the date the
    user asked for is rarely the date the version changed.
    """

    content: str
    sha: str
    commit_date: datetime


def resolve_law_at_revision(
    repo_path: Path,
    current_path: str,
    target_date: date,
) -> RevisionResult:
    """Resolve a doc to the latest commit on or before ``target_date``
    (following renames) and return its content plus that commit's identity.

    ``current_path`` is the doc's HEAD-relative path
    (e.g. ``"lover/skatteloven-sktl.md"``). ``--follow`` traces back
    through the Sprint-4 slug migration and any subsequent Lovdata
    kortform rename, so callers only ever pass today's path; the
    historical path used at the matched commit is resolved internally
    and fed to ``git show``.

    Raises :class:`RevisionNotFoundError` when no commit on or before
    ``target_date`` touches the path or any of its predecessors —
    e.g. the act first appeared in the corpus after ``target_date``.
    """
    cutoff = datetime.combine(target_date, time.max).replace(tzinfo=UTC)
    entry = _find_revision(repo_path, current_path, cutoff)
    content = _read_blob(repo_path, entry.sha, entry.path)
    return RevisionResult(content=content, sha=entry.sha, commit_date=entry.commit_date)


def get_law_at_revision(
    repo_path: Path,
    current_path: str,
    target_date: date,
) -> str:
    """Return only the file content as it was on ``target_date``.

    Thin wrapper over :func:`resolve_law_at_revision` for callers (``get_law_at``)
    that need the Markdown but not the resolved commit identity.
    """
    return resolve_law_at_revision(repo_path, current_path, target_date).content


def _find_revision(
    repo_path: Path,
    current_path: str,
    cutoff: datetime,
) -> _RevisionEntry:
    """Locate the newest follow-log entry whose author date ≤ ``cutoff``.

    Walks the full ``git log --follow`` for ``current_path`` (which
    is bounded — typically a few dozen commits per doc) and returns
    the first entry within the cutoff. Newest-first iteration order
    matches git's default log order; the first hit IS the answer.
    """
    entries = _iter_follow_log(repo_path, current_path)
    for entry in entries:
        if entry.commit_date <= cutoff:
            return entry
    raise _out_of_range_error(repo_path, current_path, cutoff, entries)


def _out_of_range_error(
    repo_path: Path,
    current_path: str,
    cutoff: datetime,
    entries: list[_RevisionEntry],
) -> LovsporError:
    """Classify an out-of-range date: genuinely pre-history vs shallow-truncated.

    On a shallow clone the oldest visible follow-log entry is the clone
    boundary, not the document's first corpus appearance — returning "not
    found" there would assert a corpus-history fact this checkout cannot
    know. The shallow check runs only on this failure path, so successful
    lookups pay nothing for it.
    """
    prefix = f"no commit on or before {cutoff.date().isoformat()} touches {current_path!r}"
    if _is_shallow_repository(repo_path):
        earliest = entries[-1].commit_date.date().isoformat() if entries else None
        reach = (
            f"locally available history for this path begins {earliest}"
            if earliest is not None
            else "no locally available history reaches this path"
        )
        return ShallowHistoryError(
            f"{prefix} in this shallow checkout; {reach}. The corpus may "
            f"contain earlier revisions that this clone cannot see — deepen "
            f"it with 'git fetch --unshallow' or re-fetch with "
            f"'lovspor fetch-corpus --full-history'.",
        )
    return RevisionNotFoundError(prefix)


def _is_shallow_repository(repo_path: Path) -> bool:
    """True when the checkout's git history is shallow (grafted boundary).

    Asks git itself (``rev-parse --is-shallow-repository``) rather than
    probing ``.git/shallow`` so worktrees and future git layouts keep
    working.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "true"


def _iter_follow_log(repo_path: Path, current_path: str) -> list[_RevisionEntry]:
    """Run ``git log --follow`` for ``current_path`` and yield one entry
    per commit (newest first). Each entry carries the SHA, the author
    date, and the path AT THAT COMMIT — which is the post-rename name
    in the commit's tree, the form ``git show`` needs.

    ``--name-only`` under ``--follow`` emits exactly one path per
    commit (the file's lineage post-rename name). The
    ``__COMMIT__``-delimited format is identical in shape to
    ``lovspor.history`` so the parser is straightforward.
    """
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            # core.quotePath=true (git's default) C-quotes non-ASCII bytes in
            # path output (æøå -> "\303\246"), which then breaks the `git show
            # <sha>:<path>` in _read_blob. ~2,000 corpus files have æøå slugs.
            "-c",
            "core.quotePath=false",
            "log",
            "--follow",
            f"--format={_LOG_RECORD_SEP}%n%H%n%aI",
            "--name-only",
            "--",
            current_path,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    entries: list[_RevisionEntry] = []
    for block in result.stdout.split(f"{_LOG_RECORD_SEP}\n")[1:]:
        lines = [line for line in block.split("\n") if line]
        if len(lines) < _FOLLOW_BLOCK_MIN_LINES:
            continue
        sha, iso, path = lines[0], lines[1], lines[2]
        entries.append(
            _RevisionEntry(
                sha=sha,
                commit_date=datetime.fromisoformat(iso),
                path=path,
            ),
        )
    return entries


def _read_blob(repo_path: Path, sha: str, path_at_commit: str) -> str:
    """Return ``git show <sha>:<path_at_commit>`` as text.

    The caller has just resolved ``(sha, path_at_commit)`` from
    ``git log --follow`` so the blob is guaranteed to exist at that
    revision; a non-zero exit here would indicate corpus corruption
    rather than a user error and is allowed to raise.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{sha}:{path_at_commit}"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout
