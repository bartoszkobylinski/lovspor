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
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path, PurePosixPath
from typing import IO

from lovspor.errors import LovsporError, ParseError
from lovspor.slug_index import SlugIndex, build_slug_index
from lovspor.storage.manifest import Manifest
from lovspor.timetravel import ShallowHistoryError, _is_shallow_repository

_STATE_LOG_SEP = "__COMMIT__"
"""Block separator for the state log, same strategy as ``timetravel``."""

_ARCHIVE_SUFFIX = re.compile(r"\.[A-Za-z0-9]+\Z")
"""A suffix safe to splice into a glob pathspec: no glob metacharacters."""

_ARCHIVE_ROOT = "/lovspor-in-memory-archive"
"""The frame ``tarfile.data_filter`` judges member names against. Nothing
is written there, or anywhere: members are only read in memory."""


class HistoryBoundaryError(LovsporError):
    """The target date precedes the whole corpus history.

    ADR-0011 point 6 outcome 1 — the reconstruction boundary. Distinct
    from :class:`~lovspor.timetravel.ShallowHistoryError` (outcome 2, an
    operational limit of this clone) and from any per-document negative
    (outcome 3, a successful answer about a resolved state).
    """


class StateIntegrityError(LovsporError):
    """A resolved state contradicts itself: its manifest names a current
    document whose committed body cannot be read from the same commit.

    This is a corpus/storage failure (ADR-0011 point 6 outcome 2), not a
    historical fact — serving it as "the text was absent at that date"
    would turn repository damage into a statement about legal history.
    """


@dataclass(frozen=True)
class CorpusStateRef:
    """One resolved global corpus state: the commit and its author date."""

    sha: str
    commit_date: datetime


