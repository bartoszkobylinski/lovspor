"""Per-act change history extracted from git log.

For each document in `lovverk`, walk the git history of its file
(following renames) and produce a structured list of events. Source of
truth is JSON (`history/<slug>.json`); a human-readable Markdown view
(`history/<slug>.md`) is generated from the same data so MCP servers
and AI consumers can query JSON directly while researchers browse the
rendered MD on GitHub.

Event types correspond to the orchestrator's per-document commit policy
(see decisions.md §12a):

    added     first appearance of the doc in the corpus
    updated   xml content changed (xml_hash differed in manifest delta)
    renamed   slug changed (from→to recorded for cross-reference)
    removed   upstream dropped the doc; corpus tombstones it

Pre-Sprint-4 commits used bulk-mode subjects (``sync: N new, M changed,
…``) that don't tell us per-doc intent. For commits matching that
pattern we fall back to git's numstat — if line removals occurred the
event is ``updated``, otherwise ``added``. Initial seed commits
(``chore: initialize corpus repository`` etc.) are classified as
``added``.

The module has no orchestrator dependency: ``extract_history`` only
needs a path to a git repo and a path within it. PR-B will wire this
into the sync pipeline so history is regenerated for changed docs on
every sync, plus a one-time bulk migration for existing docs.
"""

import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from lovspor.rendering.frontmatter import serialize_frontmatter

HISTORY_SCHEMA_VERSION = 1

EventType = Literal["added", "updated", "renamed", "removed"]

_PER_DOC_SUBJECT = re.compile(
    r"^(?P<verb>add|update|rename|remove)\((?P<dataset>lov|forskrift)\): ",
)
_MIGRATION_RENAME_SUBJECT = re.compile(r"^migration: rename ")
_MIGRATION_SUBJECT = re.compile(r"^migration: ")
_BULK_SYNC_SUBJECT = re.compile(r"^sync: \d+ new, \d+ changed")
_BULK_SYNC_REMOVED_COUNT = re.compile(r"(?P<count>\d+) removed")
_BULK_SYNC_CHANGED_COUNT = re.compile(r"(?P<count>\d+) changed")
_RENAME_BRACE = re.compile(r"^(?P<prefix>.*)\{(?P<old>[^{}]*) => (?P<new>[^{}]*)\}(?P<suffix>.*)$")
_VERB_TO_TYPE: dict[str, EventType] = {
    "add": "added",
    "update": "updated",
    "rename": "renamed",
    "remove": "removed",
}

_MIN_COMMIT_BLOCK_LINES = 3  # sha + iso date + subject (numstat optional)
_MIN_NUMSTAT_PARTS = 2  # added + removed columns; path may be absent
_NUMSTAT_PATH_INDEX = 2  # third tab-separated column is the path
_EVENT_TITLES: dict[EventType, str] = {
    "added": "Added to corpus",
    "updated": "Content updated",
    "renamed": "Filename renamed",
    "removed": "Removed from corpus",
}


class HistoryEvent(BaseModel):
    """A single dated event in a document's history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    commit: str
    type: EventType
    subject: str
    from_path: str | None = None
    to_path: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None


class HistoryRecord(BaseModel):
    """Full per-document history. Newest events first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = HISTORY_SCHEMA_VERSION
    slug: str
    doc_id: str
    events: list[HistoryEvent]

    @field_validator("slug")
    @classmethod
    def _slug_is_safe_basename(cls, value: str) -> str:
        """Reject slugs that could escape the ``history/`` subdirectory.

        ``write_history`` concatenates ``slug`` directly into a filename,
        so any path separator, parent reference, or null byte would let
        a malicious or buggy upstream write outside the intended dir.
        Real slugs produced by ``lovspor.rendering.slug.derive_slug``
        only contain ``[a-z0-9æøåäöü-]`` so this only fires on
        adversarial input — but the writer must not assume goodwill.
        """
        if not value:
            raise ValueError("slug must not be empty")
        if "/" in value or "\\" in value:
            raise ValueError(f"slug must not contain path separators: {value!r}")
        if ".." in value:
            raise ValueError(f"slug must not contain parent references: {value!r}")
        if "\x00" in value:
            raise ValueError("slug must not contain null bytes")
        return value


def extract_history(
    repo_path: Path,
    current_path: str,
    doc_id: str,
    slug: str,
) -> HistoryRecord:
    """Walk ``git log --follow`` on ``current_path`` and build a history.

    ``current_path`` is the doc's path *as of HEAD* relative to the repo
    root (e.g. ``"lover/skatteloven.md"``). ``--follow`` traces back
    through renames so the returned list spans the full lifetime of the
    underlying document.
    """
    raw = _run_git_log(repo_path, current_path)
    return HistoryRecord(slug=slug, doc_id=doc_id, events=_parse_events(raw))


