"""Stdio MCP server exposing the lovverk corpus to AI consumers.

Bundles four read-only tools over a local clone of the lovverk Markdown
corpus (produced by the lovspor sync engine). Each tool answers a class
of question an AI agent would naturally ask about Norwegian law:

    get_law(slug)              -> "Show me Skatteloven"
    get_law_history(slug)      -> "What changed in Skatteloven recently?"
    list_recent_changes(...)   -> "Which laws changed last week?"
    search_laws(query, ...)    -> "Are there laws about jernbane?"

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
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from lovspor.errors import LovsporError
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest

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

    return mcp


def serve(corpus_path: Path) -> None:
    """Start the stdio MCP server bound to ``corpus_path``.

    Blocks until the MCP client disconnects.
    """
    build_server(corpus_path).run()
