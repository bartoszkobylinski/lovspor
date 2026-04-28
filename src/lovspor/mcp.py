"""Stdio MCP server exposing the lovverk corpus to AI consumers.

Bundles six read-only tools over a local clone of the lovverk Markdown
corpus (produced by the lovspor sync engine). Each tool answers a class
of question an AI agent would naturally ask about Norwegian law:

    get_law(slug)              -> "Show me Skatteloven"
    get_law_history(slug)      -> "What changed in Skatteloven recently?"
    list_recent_changes(...)   -> "Which laws changed last week?"
    search_laws(query, ...)    -> "Are there laws about jernbane?" (metadata)
    search_body(query, ...)    -> "Which laws mention boligkjøpsmodeller?" (body text)
    corpus_status()            -> "Is my local corpus current?"

Data path: the server reads the corpus from disk via the supplied
``corpus_path``. It does not pull from GitHub or trigger an engine
sync. The lovspor scheduled workflow (see ``operations.md``) keeps
the corpus current; MCP consumers ``git pull`` their local clone (or
set up a cron) to pick up updates.

Transport: stdio only — the server is launched as a subprocess by the
MCP client (Claude Desktop, Claude Code, etc.) and communicates over
stdin/stdout. No network surface.

Why dataset aliases: legal text consumers think in Norwegian terms
(``lover``, ``forskrifter``), not in Lovdata's archive filenames
(``gjeldende-lover``, ``gjeldende-sentrale-forskrifter``). The tool
inputs accept either form and normalize internally.
"""

import json
import shlex
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from lovspor.errors import LovsporError
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest

_STALE_THRESHOLD_DAYS = 7
"""Manifest age beyond which corpus_status() flags the corpus as stale.

Chosen against the daily 04:00 UTC sync cadence: a 7-day-old manifest
means at least one full week of scheduled syncs failed to land in the
user's local clone (most likely they simply forgot to ``git pull``).
Adjustable later if production cadence changes — keep documented in
docs/mcp.md if changed."""

_GIT_HEAD_FIELDS = 3  # sha + ISO date + subject from the --format string

_SNIPPET_CONTEXT_CHARS = 50
"""Characters of context on each side of a body-search match in the
returned snippet. 50 chars on each side + match length ~= 100-130 chars
total, which fits a single AI message line and gives enough context
to judge relevance without overwhelming the response."""

_DATASET_ALIAS_TO_KEY = {
    "lover": "gjeldende-lover",
    "gjeldende-lover": "gjeldende-lover",
    "forskrifter": "gjeldende-sentrale-forskrifter",
    "gjeldende-sentrale-forskrifter": "gjeldende-sentrale-forskrifter",
}
_DATASET_KEY_TO_SUBDIR = {
    "gjeldende-lover": "lover",
    "gjeldende-sentrale-forskrifter": "forskrifter",
}


class CorpusNotFoundError(LovsporError):
    """Raised when the requested doc or corpus path is not present."""