class _HistoryFrontMatter(BaseModel):
    """Minimal NLOD 2.0 attribution front matter for a ``history/<slug>.md``.

    The corpus contract requires every output Markdown file to carry NLOD
    attribution. No timestamp field, so regeneration stays byte-identical.
    """

    model_config = ConfigDict(frozen=True)

    type: str = "history"
    slug: str
    source_provider: str = "Lovdata"
    source_license: str = "NLOD 2.0"


def render_history_markdown(record: HistoryRecord) -> str:
    """Render ``HistoryRecord`` as human-readable Markdown.

    Output is deterministic: same record in -> byte-identical text out.
    Carries NLOD 2.0 attribution front matter like every corpus Markdown file.
    """
    front = serialize_frontmatter(_HistoryFrontMatter(slug=record.slug))
    lines = [f"# {record.slug} — Change history", ""]
    if not record.events:
        lines.append(f"_No history available; doc_id `{record.doc_id}`._")
        return front + "\n" + "\n".join(lines) + "\n"
    lines.append(f"_{len(record.events)} events; doc_id `{record.doc_id}`._")
    lines.append("")
    for event in record.events:
        lines.extend(_render_event(event))
        lines.append("")
    return front + "\n" + "\n".join(lines).rstrip() + "\n"


def write_history(record: HistoryRecord, dataset_dir: Path) -> tuple[Path, Path]:
    """Write ``history/<slug>.json`` and ``history/<slug>.md`` under
    ``dataset_dir``. Creates the directory if missing. Returns both paths."""
    history_dir = dataset_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    json_path = history_dir / f"{record.slug}.json"
    md_path = history_dir / f"{record.slug}.md"
    payload = json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )
    json_path.write_text(payload + "\n", encoding="utf-8")
    md_path.write_text(render_history_markdown(record), encoding="utf-8")
    return json_path, md_path


def _run_git_log(repo_path: Path, file_path: str) -> str:
    """Run ``git log --follow --numstat`` for ``file_path`` and return stdout."""
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            # core.quotePath=true (git's default) C-quotes non-ASCII bytes in
            # numstat rename rows (æøå -> "\303\246"), storing garbage paths
            # in the history record. ~2,000 corpus files have æøå slugs.
            "-c",
            "core.quotePath=false",
            "log",
            "--follow",
            "--numstat",
            "--format=__COMMIT__%n%H%n%aI%n%s",
            "--",
            file_path,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_events(raw: str) -> list[HistoryEvent]:
    """Parse the raw git log output (newest commit first) into events."""
    events: list[HistoryEvent] = []
    for block in raw.split("__COMMIT__\n")[1:]:
        lines = block.strip("\n").split("\n")
        if len(lines) < _MIN_COMMIT_BLOCK_LINES:
            continue
        sha, iso_date, subject = lines[0], lines[1], lines[2]
        stat_lines = [line for line in lines[3:] if line]
        event = _classify_commit(sha, iso_date, subject, stat_lines)
        if event is not None:
            events.append(event)
    return events


def _classify_commit(
    sha: str,
    iso_date: str,
    subject: str,
    stat_lines: list[str],
) -> HistoryEvent | None:
    """Map a single git-log block to a ``HistoryEvent``."""
    short_sha = sha[:7]
    # Convert the author-timezone %aI instant to a UTC calendar date so
    # history dates agree with the time-machine (get_law_at), which cuts off
    # by end-of-day UTC. A commit authored 00:30+02:00 is the PREVIOUS day in
    # UTC; recording the author-local date would disagree at the midnight
    # boundary. Bot syncs author in UTC (GitHub runners), so this is a no-op
    # for the production corpus — it only corrects manual/non-UTC commits.
    commit_date = datetime.fromisoformat(iso_date).astimezone(UTC).date()
    added, removed = _parse_line_stats(stat_lines)
    from_path, to_path = _parse_rename_paths(stat_lines)

    per_doc = _PER_DOC_SUBJECT.match(subject)
    if per_doc:
        event_type = _VERB_TO_TYPE[per_doc.group("verb")]
    elif _MIGRATION_RENAME_SUBJECT.match(subject):
        # Only the Sprint 4 slug migration ("migration: rename N documents
        # to slug-based filenames") is a genuine filename change.
        event_type = "renamed"
    elif from_path and to_path:
        # Numstat rename signal is unambiguous — for any commit whose
        # subject does not already encode a verb (per-doc / migration),
        # trust git's own rename detection. This catches bulk single-
        # mode renames ("sync: 0 new, 0 changed, 1 renamed, 0 removed")
        # which would otherwise fall through to the bulk-sync heuristic
        # and be misclassified as added.
        event_type = "renamed"
    elif _MIGRATION_SUBJECT.match(subject):
        # A non-rename migration re-renders the doc in place — a content
        # change, not a filename change. The Sprint 8 "migration: backfill
        # eu_basis for N documents" commit rewrites every doc's frontmatter;
        # classifying every "migration:" subject as renamed stamped a bogus
        # "Filename renamed" event on every backfilled doc's history.
        event_type = "updated"
    elif _BULK_SYNC_SUBJECT.match(subject):
        event_type = _classify_bulk_sync(subject, added, removed)
    else:
        event_type = "added"

    return HistoryEvent(
        date=commit_date,
        commit=short_sha,
        type=event_type,
        subject=subject,
        from_path=from_path if event_type == "renamed" else None,
        to_path=to_path if event_type == "renamed" else None,
        lines_added=added,
        lines_removed=removed,
    )


