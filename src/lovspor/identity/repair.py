"""Attribute every commit on a reused path to the document it belongs to (#871).

The slug histories that name a non-owner cannot be repaired from what they
hold: the pre-#871 extractor overwrote the owner's events with the later
holder's and filed the owner's own deletion under the later holder
(``endr-i-utrullingsforskriften``: ``5511aa4``). Nor from a walk back from
HEAD: ``extract_lineage`` stops at the first other ``doc_id``, so a first owner
on a reused path gets an empty lineage. So each event is rebuilt here from the
original commit, path by path, and attributed by one rule:

- the front-matter ``id`` of the file at the commit (``id-at-commit``);
- else, for a deletion or an overwrite by a file with no ``id``, the ``id`` of
  the file at the commit's parent (``id-at-parent``).

A commit neither names is excluded and listed, never guessed. The path list is
explicit: the slug's path, and each path a file was moved from into it (git
rename detection at the commit that added it), followed back from that commit.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.history import HistoryEvent, classify_commit, frontmatter_id, parse_rename_paths
from lovspor.identity.corpus_git import (
    PathCommit,
    path_commits,
    read_revision_blobs,
    rename_row,
    topo_commits,
)

Rule = Literal["id-at-commit", "id-at-parent"]
NO_IDENTITY = "no front-matter id at the commit or at its parent"
NOT_AN_EVENT = "not a history event (exempt subject)"


class Segment(BaseModel):
    """A path walked back from ``tip``; a file left it for ``moved_to`` at ``moved_at``."""

    model_config = ConfigDict(frozen=True)

    path: str
    tip: str
    moved_to: str | None = None
    moved_at: str | None = None


class CommitFacts(BaseModel):
    """One commit on one path and the ``id`` of the file after it and before it."""

    model_config = ConfigDict(frozen=True)

    commit: str
    path: str
    before_doc: str | None
    after_doc: str | None


class TracedEvent(CommitFacts):
    """A commit's event and the document it belongs to, by ``rule``."""

    doc_id: str
    rule: Rule
    event: HistoryEvent


class ExcludedEvent(CommitFacts):
    """A commit on a traced path that no document receives, and why."""

    reason: str


class PathTrace(BaseModel):
    """Every commit on a slug's path chain: attributed newest first, or excluded."""

    model_config = ConfigDict(frozen=True)

    path: str
    segments: list[Segment]
    events: list[TracedEvent]
    excluded: list[ExcludedEvent]


class _Step(BaseModel):
    """One commit on one path with the ids of the file after and before it."""

    model_config = ConfigDict(frozen=True)

    commit: PathCommit
    path: str
    after: bytes | None
    before: bytes | None


def _doc(blob: bytes | None) -> str | None:
    return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))


def _steps(repo: Path, segment: Segment) -> list[_Step]:
    commits = path_commits(repo, segment.tip, segment.path)
    specs = [
        spec
        for c in commits
        for spec in (f"{c.sha}:{segment.path}", f"{c.parent or c.sha + '^'}:{segment.path}")
    ]
    blobs = read_revision_blobs(repo, specs)
    return [
        _Step(commit=c, path=segment.path, after=blobs[2 * i], before=blobs[2 * i + 1])
        for i, c in enumerate(commits)
    ]


def _moved_in(repo: Path, step: _Step) -> tuple[str, str] | None:
    """``(source path, numstat row)`` when ``step`` moved a file onto its path."""
    if step.after is None or step.before is not None:
        return None
    row = rename_row(repo, step.commit, step.path)
    if row is None:
        return None
    from_path, _ = parse_rename_paths([row])
    return (from_path, row) if from_path else None


def _outcome(step: _Step, stat_lines: list[str]) -> TracedEvent | ExcludedEvent:
    after, before = _doc(step.after), _doc(step.before)
    facts = CommitFacts(commit=step.commit.sha, path=step.path, before_doc=before, after_doc=after)
    doc_id = after or before
    if doc_id is None:
        return ExcludedEvent(**facts.model_dump(), reason=NO_IDENTITY)
    c = step.commit
    event = classify_commit(c.sha, c.date, c.subject, stat_lines)
    if event is None:
        return ExcludedEvent(**facts.model_dump(), reason=NOT_AN_EVENT)
    rule: Rule = "id-at-commit" if after else "id-at-parent"
    return TracedEvent(**facts.model_dump(), doc_id=doc_id, rule=rule, event=event)


def _walk_segment(
    repo: Path, segment: Segment
) -> tuple[list[TracedEvent | ExcludedEvent], list[Segment]]:
    """The outcome of every commit on ``segment`` and the segments files came from."""
    outcomes: list[TracedEvent | ExcludedEvent] = []
    sources: list[Segment] = []
    for step in _steps(repo, segment):
        moved = _moved_in(repo, step)
        stat_lines = step.commit.stat_lines
        if moved is not None and step.commit.parent is not None:
            stat_lines = [moved[1]]
            source = Segment(
                path=moved[0], tip=step.commit.parent, moved_to=step.path, moved_at=step.commit.sha
            )
            sources.append(source)
        outcomes.append(_outcome(step, stat_lines))
    return outcomes, sources


def trace_path(repo: Path, base: str, path: str) -> PathTrace:
    """Attribute every commit reachable from ``base`` on ``path`` and the paths moved into it."""
    segments = [Segment(path=path, tip=base)]
    outcomes: list[TracedEvent | ExcludedEvent] = []
    seen = {path}
    for segment in segments:
        found, sources = _walk_segment(repo, segment)
        outcomes += found
        fresh = [s for s in sources if s.path not in seen]
        seen |= {s.path for s in fresh}
        segments += fresh
    order = _topo_order(repo, base, segments)
    outcomes.sort(key=lambda o: order[o.commit])
    return PathTrace(
        path=path,
        segments=segments,
        events=[o for o in outcomes if isinstance(o, TracedEvent)],
        excluded=[o for o in outcomes if isinstance(o, ExcludedEvent)],
    )


def _topo_order(repo: Path, base: str, segments: list[Segment]) -> dict[str, int]:
    return {sha: i for i, sha in enumerate(topo_commits(repo, base, [s.path for s in segments]))}