class CorpusReader:
    """Read-only view of a local lovverk corpus clone.

    Holds the manifest in memory after the first load: it changes only
    when the user pulls new commits, and the MCP server process is
    short-lived (one launch per MCP client session).
    """

    def __init__(self, corpus_path: Path) -> None:
        if not corpus_path.exists():
            raise CorpusNotFoundError(
                f"corpus path does not exist: {corpus_path}",
            )
        manifest_path = corpus_path / "manifest.json"
        if not manifest_path.exists():
            raise CorpusNotFoundError(
                f"corpus path is missing manifest.json: {corpus_path}",
            )
        self.corpus_path = corpus_path
        self._manifest: Manifest | None = None
        # Body-text index for search_body; lazy-loaded on first call so
        # MCP server startup stays fast for clients that only query
        # metadata. ~45 MB resident once populated for the production
        # 4522-doc corpus — acceptable for a long-lived stdio process.
        self._body_index: dict[str, str] | None = None

    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            self._manifest = read_manifest(self.corpus_path / "manifest.json")
        return self._manifest

    def get_law(self, slug: str) -> str:
        """Return the rendered Markdown (frontmatter + body) for ``slug``."""
        record = self._find_current_by_slug(slug)
        path = self._safe_join(record.markdown_path)
        if not path.exists():
            raise CorpusNotFoundError(
                f"manifest references {record.markdown_path!r} but file is missing; "
                f"run 'git pull' in the corpus to refresh",
            )
        return path.read_text(encoding="utf-8")

    def get_law_history(self, slug: str) -> dict[str, Any]:
        """Return the parsed ``history/<slug>.json`` for ``slug``."""
        record = self._find_current_by_slug(slug)
        history_path = self._safe_join(
            _subdir_for_dataset(record.source_dataset),
            "history",
            f"{record.slug}.json",
        )
        if not history_path.exists():
            raise CorpusNotFoundError(
                f"history file missing for {slug!r}; corpus may predate the Sprint 5 history layer",
            )
        loaded: dict[str, Any] = json.loads(history_path.read_text(encoding="utf-8"))
        return loaded

    def list_recent_changes(
        self,
        dataset: str | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List current docs ordered by ``last_changed`` descending.

        ``dataset`` accepts ``lover`` / ``forskrifter`` (or the full
        Lovdata key). ``since`` is an ISO date filter (``YYYY-MM-DD``);
        invalid formats are rejected up-front rather than silently
        comparing as opaque strings. ``limit`` caps the result count
        and must be non-negative — Python slicing with a negative
        limit silently returns 'all but the last N', which is
        unambiguously not what an AI caller intends.
        """
        if limit < 0:
            raise ValueError(
                f"limit must be non-negative, got {limit}",
            )
        if since is not None:
            try:
                # Normalize, do not just validate: date.fromisoformat
                # accepts alternate ISO forms like '20260427' (no dashes)
                # which would then compare lexicographically WRONG against
                # the manifest's canonical 'YYYY-MM-DD' values (digits
                # sort before the '-' separator). Round-tripping through
                # date pins both inputs to the same canonical form.
                since = date.fromisoformat(since).isoformat()
            except ValueError as exc:
                raise ValueError(
                    f"since must be ISO date YYYY-MM-DD, got {since!r}",
                ) from exc
        dataset_key = _resolve_dataset(dataset) if dataset is not None else None
        rows: list[tuple[str, ManifestRecord]] = []
        for doc_id, record in self.manifest.documents.items():
            if record.status != "current":
                continue
            if dataset_key is not None and record.source_dataset != dataset_key:
                continue
            if record.last_changed is None:
                continue
            if since is not None and record.last_changed < since:
                continue
            rows.append((doc_id, record))
        rows.sort(key=lambda pair: pair[1].last_changed or "", reverse=True)
        return [_record_summary(doc_id, rec) for doc_id, rec in rows[:limit]]

    def search_laws(
        self,
        query: str,
        dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        """Substring-match ``query`` against slug + title (case-insensitive).

        Matches against manifest data only (slug, title) — no body
        text scan in this MVP. Body-text search would be a separate
        sprint with its own indexing strategy.
        """
        if not query.strip():
            return []
        needle = query.lower()
        dataset_key = _resolve_dataset(dataset) if dataset is not None else None
        results: list[dict[str, Any]] = []
        for doc_id, record in self.manifest.documents.items():
            if record.status != "current":
                continue
            if dataset_key is not None and record.source_dataset != dataset_key:
                continue
            haystack = f"{record.slug or ''} {record.title or ''}".lower()
            if needle in haystack:
                results.append(_record_summary(doc_id, record))
        return results

    def search_body(
        self,
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Substring-match ``query`` against the rendered Markdown body
        of every current doc (case-insensitive).

        Complement to ``search_laws``: that one matches manifest
        metadata only (slug + title); this one scans the full legal
        text. Returns slug, doc_id, title, dataset, ``match_count``
        (occurrences of the substring across the body), and a
        ``snippet`` (~100 char window around the FIRST match). Sorted
        by match_count descending, then by slug for stable ordering.

        ``limit`` caps the result count and must be non-negative.
        ``dataset`` accepts ``lover`` / ``forskrifter`` (or the full
        Lovdata key).

        The body index is loaded lazily on the first call (~45 MB
        resident for the production 4522-doc corpus, ~3-5 s cold
        load) so server startup stays fast for clients that only
        query metadata.
        """
        if limit < 0:
            raise ValueError(
                f"limit must be non-negative, got {limit}",
            )
        if not query.strip():
            return []
        needle = query.lower()
        dataset_key = _resolve_dataset(dataset) if dataset is not None else None
        index = self._load_body_index()
        results: list[dict[str, Any]] = []
        for doc_id, record in self.manifest.documents.items():
            if record.status != "current" or record.slug is None:
                continue
            if dataset_key is not None and record.source_dataset != dataset_key:
                continue
            body = index.get(record.slug)
            if body is None:
                continue
            haystack = body.lower()
            count = haystack.count(needle)
            if count == 0:
                continue
            first_match = haystack.find(needle)
            results.append(
                {
                    "slug": record.slug,
                    "doc_id": doc_id,
                    "title": record.title,
                    "dataset": _subdir_for_dataset(record.source_dataset),
                    "match_count": count,
                    "snippet": _snippet(body, first_match, len(query)),
                },
            )
        results.sort(key=lambda hit: (-hit["match_count"], hit["slug"] or ""))
        return results[:limit]

    def _load_body_index(self) -> dict[str, str]:
        """Lazy-build the slug -> body-text dict for ``search_body``.

        Reads every current doc's Markdown file once on first call,
        strips the YAML frontmatter and the leading H1 title (both are
        metadata already searchable via ``search_laws`` — see
        ``_strip_frontmatter_and_h1``), caches the dict for the rest
        of the server's lifetime. Files that fail the path-containment
        check or are missing on disk are silently skipped — the same
        defensive posture as ``get_law``.
        """
        if self._body_index is None:
            index: dict[str, str] = {}
            for record in self.manifest.documents.values():
                if record.status != "current" or record.slug is None:
                    continue
                try:
                    path = self._safe_join(record.markdown_path)
                except CorpusNotFoundError:
                    continue
                if not path.exists():
                    continue
                raw = path.read_text(encoding="utf-8")
                index[record.slug] = _strip_frontmatter_and_h1(raw)
            self._body_index = index
        return self._body_index

    def corpus_status(self) -> dict[str, Any]:
        """Return manifest + git HEAD freshness metadata.

        Designed to be called proactively when other tools return
        unexpectedly empty results (a stale corpus looks like a missing
        law) and as the answer to "is my corpus current?". The
        ``refresh_command`` field gives the user a copy-paste command;
        the server itself never mutates the corpus or fetches anything.
        """
        manifest = self.manifest
        generated_at = manifest.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        raw_age_days = (datetime.now(UTC) - generated_at).days
        # A negative raw age means the manifest is dated in the future:
        # either a forged manifest or (more likely) clock skew on the
        # user's machine. Clamp to 0 so age_days is always sensible,
        # surface a clock-skew notice instead of "Corpus is current
        # (-1 days old)" which reads as a tooling bug.
        is_clock_skew = raw_age_days < 0
        age_days = max(0, raw_age_days)
        current_docs = sum(
            1 for record in manifest.documents.values() if record.status == "current"
        )
        # Schema-staleness: pre-Sprint-4 manifests stored records without
        # a ``slug`` field. The MCP tools (search_laws, get_law,
        # get_law_history) all key off slug, so a manifest in that schema
        # silently breaks every tool while the date-based ``is_stale``
        # signal stays false (the manifest itself was written by an old
        # engine version, but its generated_at can be recent if the user
        # is on an older checkout). Detect this case explicitly so the
        # AI can quote a clear remediation instead of guessing.
        docs_without_slug = sum(
            1
            for record in manifest.documents.values()
            if record.status == "current" and record.slug is None
        )
        schema_compatible = docs_without_slug == 0
        is_age_stale = age_days > _STALE_THRESHOLD_DAYS
        is_stale = is_age_stale or not schema_compatible
        git_info = self._git_head_info()
        # shlex.quote handles paths containing spaces or shell metacharacters
        # (e.g. '/tmp/lovverk test' would otherwise parse as -C /tmp/lovverk
        # with 'test' as a positional arg, silently targeting the wrong path).
        refresh_command = f"git -C {shlex.quote(str(self.corpus_path))} pull"
        # Notice priority is intentional: schema-stale > clock-skew >
        # age-stale > fresh. Schema-staleness is the only signal that
        # makes the MCP tools unusable, so it must dominate. Clock-skew
        # alone says "treating as fresh" — that wording would directly
        # contradict is_stale=true if a future-dated manifest also had
        # the pre-Sprint-4 schema, so schema-stale has to win first.
        if not schema_compatible:
            notice = (
                f"Corpus manifest is on the pre-Sprint-4 schema "
                f"({docs_without_slug} of {current_docs} current documents have "
                f"no slug field). MCP search/get tools cannot operate on this "
                f"schema. Run: {refresh_command} to refresh."
            )
        elif is_clock_skew:
            notice = (
                f"Corpus manifest is dated in the future "
                f"({generated_at.isoformat()}); likely a clock-skew issue on "
                f"your machine. Treating as fresh."
            )
        elif is_age_stale:
            notice = f"Corpus manifest is {age_days} days old. Run: {refresh_command} to refresh."
        else:
            notice = f"Corpus is current ({age_days} days old)."
        return {
            "manifest_generated_at": generated_at.isoformat(),
            "manifest_age_days": age_days,
            "is_stale": is_stale,
            "schema_compatible": schema_compatible,
            "total_current_documents": current_docs,
            "head_commit": git_info["commit"],
            "head_commit_date": git_info["date"],
            "head_commit_subject": git_info["subject"],
            "refresh_command": refresh_command,
            "notice": notice,
        }

    def _git_head_info(self) -> dict[str, str | None]:
        """Read the current HEAD commit's sha / date / subject from
        the corpus's git directory. Returns ``None`` for each field
        when the corpus is not a git repository (or when ``git`` is
        unavailable on PATH) — the corpus_status() output stays
        well-shaped either way."""
        try:
            result = subprocess.run(
                [  # noqa: S607
                    "git",
                    "log",
                    "-1",
                    "--format=%H%n%aI%n%s",
                ],
                cwd=self.corpus_path,
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {"commit": None, "date": None, "subject": None}
        lines = result.stdout.strip("\n").split("\n", maxsplit=2)
        if len(lines) < _GIT_HEAD_FIELDS:
            return {"commit": None, "date": None, "subject": None}
        return {
            "commit": lines[0][:7],
            "date": lines[1].split("T", maxsplit=1)[0],
            "subject": lines[2],
        }

    def _find_current_by_slug(self, slug: str) -> ManifestRecord:
        for record in self.manifest.documents.values():
            if record.slug == slug and record.status == "current":
                return record
        raise CorpusNotFoundError(
            f"no current law with slug {slug!r}; "
            f"use search_laws or list_recent_changes to discover slugs",
        )

    def _safe_join(self, *parts: str) -> Path:
        """Join ``parts`` under ``corpus_path`` and refuse paths that escape it.

        Defense-in-depth against a malicious or buggy manifest that
        encodes ``markdown_path`` or ``slug`` containing ``..`` /
        absolute paths / symlinks pointing outside the corpus root.
        Within our own pipeline these fields are constructed from
        validated slug + dataset subdir, but the MCP server reads a
        manifest that the user clones from elsewhere — assume nothing.
        """
        target = self.corpus_path.joinpath(*parts).resolve()
        root = self.corpus_path.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CorpusNotFoundError(
                f"path {'/'.join(parts)!r} escapes corpus root",
            ) from exc
        return target


def _strip_frontmatter_and_h1(text: str) -> str:
    """Remove the YAML frontmatter block and the leading H1 title from
    a rendered lovspor Markdown file, leaving only the legal body.

    Lovspor's renderer always produces files in this exact shape:

        ---
        <YAML frontmatter>
        ---

        # <Title that duplicates the title: frontmatter field>

        ## <Chapter heading>
        ### § <Section heading>
        <paragraph text>

    The frontmatter and the H1 are metadata — both fields are already
    surfaced via ``search_laws`` (slug + title). ``search_body`` is
    contractually a *body* search, so frontmatter / H1 hits would be
    false positives that contradict its docstring. Strip them once at
    cache-load time so every subsequent scan operates on body text only.

    Tolerant of files that do not match the expected shape (e.g. a
    file without frontmatter or without an H1): return the input
    unchanged in that case rather than raising. The render pipeline
    is deterministic so production files always have the shape, but
    we still defend against malformed corpus content.
    """
    if text.startswith("---\n"):
        closing = text.find("\n---\n", 4)
        if closing > 0:
            text = text[closing + len("\n---\n") :]
    text = text.lstrip("\n")
    if text.startswith("# "):
        line_end = text.find("\n")
        if line_end > 0:
            text = text[line_end + 1 :]
    return text.lstrip("\n")


def _snippet(body: str, match_idx: int, match_len: int) -> str:
    """Extract a ``~100`` char window around ``match_idx`` in ``body``.

    Whitespace (including newlines) is collapsed to single spaces so
    the snippet renders as a single readable line in the AI's response.
    Adds leading ``...`` if not at the start, trailing ``...`` if not
    at the end, so the AI can see the snippet is a fragment.
    """
    start = max(0, match_idx - _SNIPPET_CONTEXT_CHARS)
    end = min(len(body), match_idx + match_len + _SNIPPET_CONTEXT_CHARS)
    snippet = " ".join(body[start:end].split())
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{snippet}{suffix}"


def _record_summary(doc_id: str, record: ManifestRecord) -> dict[str, Any]:
    """Public-facing summary of a manifest record (for AI consumers)."""
    return {
        "slug": record.slug,
        "doc_id": doc_id,
        "title": record.title,
        "dataset": _subdir_for_dataset(record.source_dataset),
        "last_changed": record.last_changed,
        "total_changes": record.total_changes,
    }


def _subdir_for_dataset(source_dataset: str) -> str:
    try:
        return _DATASET_KEY_TO_SUBDIR[source_dataset]
    except KeyError as exc:
        raise CorpusNotFoundError(
            f"unknown source_dataset in manifest: {source_dataset!r}",
        ) from exc


def _resolve_dataset(dataset: str) -> str:
    try:
        return _DATASET_ALIAS_TO_KEY[dataset]
    except KeyError as exc:
        raise CorpusNotFoundError(
            f"unknown dataset {dataset!r}; use one of: lover, forskrifter",
        ) from exc


def build_server(corpus_path: Path) -> FastMCP:
    """Build a FastMCP server bound to ``corpus_path``.

    The reader is constructed eagerly so configuration errors (missing
    corpus, missing manifest) surface at server startup rather than
    on the first tool invocation.
    """
    reader = CorpusReader(corpus_path)
    mcp = FastMCP("lovverk")

    @mcp.tool()
    def get_law(slug: str) -> str:
        """Return the full Markdown (frontmatter + body) of a Norwegian law or regulation.

        The slug is the human-readable kortform identifier (e.g.
        ``skatteloven``, ``opplaeringslova``, ``trafikkforskriften``).
        Use ``search_laws`` or ``list_recent_changes`` to discover
        valid slugs.

        Output begins with YAML frontmatter (id, title, ministry,
        dates, license, ...) followed by the legal text in Markdown.
        """
        return reader.get_law(slug)

    @mcp.tool()
    def get_law_history(slug: str) -> dict[str, Any]:
        """Return the per-act change history of a law as structured JSON.

        Each event has: date, commit hash, type (added | updated |
        renamed | removed), commit subject, optional from_path / to_path
        (for renames), optional lines_added / lines_removed. Newest
        event first.
        """
        return reader.get_law_history(slug)

    @mcp.tool()
    def list_recent_changes(
        dataset: str | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List current laws ordered by most recent change first.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` to filter
        by document type.
        ``since`` (optional): ISO date ``YYYY-MM-DD`` — only include
        laws whose last change is on or after this date.
        ``limit``: max results (default 20).
        """
        return reader.list_recent_changes(dataset=dataset, since=since, limit=limit)

    @mcp.tool()
    def search_laws(query: str, dataset: str | None = None) -> list[dict[str, Any]]:
        """Search the corpus for laws whose slug or title contains ``query``.

        Substring match, case-insensitive, against manifest metadata
        only (no body-text scan in this MVP). Returns slug, doc_id,
        title, dataset, last_changed, and total_changes for each hit.
        Use ``get_law(slug)`` to fetch the full text of any result.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` to filter.
        """
        return reader.search_laws(query, dataset=dataset)

    @mcp.tool()
    def search_body(
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search the corpus body text (full Markdown) for ``query``.

        Complement to ``search_laws``: that tool matches manifest
        metadata only (slug + title); this one scans the actual legal
        text. Use it when the user asks about a topic that may not
        appear in any law's title — e.g. "boligkjøpsmodeller",
        "kryptovaluta", "kunstig intelligens".

        Substring match, case-insensitive. Returns slug, doc_id,
        title, dataset, ``match_count`` (occurrences across the body),
        and a ``snippet`` (~100 char context window around the FIRST
        match). Sorted by match_count descending, then by slug.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` (or the
        full Lovdata key) to restrict the scan.
        ``limit``: max results (default 20). Must be non-negative.

        Performance note: the body index is loaded lazily on the first
        call (~3-5 s for the production 4522-doc corpus, ~45 MB
        resident); subsequent calls are O(N) substring scans (~100-
        200 ms typical).
        """
        return reader.search_body(query, dataset=dataset, limit=limit)

    @mcp.tool()
    def corpus_status() -> dict[str, Any]:
        """Return the current state of the local corpus + freshness metadata.

        Call this proactively when:
        - The user asks "is my corpus current?" or "when was the
          corpus last updated?".
        - Other tools (search_laws, list_recent_changes, get_law) return
          unexpectedly empty or "not found" results — a stale corpus
          can look indistinguishable from a missing law.

        Returns a dict with: ``manifest_generated_at`` (ISO datetime),
        ``manifest_age_days`` (int, clamped to 0 for future-dated
        manifests), ``is_stale`` (bool — true when EITHER the manifest
        is older than 7 days OR the schema is pre-Sprint-4),
        ``schema_compatible`` (bool — false when any current record
        has no slug field, meaning the manifest pre-dates Sprint 4 and
        the search/get tools cannot operate on it),
        ``total_current_documents``, ``head_commit`` (short SHA),
        ``head_commit_date`` (ISO date), ``head_commit_subject``,
        ``refresh_command`` (a copy-pasteable git command the user can
        run to refresh), and a human-readable ``notice`` summarizing
        the status (covers four cases: clock-skew, schema-stale,
        age-stale, fresh).

        The server itself never mutates the corpus or fetches anything —
        the user runs the suggested ``refresh_command`` manually.
        """
        return reader.corpus_status()

    return mcp


def serve(corpus_path: Path) -> None:
    """Start the stdio MCP server bound to ``corpus_path``.

    Blocks until the MCP client disconnects.
    """
    build_server(corpus_path).run()