def resolve_corpus_state(repo_path: Path, target_date: date) -> CorpusStateRef:
    """Resolve ``target_date`` to one corpus commit: the first entry in
    ``git log`` order (reverse-chronological ancestry) whose author date
    is at or before UTC end-of-day — the whole history, no path filter.

    When author dates and ancestry order disagree (a commit authored
    earlier but committed later), ancestry order wins — deliberately the
    SAME convention as ``timetravel._find_revision``, so the global state
    resolution and the per-document lineage resolution can never pick
    opposite sides of such a divergence. Pinned by a real-git test.

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
    _slug_index: SlugIndex | None = field(default=None, init=False, repr=False)

    def read_text(self, rel_path: str) -> str | None:
        """Blob content of ``rel_path`` at this state, or ``None`` when the
        path does not exist in the commit's tree.

        ``None`` is a *historical* statement, so it is only returned once
        the commit itself is proven resolvable: a probe miss re-checks the
        commit (``<sha>^{commit}``) and lets an unresolvable state raise —
        an invalid commit, a truncated object database or repository
        corruption is an operational failure (ADR-0011 point 6 outcome 2)
        and must never read as "the path was absent at that date".
        """
        spec = f"{self.sha}:{rel_path}"
        probe = subprocess.run(  # noqa: S603
            ["git", "cat-file", "-e", spec],  # noqa: S607
            cwd=self.repo_path,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            subprocess.run(  # noqa: S603
                ["git", "cat-file", "-e", f"{self.sha}^{{commit}}"],  # noqa: S607
                cwd=self.repo_path,
                capture_output=True,
                check=True,
            )
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
    def slug_index(self) -> SlugIndex:
        """``slug -> (doc_id, record)`` for this state's current records.

        Built by ``lovspor.slug_index`` — the same builder as the live
        reader's ``_load_slug_index``, so a historical answer and a current
        answer never disagree about which record a slug names (or that a
        slug is ambiguous, issue #243) for reasons other than the state.
        """
        if self._slug_index is None:
            self._slug_index = build_slug_index(self.manifest.documents)
        return self._slug_index

    def iter_texts(self, paths: Collection[str]) -> Iterator[tuple[str, str]]:
        """``(path, text)`` for every one of ``paths`` present at this state,
        streamed from one ``git archive`` in archive order.

        One subprocess for any number of files, where ``read_text`` costs
        two per file. The tar is read as a stream (``r|``), never captured
        whole: on the production corpus the captured archive was ~500 MB
        of transient memory per historical search (issue #223). A wanted
        path absent from the tree is simply not yielded; judging that
        absence is the caller's business.
        """
        wanted = frozenset(paths)
        present = wanted & self._tree_paths() if wanted else wanted
        if not present:
            return
        command = ["git", "archive", "--format=tar", self.sha, "--", *_archive_pathspecs(present)]
        with (
            tempfile.TemporaryFile() as stderr,
            subprocess.Popen(  # noqa: S603
                command, cwd=self.repo_path, stdout=subprocess.PIPE, stderr=stderr
            ) as proc,
        ):
            try:
                yield from _stream_archive(proc, wanted, stderr)
            finally:
                if proc.poll() is None:
                    proc.kill()

    def _tree_paths(self) -> frozenset[str]:
        """Every file path in this state's tree.

        ``git archive`` refuses a pathspec that matches no file, so the
        narrowed archive may only name suffixes proven present; an invalid
        commit fails here, loudly, as it did for ``git archive`` itself.
        """
        listing = subprocess.run(  # noqa: S603
            ["git", "ls-tree", "-r", "-z", "--name-only", self.sha],  # noqa: S607
            cwd=self.repo_path,
            capture_output=True,
            check=True,
        )
        return frozenset(listing.stdout.decode("utf-8", "surrogateescape").split("\0")) - {""}


def _archive_pathspecs(present: frozenset[str]) -> list[str]:
    """Narrow the archive to the suffixes of the wanted paths present.

    The production tree carries embedding sidecars and JSON next to the
    Markdown; archiving only ``*.md`` cut the stream from ~496 MB to
    ~136 MB and git's own peak from ~529 MB to ~169 MB (lovverk
    ``2d3178c93``, 2026-09-26). A path without a plain suffix gets the
    whole tree (``[]``): reading more is safe, guessing a pattern is not.
    """
    suffixes = {PurePosixPath(path).suffix for path in present}
    if not all(_ARCHIVE_SUFFIX.fullmatch(suffix) for suffix in suffixes):
        return []
    return [f":(glob)**/*{suffix}" for suffix in sorted(suffixes)]


def _stream_archive(
    proc: "subprocess.Popen[bytes]",
    wanted: frozenset[str],
    stderr: IO[bytes],
) -> Iterator[tuple[str, str]]:
    """Read the wanted members off git's stdout, then prove git succeeded.

    A git failure truncates or empties the stream, which ``tarfile`` sees
    first — so a tar error is re-judged against git's exit status: a
    failed git is the operational error it always was, not a tar puzzle.
    """
    assert proc.stdout is not None  # noqa: S101 — stdout=PIPE above
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            yield from _read_wanted(tar, wanted)
    except tarfile.ReadError:
        _raise_if_git_failed(proc, stderr)
        raise
    proc.stdout.read()
    _raise_if_git_failed(proc, stderr)


def _raise_if_git_failed(proc: "subprocess.Popen[bytes]", stderr: IO[bytes]) -> None:
    returncode = proc.wait()
    if returncode != 0:
        stderr.seek(0)
        raise subprocess.CalledProcessError(returncode, proc.args, stderr=stderr.read())


def _read_wanted(tar: tarfile.TarFile, wanted: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Wanted regular files only, each passed through ``tarfile.data_filter``.

    A symlink or other non-regular member is skipped, never followed. The
    filter refuses a name escaping the tree (absolute, ``..``) — git cannot
    commit one, so a refusal means a damaged or forged stream.
    """
    for member in tar:
        if member.name not in wanted or not member.isfile():
            continue
        try:
            tarfile.data_filter(member, _ARCHIVE_ROOT)
        except tarfile.FilterError as exc:
            raise StateIntegrityError(
                f"archive member {member.name!r} refused by the tar data filter: {exc}",
            ) from exc
        blob = tar.extractfile(member)
        if blob is not None:
            yield member.name, blob.read().decode("utf-8")
