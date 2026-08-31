"""Resolve one immutable corpus state for transaction-time queries (ADR-0011).

``recorded_at`` is a state *selector*, not a state identity: a calendar
date resolves to the newest corpus commit authored at or before UTC
end-of-day, and that commit — reported as ``corpus_commit`` — is what a
bundle's members compare to prove they answered from one shared state
(ADR-0011 point 4). Contrast ``lovspor.timetravel``: that module walks one
document's ``--follow`` lineage and returns a *document revision*; the two
must never be conflated, because two documents resolved for the same date
legitimately carry two different document revisions while sharing one
global state.

``CorpusSnapshot`` is the read view of that resolved state. It reads blobs
with ``git show <sha>:<path>`` and the manifest of that commit, so every
lookup a primitive makes under ``recorded_at`` — slug index, body, section
ids — can come from the same state (ADR-0011 point 5, snapshot closure).
A snapshot is immutable, so its caches can never go stale; no epoch
machinery is needed here.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path

from lovspor.errors import LovsporError, ParseError
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.timetravel import ShallowHistoryError, _is_shallow_repository

_STATE_LOG_SEP = "__COMMIT__"
"""Block separator for the state log, same strategy as ``timetravel``."""


class HistoryBoundaryError(LovsporError):
    """The target date precedes the whole corpus history.

    ADR-0011 point 6 outcome 1 — the reconstruction boundary. Distinct
    from :class:`~lovspor.timetravel.ShallowHistoryError` (outcome 2, an
    operational limit of this clone) and from any per-document negative
    (outcome 3, a successful answer about a resolved state).
    """


@dataclass(frozen=True)
class CorpusStateRef:
    """One resolved global corpus state: the commit and its author date."""

    sha: str
    commit_date: datetime


def resolve_corpus_state(repo_path: Path, target_date: date) -> CorpusStateRef:
    """Resolve ``target_date`` to the newest corpus commit authored at or
    before UTC end-of-day — the whole history, no path filter.

    Raises :class:`HistoryBoundaryError` when the date precedes the corpus
    start, or :class:`ShallowHistoryError` when this clone's truncated
    history cannot answer (the two must never be conflated — ADR-0003).
    """
    cutoff = datetime.combine(target_date, time.max).replace(tzinfo=UTC)
    entries = _iter_state_log(repo_path)
    for entry in entries:
        if entry.commit_date <= cutoff:
            return entry
    raise _pre_history_error(repo_path, cutoff, entries)


def _pre_history_error(
    repo_path: Path,
    cutoff: datetime,
    entries: list[CorpusStateRef],
) -> LovsporError:
    """Classify an unanswerable date: reconstruction boundary vs shallow clone.

    Same split as ``timetravel._out_of_range_error``: a shallow clone's
    oldest visible commit is the clone boundary, not the corpus start, so
    claiming a boundary outcome from it would assert corpus history this
    checkout cannot know.
    """
    asked = cutoff.date().isoformat()
    earliest = entries[-1].commit_date.date().isoformat() if entries else None
    if _is_shallow_repository(repo_path):
        reach = (
            f"locally available history begins {earliest}"
            if earliest is not None
            else "no locally available history"
        )
        return ShallowHistoryError(
            f"no corpus state on or before {asked} in this shallow checkout; "
            f"{reach}. Deepen it with 'git fetch --unshallow' or re-fetch "
            f"with 'lovspor fetch-corpus --full-history'.",
        )
    start = earliest if earliest is not None else "unknown"
    return HistoryBoundaryError(
        f"no corpus state exists on or before {asked}: corpus history "
        f"begins {start}. Earlier states are not reconstructable from this "
        f"source (ADR-0009 §5).",
    )


def _iter_state_log(repo_path: Path) -> list[CorpusStateRef]:
    """Full corpus log, newest first: one ``CorpusStateRef`` per commit.

    Author dates (``%aI``) — the same axis ``timetravel`` filters on, so
    the global state resolution and the per-document lineage resolution
    agree about which side of a cutoff a commit falls on.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "log", f"--format={_STATE_LOG_SEP}%n%H%n%aI"],  # noqa: S607
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    entries: list[CorpusStateRef] = []
    for block in result.stdout.split(f"{_STATE_LOG_SEP}\n")[1:]:
        lines = [line for line in block.split("\n") if line]
        if len(lines) < 2:  # noqa: PLR2004 — SHA + ISO date
            continue
        entries.append(
            CorpusStateRef(sha=lines[0], commit_date=datetime.fromisoformat(lines[1])),
        )
    return entries


@dataclass
class CorpusSnapshot:
    """Read-only view of the corpus at one resolved commit.

    Immutable by construction: everything is read from the commit's tree,
    never from the working tree, so cached values stay valid for the
    snapshot's whole lifetime.
    """

    repo_path: Path
    sha: str
    _manifest: Manifest | None = field(default=None, init=False, repr=False)
    _slug_index: dict[str, tuple[str, ManifestRecord]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def read_text(self, rel_path: str) -> str | None:
        """Blob content of ``rel_path`` at this state, or ``None`` when the
        path does not exist in the commit's tree.

        Existence is probed with ``git cat-file -e`` first so an absent
        path — an expected historical negative — is distinguished from a
        real git failure, which still raises.
        """
        spec = f"{self.sha}:{rel_path}"
        probe = subprocess.run(  # noqa: S603
            ["git", "cat-file", "-e", spec],  # noqa: S607
            cwd=self.repo_path,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            return None
        result = subprocess.run(  # noqa: S603
            ["git", "-c", "core.quotePath=false", "show", spec],  # noqa: S607
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    @property
    def manifest(self) -> Manifest:
        """The manifest as committed in this state, parsed and validated."""
        if self._manifest is None:
            text = self.read_text("manifest.json")
            if text is None:
                raise ParseError(
                    f"corpus state {self.sha} carries no manifest.json; "
                    f"this state predates the manifest and cannot be served",
                )
            self._manifest = Manifest.model_validate(json.loads(text))
        return self._manifest

    @property
    def slug_index(self) -> dict[str, tuple[str, ManifestRecord]]:
        """``slug -> (doc_id, record)`` for this state's current records.

        First manifest entry wins on a duplicate slug — the same contract
        as the live reader's ``_load_slug_index``, so a historical answer
        and a current answer never disagree about which record a slug names
        for reasons other than the state itself.
        """
        if self._slug_index is None:
            index: dict[str, tuple[str, ManifestRecord]] = {}
            for doc_id, record in self.manifest.documents.items():
                if record.status != "current" or record.slug is None:
                    continue
                index.setdefault(record.slug, (doc_id, record))
            self._slug_index = index
        return self._slug_index
