"""Which provision ids each corpus path has lost, from git history (#747).

A provision URL is retired when its pid leaves a document that keeps its
URL: the slug stays live, so the lineage walk of :mod:`redirects` — which
reads renames and deletions of whole files — never sees it, and without
this the old URL would fall through to a 404. The evidence is the
removed-line side of every first-parent diff of the pinned history: a
``### § N.`` heading line removed from a path proves that pid was in that
path's document at some commit.

Renames are deliberately not detected (``--no-renames``): a renamed file
then reads as a deletion of the old path, so every pid it carried is
attributed to the old path, whose URL namespace is exactly where those
pids were served. It also keeps the output independent of
``diff.renameLimit``, which would otherwise make the result depend on the
machine's git configuration.
"""

import subprocess
from pathlib import Path

from lovspor.headings import parse_section_heading
from lovspor.publish.inventory import body_lines, normalise_pid

_OLD_PATH = "--- a/"


_REMOVED_HEADINGS_LOG = (
    "log",
    "--first-parent",
    "--no-renames",
    "--diff-filter=DM",
    "-p",
    "-U0",
    "--no-color",
    "--no-ext-diff",
    "--format=",
)


def removed_pids_by_path(corpus: Path, sha: str) -> dict[str, frozenset[str]]:
    """``path -> pids whose heading was ever removed from it``, up to ``sha``."""
    log = _git_text(corpus, [*_REMOVED_HEADINGS_LOG, sha, "--", "lover", "forskrifter"])
    return parse_removed_pids(log)


def parse_removed_pids(stdout: str) -> dict[str, frozenset[str]]:
    """Parse ``git log -p -U0`` output into removed pids per old path.

    The parser tracks whether it is inside a hunk, because a removed body
    line that itself starts with ``-- a/`` reads as ``--- a/`` and must not
    be taken for a file header. Lines are split on ``\\n`` only: a
    ``str.splitlines`` split would also break inside corpus text at form
    feeds and Unicode line separators.
    """
    removed: dict[str, set[str]] = {}
    path: str | None = None
    in_hunk = False
    for line in stdout.split("\n"):
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif not in_hunk and line.startswith(_OLD_PATH):
            path = line[len(_OLD_PATH) :].rstrip("\t")
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and path is not None and line.startswith("-"):
            parsed = parse_section_heading(line[1:])
            if parsed is not None:
                removed.setdefault(path, set()).add(normalise_pid(parsed[0]))
    return {path: frozenset(pids) for path, pids in removed.items()}


def once_publishable_pids(corpus: Path, sha: str, path: str) -> frozenset[str]:
    """Pids of every version of ``path`` that had no duplicate pid.

    A document whose pids collide publishes no provision pages (ADR-0013
    Decision 1), so its provision URLs were servable only in a version
    without a collision. Called only for duplicate-pid documents and for
    paths the removed-heading scan names: it reads one blob per commit
    that touched the path.
    """
    commits = _git_text(
        corpus,
        ["log", "--first-parent", "--no-renames", "--format=%H", sha, "--", path],
    )
    pids: set[str] = set()
    for commit in commits.split():
        blob = _blob(corpus, f"{commit}:{path}")
        found = [normalise_pid(p[0]) for line in blob if (p := parse_section_heading(line))]
        if len(found) == len(set(found)):
            pids.update(found)
    return frozenset(pids)


def _git_text(corpus: Path, args: list[str]) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-c", "core.quotePath=false", *args],  # noqa: S607
        cwd=corpus,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _blob(corpus: Path, spec: str) -> list[str]:
    """Body lines of ``spec``, empty when the path is absent at that commit
    (the deleting commit names the path but holds no blob).

    No ``core.quotePath`` override: it changes how git prints path names,
    and ``git show <commit>:<path>`` prints only the blob's content."""
    result = subprocess.run(  # noqa: S603
        ["git", "show", spec],  # noqa: S607
        cwd=corpus,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    return body_lines(result.stdout) if result.returncode == 0 else []