def _classify_bulk_sync(
    subject: str,
    added: int | None,
    removed: int | None,
) -> EventType:
    """Resolve event type for a pre-Sprint-4 bulk-sync commit.

    Subject form is ``sync: N new, M changed, [K renamed,] L removed``.
    For *this* file's numstat row we know its add/remove counts; we
    cross-reference with the subject to disambiguate.

    Limitations of the numstat-only signal: a file showing
    ``additions=0, removals>0`` could be either *deleted* in this
    commit or *shortened in place* (an update that only removed
    lines). To tell them apart we'd need ``git log --name-status``
    too, which is more parsing complexity than this codepath warrants
    (per-doc commits — the post-Sprint-4 default — never go through
    this branch). The heuristic therefore:

    - Classifies as ``removed`` only when the bulk subject claims
      removals AND the same commit had no updates (``M changed == 0``).
      This catches pure-delete bulk commits.
    - When the bulk subject claims BOTH updates AND removals in the
      same commit, we can't distinguish; default to ``updated`` (the
      more common case — line shrinkage in place rather than a delete).
      A mixed-mode pure-delete is therefore misclassified as updated;
      the misclassification is bounded to legacy bulk-mode history
      since no current commit type produces this ambiguity.
    """
    bulk_removed_match = _BULK_SYNC_REMOVED_COUNT.search(subject)
    bulk_removed_count = int(bulk_removed_match.group("count")) if bulk_removed_match else 0
    bulk_changed_match = _BULK_SYNC_CHANGED_COUNT.search(subject)
    bulk_changed_count = int(bulk_changed_match.group("count")) if bulk_changed_match else 0
    file_was_only_removed = (added is None or added == 0) and (removed is not None and removed > 0)
    if bulk_removed_count > 0 and bulk_changed_count == 0 and file_was_only_removed:
        return "removed"
    if removed is not None and removed > 0:
        return "updated"
    return "added"


def _parse_line_stats(stat_lines: list[str]) -> tuple[int | None, int | None]:
    """Sum ``+`` / ``-`` line counts across all numstat rows in this commit.

    Returns ``(None, None)`` if no numeric stats were found (binary files
    show as ``-`` / ``-`` in numstat, which we skip).
    """
    total_added = 0
    total_removed = 0
    found = False
    for line in stat_lines:
        parts = line.split("\t")
        if len(parts) >= _MIN_NUMSTAT_PARTS and parts[0].isdigit() and parts[1].isdigit():
            total_added += int(parts[0])
            total_removed += int(parts[1])
            found = True
    if not found:
        return None, None
    return total_added, total_removed


def _parse_rename_paths(stat_lines: list[str]) -> tuple[str | None, str | None]:
    """Extract from→to paths if any numstat row describes a rename.

    ``--numstat`` formats renames as ``<added>\\t<removed>\\t<from> => <to>``
    or ``<added>\\t<removed>\\t<prefix>{<old> => <new>}<suffix>`` when the
    common prefix can be factored out. Both forms are handled.
    """
    for line in stat_lines:
        parts = line.split("\t")
        if len(parts) <= _NUMSTAT_PATH_INDEX or " => " not in parts[_NUMSTAT_PATH_INDEX]:
            continue
        path_part = parts[_NUMSTAT_PATH_INDEX]
        brace = _RENAME_BRACE.match(path_part)
        if brace:
            prefix = brace.group("prefix")
            suffix = brace.group("suffix")
            return prefix + brace.group("old") + suffix, prefix + brace.group("new") + suffix
        old, _, new = path_part.partition(" => ")
        return old, new
    return None, None


def _render_event(event: HistoryEvent) -> list[str]:
    """Render one ``HistoryEvent`` as a list of Markdown lines."""
    title = _EVENT_TITLES[event.type]
    lines = [f"## {event.date.isoformat()} — {title}"]
    if event.from_path and event.to_path:
        lines.append(f"Renamed: `{event.from_path}` → `{event.to_path}`.")
    if event.lines_added is not None and event.lines_removed is not None:
        lines.append(f"Lines: +{event.lines_added} -{event.lines_removed}.")
    lines.append(f"Subject: `{event.subject}`")
    lines.append(f"Commit: `{event.commit}`.")
    return lines
