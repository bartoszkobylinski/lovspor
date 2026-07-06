"""Stdio MCP server exposing the lovverk corpus to AI consumers.

Bundles fifteen read-only tools over a local clone of the lovverk
Markdown corpus (produced by the lovspor sync engine). Each tool
answers a class of question an AI agent would naturally ask about
Norwegian law:

    get_law(slug)                       -> "Show me Skatteloven"
    get_law_at(slug, "2018-06-15")      -> "Show me Skatteloven as of 2018-06-15"
    list_law_versions(slug)             -> "When did Skatteloven change?"
    get_section(slug, "5-12")           -> "Show me just § 5-12 of Skatteloven"
    list_sections(slug)                 -> "Which sections does Skatteloven have?"
    get_law_history(slug)               -> "What changed in Skatteloven recently?"
    list_recent_changes(...)            -> "Which laws changed last week?"
    search_laws(query, ...)             -> "Are there laws about jernbane?" (metadata)
    search_body(query, ...)             -> "Which laws mention boligkjøpsmodeller?"
    semantic_search(query, ...)         -> "Which sections are about renter rights?"
    validate_citation(citation)         -> "Does '§ 5-12 skatteloven' actually exist?"
    verify_quote(slug, section, quote)  -> "Did this section actually say that?"
    get_eu_basis(slug)                  -> "Which EU dirs does Personopplysningsloven implement?"
    search_eu_implementations(celex)    -> "Which Norwegian laws implement GDPR?"
    corpus_status()                     -> "Is my local corpus current?"

Data path: the server reads the corpus from disk via the supplied
``corpus_path``. It does not pull from GitHub or trigger an engine
sync. The lovspor scheduled workflow (see ``operations.md``) keeps
the corpus current; MCP consumers ``git pull`` their local clone (or
set up a cron) to pick up updates.

Transport: stdio only — the server is launched as a subprocess by the
MCP client (Claude Desktop, Claude Code, etc.) and communicates over
stdin/stdout. No *inbound* network surface. The one outbound call is
``semantic_search`` embedding the user's query via the OpenAI API; every
other tool is filesystem-and-git only.

Why dataset aliases: legal text consumers think in Norwegian terms
(``lover``, ``forskrifter``), not in Lovdata's archive filenames
(``gjeldende-lover``, ``gjeldende-sentrale-forskrifter``). The tool
inputs accept either form and normalize internally.
"""

import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP

from lovspor.embeddings import (
    EmbeddingIndex,
    EmbeddingModel,
    OpenAIEmbedder,
    SearchHit,
    read_embeddings,
)
from lovspor.errors import LovsporError
from lovspor.settings import load_env
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest
from lovspor.timetravel import RevisionNotFoundError, get_law_at_revision

_STALE_THRESHOLD_DAYS = 7
"""Manifest age beyond which corpus_status() flags the corpus as stale.

Chosen against the daily 04:00 UTC sync cadence: a 7-day-old manifest
means at least one full week of scheduled syncs failed to land in the
user's local clone (most likely they simply forgot to ``git pull``).
Adjustable later if production cadence changes — keep documented in
docs/mcp.md if changed."""

_GIT_HEAD_FIELDS = 3  # sha + ISO date + subject from the --format string

_CITATION_SECTION_ID = re.compile(r"§\s*([\d-]+[a-z]?)")
"""Permissive matcher for the ``§ N-M`` part of a citation string.

Allows whitespace between ``§`` and the id; matches anywhere in the
input. Citations occur in many forms in real AI prompts —
``§ 5-12 skatteloven``, ``skatteloven § 5-12``, ``§ 5-12 i skatteloven``,
``§5-12``, etc. — so the parser doesn't pin a fixed order; it extracts
each component independently from wherever it appears."""

_SLUG_CHARACTER = re.compile(r"[a-z0-9æøåäöü-]")
"""Single character that could be part of a lovspor-rendered slug.

Used by ``_slug_token_in_citation`` to detect token boundaries: a
slug match is valid only when neither the character before nor the
character after the match is itself a slug character. Without this,
``record.slug in citation_lower`` accepts garbage like
``"skatteloven-sktlX"`` because the trailing X is appended to the
slug, contradicting the strict-match contract."""

_SECTION_HEADING = re.compile(r"^### § ([\d-]+[a-z]?)(?:\.\s+(.+?))?\s*$")
"""Matches a Norwegian-law section heading produced by the lovspor
renderer. Captures the section id (e.g. ``5-12``, ``1``, ``5-12a``)
and the optional section title (everything after the dot).

The title group is OPTIONAL because Lovdata's source XML sometimes
ships a ``legalArticleValue`` with no accompanying ``title`` field,
in which case the renderer emits a bare ``### § 5`` heading. We must
match those too — otherwise the section becomes invisible to
``get_section`` even though it exists in the rendered Markdown."""

_CHAPTER_HEADING = re.compile(r"^## (.+?)\s*$")
"""Matches a chapter heading (``## Kapittel N. Title``). Captured for
the ``parent_chapter`` field returned by ``get_section`` so the AI has
context for where in the act the section lives."""

_SUBSECTION_HEADING_PREFIX = "### "
"""Any ``### `` heading that does NOT match _SECTION_HEADING (e.g. a
plain subsection grouping like ``### Hvem som har skatteplikt``) acts
as a boundary that closes the current section without starting a new
one — same boundary semantics as the next ``### §`` or ``## ``."""

_SNIPPET_CONTEXT_CHARS = 50
"""Characters of context on each side of a body-search match in the
returned snippet. 50 chars on each side + match length ~= 100-130 chars
total, which fits a single AI message line and gives enough context
to judge relevance without overwhelming the response."""

_SEMANTIC_MIN_SCORE_DEFAULT = 0.25
"""Default similarity floor for ``semantic_search``.

Hits below this score are noise for the production embedding model
(text-embedding-3-large on Norwegian legal text): off-topic sections
sit around 0.1-0.2 while same-language matches score > 0.4.
The floor is deliberately below the same-language band because
cross-lingual queries score systematically lower — the eval suite's
English lay-vocabulary query against personopplysningsloven scores
~0.31 for the correct section. Returning 20 paste-ready citation
hints for an off-corpus query invites the AI to cite the least-bad
one — filtering plus an explicit 'no strong match' notice is the
anti-hallucination posture. Callers can pass ``min_score=0.0`` to
see everything."""

_MAX_RESULT_LIMIT = 100
"""Hard cap on any list-returning tool's ``limit``. A request above this is
clamped (not rejected), so one call cannot amplify into a corpus-wide scan
or return; the per-tool default stays 20."""


def _bounded_limit(limit: int) -> int:
    """Validate and clamp a tool's ``limit``.

    Rejects negatives — Python slicing with a negative limit silently
    returns 'all but the last N', which an AI caller never intends — and
    caps large values at :data:`_MAX_RESULT_LIMIT`.
    """
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    return min(limit, _MAX_RESULT_LIMIT)


_SEMANTIC_SNIPPET_CHARS = 200
"""Max characters of section-body lead included with each
``semantic_search`` hit. Enough to judge relevance and ground the
hit in real corpus text; the full section still comes from
``get_section``."""

_MIN_SUGGESTION_TOKEN_CHARS = 4
"""Citation tokens shorter than this never feed slug suggestions —
Norwegian filler words (``i``, ``jf``, ``og``, ``av``) are slug-shaped
but would only produce noise matches."""

_CROSS_REF_SECTION = re.compile(r"§\s*(\d+(?:-[\da-z]+)?)")
"""Detect ``§ N-M`` (or just ``§ N``) section references inside a
section body for cross-reference extraction. Captures the bare
section_id (no ``§`` prefix). Permissive about whitespace between
``§`` and the id, covering all section_id shapes the renderer
produces: plain integer, chapter-section, trailing letter."""

_CROSS_REF_CONTEXT_CHARS = 80
"""Characters of context on each side of a ``§`` match to scan for
slug tokens during cross-reference resolution. 80 chars typically
captures a cue word (``i``, ``jf.``, ``iht.``) plus the slug
token; longer windows risk picking up an unrelated slug from the
previous sentence and false-positively classifying a same-act
ref as cross-act."""

_SLUG_TOKEN_PATTERN = re.compile(r"[a-z0-9æøåäöü-]+")
"""Tokenize text by sequences of slug-class characters.

Used by ``_resolve_slug_in_window`` for fast cross-act resolution:
extract every slug-shaped token from the window once, then look
each up against the current-slug set in O(1). Cheaper than
walking 4500 manifest slugs per ``§`` match and running
``_slug_token_in_citation`` on each."""

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

    Holds the manifest and its derived indices in memory after the first
    load. The server process is long-lived (one launch per MCP client
    session), so a ``git pull`` against the corpus can land underneath it;
    ``_refresh_if_stale`` drops every cache when manifest.json changes on
    disk so tools never serve the pre-pull corpus (see its docstring).
    """

    def __init__(
        self,
        corpus_path: Path,
        embedder: EmbeddingModel | None = None,
    ) -> None:
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
        self._embedder = embedder
        # Pin the expected embedding dimension at construction time so
        # _load_embedding_index can drop .bin files written by an older
        # model (different dim). Without this filter top_k_cosine would
        # raise ``shapes not aligned`` deep inside numpy when the query
        # vector and a stale .bin disagree on dim, taking the whole
        # search down for one orphan file from a prior model migration.
        self._expected_dim: int | None = embedder.get_dimension() if embedder else None
        self._manifest: Manifest | None = None
        # Body-text index for search_body; lazy-loaded on first call so
        # MCP server startup stays fast for clients that only query
        # metadata. ~45 MB resident once populated for the production
        # 4522-doc corpus — acceptable for a long-lived stdio process.
        self._body_index: dict[str, str] | None = None
        # Per-section int8 embedding index for semantic_search; lazy-
        # loaded on first call. ~200 MB resident for the production
        # corpus at 3072-dim int8 (one contiguous matrix inside
        # EmbeddingIndex). Kept separate from _body_index because the
        # two indices have very different load profiles (binary parse
        # vs. text strip) and most consumers will use only one of them.
        self._embedding_index: EmbeddingIndex | None = None
        # Count of .bin files dropped due to dim mismatch during the
        # last index build. Surfaced in semantic_search error messages
        # so an all-stale corpus (post-model-migration state) gets a
        # clearer "needs re-embedding" hint rather than the generic
        # "no embeddings found" message that fits an empty-corpus
        # bootstrap state instead.
        self._stale_bin_count: int = 0
        # Per-doc body cache for point lookups (get_section,
        # verify_quote). Filled one doc at a time so a single
        # get_section never pays the corpus-wide load that
        # search_body's _body_index needs (~3-5 s, ~45 MB for the
        # production corpus). When the full index IS already loaded,
        # _body_for_record reads from it instead of this cache.
        self._doc_bodies: dict[str, str] = {}
        # ``slug -> {section_ids}`` cache for cross-reference
        # validation in get_section. Filled per target slug on
        # demand — only acts actually referenced by a ``§`` pattern
        # are parsed, not the whole corpus.
        self._section_ids_cache: dict[str, set[str]] = {}
        # ``slug -> (doc_id, record)`` for O(1) point lookups
        # (get_law, get_section, get_eu_basis, ...). Built once from
        # the cached manifest — every per-call linear scan over ~4500
        # records was pure waste for the most common tool calls.
        self._slug_index: dict[str, tuple[str, ManifestRecord]] | None = None
        # mtime of manifest.json as of the last cache load. Guards every
        # cache against a ``git pull`` landing underneath the long-lived
        # server (see _refresh_if_stale). None until the first read.
        self._manifest_mtime_ns: int | None = None

    def _refresh_if_stale(self) -> None:
        """Drop all in-memory caches when ``manifest.json`` changed on disk.

        The server is long-lived (one stdio session) but the user can
        ``git pull`` the corpus underneath it. Every sync rewrites
        manifest.json with a fresh ``generated_at``, so its mtime is a
        cheap, reliable change signal: when it moves, the cached manifest
        and every derived index (slug / body / embeddings) describe the
        pre-pull corpus and must be rebuilt. Without this, ``corpus_status``
        reports a freshly-pulled git HEAD beside a stale manifest age, and
        the search tools keep serving the old corpus.
        """
        try:
            mtime = (self.corpus_path / "manifest.json").stat().st_mtime_ns
        except OSError:
            return  # manifest vanished mid-session; the next read raises clearly
        if mtime == self._manifest_mtime_ns:
            return
        self._manifest_mtime_ns = mtime
        self._manifest = None
        self._slug_index = None
        self._body_index = None
        self._embedding_index = None
        self._stale_bin_count = 0
        self._doc_bodies = {}
        self._section_ids_cache = {}

    @property
    def manifest(self) -> Manifest:
        self._refresh_if_stale()
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

    def get_section(self, slug: str, section_id: str) -> dict[str, Any]:
        """Return a single ``§`` section of an act.

        ``section_id`` is the bare numeric / hyphenated identifier
        (``"5-12"`` or ``"1"``). The obvious AI-written variants —
        leading ``§``, trailing dot, surrounding whitespace — are
        normalized away rather than costing an error round trip; the
        response always carries the canonical bare id.

        The section body runs from the heading line to the next
        ``###`` or ``##`` heading. ``parent_chapter`` carries the
        most recent ``## Kapittel N. ...`` heading so the AI has
        structural context.

        ``cross_references`` lists every ``§ N-M`` pattern detected
        in the body, deduplicated by target, with each entry already
        validated against the manifest. Each entry is
        ``{text, target_slug, target_section_id, valid, reason}``;
        ``target_slug`` defaults to the current act when no other
        slug appears within ``±_CROSS_REF_CONTEXT_CHARS`` of the
        match. The field is empty when the body has no ``§``
        patterns. See ``_extract_cross_references`` for the parser
        contract and its deliberate MVP limitations.

        Raises ``CorpusNotFoundError`` if the slug is unknown OR the
        section is absent — the error message lists the act's
        available section ids in natural order so the AI can recover
        without a separate get_law call.

        Reads only this act's Markdown file (plus the files of acts
        its cross-references point at) — never the corpus-wide body
        index that ``search_body`` needs.
        """
        section_id = _normalize_section_id(section_id)
        record = self._find_current_by_slug(slug)
        # _find_current_by_slug only returns records whose slug == the
        # query slug, so record.slug is non-None here even though the
        # type annotation allows None.
        body = self._body_for_record(record)
        sections = _parse_sections(body)
        if section_id not in sections:
            available = ", ".join(
                f"§ {sid}" for sid in sorted(sections.keys(), key=_natural_section_key)
            )
            raise CorpusNotFoundError(
                f"section {section_id!r} not found in {slug!r}; "
                f"available: {available or '(no sections in this act)'}",
            )
        section = sections[section_id]
        # The act's own section-id set is a free by-product of the
        # parse above — seed the cache so same-act cross-refs don't
        # re-parse the body.
        self._section_ids_cache.setdefault(record.slug or "", set(sections.keys()))
        # Skip cross-ref resolution for sections with no ``§``
        # patterns at all — most short sections have none.
        cross_references: list[dict[str, Any]] = []
        if _CROSS_REF_SECTION.search(section["body"]):
            cross_references = _extract_cross_references(
                section["body"],
                record.slug or "",
                set(self._load_slug_index()),
                self._section_ids_for,
            )
        return {
            "slug": record.slug,
            "section_id": section_id,
            "heading": section["heading"],
            "parent_chapter": section["parent_chapter"],
            "body": section["body"],
            "cross_references": cross_references,
        }

    def list_sections(self, slug: str) -> list[dict[str, str]]:
        """Table of contents for one act, in document order.

        One row per ``§`` section: ``{section_id, heading,
        parent_chapter}``. The cheap navigation companion to
        ``get_section`` — an AI that doesn't know the exact section
        id can fetch the TOC instead of pulling the whole act
        through ``get_law`` (hundreds of KB for the big codes).

        Empty list for an act with no ``§`` sections. Unknown slug
        raises with the usual recovery hints.

        Reads only this act's Markdown file; the parsed section-id
        set is seeded into the cross-reference cache as a free
        by-product.
        """
        record = self._find_current_by_slug(slug)
        sections = _parse_sections(self._body_for_record(record))
        self._section_ids_cache.setdefault(record.slug or "", set(sections.keys()))
        return [
            {
                "section_id": section_id,
                "heading": data["heading"],
                "parent_chapter": data["parent_chapter"],
            }
            for section_id, data in sections.items()
        ]

    def validate_citation(self, citation: str) -> dict[str, Any]:
        """Verify that a citation string actually resolves in the corpus.

        Permissive parser: extracts ``§ N-M`` and slug components from
        anywhere in the input — citation order is not pinned because
        AI prompts produce many forms (``§ 5-12 skatteloven``,
        ``skatteloven § 5-12``, ``§ 5-12 i skatteloven``, etc.).

        Slug match is strict: the cited slug must equal a known
        manifest slug (case-insensitive substring on the slug field).
        ``"skatteloven"`` will not match production slug
        ``"skatteloven-sktl"`` — return ``valid: false`` with a
        descriptive ``reason`` instead. Strict because the slug is the
        authoritative id and AI consumers should be using results from
        ``search_laws`` (which returns canonical slugs) rather than
        guessing.

        Returns ``{valid, slug, section_id, heading, reason}``. ``valid``
        is true only when both the slug and (if present) the section
        resolve. Slug-only citations are valid as long as the slug is
        known. ``§``-only citations are ambiguous (many acts have
        ``§ 5-12``) — flagged invalid with a reason. Unparseable
        citations likewise return invalid + reason.
        """
        section_match = _CITATION_SECTION_ID.search(citation)
        section_id = section_match.group(1) if section_match else None

        # Find slug-shaped TOKEN in citation (token = word-boundaried
        # substring; not a substring inside a longer alphanumeric run).
        # Plain ``record.slug in citation_lower`` is too lax: 'skatteloven-
        # sktl' would match inside 'skatteloven-sktlX', contradicting the
        # strict-match contract. _slug_token_in_citation rejects that.
        # Pick the LONGEST among token-matching candidates so canonical
        # 'skatteloven-sktl' wins over a hypothetical shorter slug that
        # happens to also be a separate token in the same citation.
        citation_lower = citation.lower()
        candidates = [
            slug
            for slug in self._load_slug_index()
            if _slug_token_in_citation(slug, citation_lower)
        ]
        matched_slug = max(candidates, key=len) if candidates else None

        if matched_slug is None and section_id is None:
            return {
                "valid": False,
                "slug": None,
                "section_id": None,
                "heading": None,
                "reason": (
                    f"could not parse citation {citation!r}: no § id and no known slug found"
                    f"{self._citation_suggestion_hint(citation_lower)}"
                ),
            }

        if matched_slug is None:
            return {
                "valid": False,
                "slug": None,
                "section_id": section_id,
                "heading": None,
                "reason": (
                    f"ambiguous citation: § {section_id} found but no act "
                    f"identifier; many acts have a section by that id"
                    f"{self._citation_suggestion_hint(citation_lower)}"
                ),
            }

        if section_id is None:
            # Slug-only citation: valid if the slug is known (it is —
            # we matched it from the manifest).
            return {
                "valid": True,
                "slug": matched_slug,
                "section_id": None,
                "heading": None,
                "reason": None,
            }

        # Both slug and section_id present — delegate to get_section
        # for the section-existence check. Reuses its body-index cache
        # and natural-order error message.
        try:
            section = self.get_section(matched_slug, section_id)
        except CorpusNotFoundError as exc:
            return {
                "valid": False,
                "slug": matched_slug,
                "section_id": section_id,
                "heading": None,
                "reason": str(exc),
            }
        return {
            "valid": True,
            "slug": matched_slug,
            "section_id": section_id,
            "heading": section["heading"],
            "reason": None,
        }

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

    def get_law_at(self, slug: str, target_date: str) -> str:
        """Return the rendered Markdown of ``slug`` as it stood on ``target_date``.

        ``target_date`` is an ISO ``YYYY-MM-DD`` string; end-of-day
        UTC semantics ("close of business on that day"). Future dates
        are refused — a calendar date past today's UTC date is almost
        always a typo, and silently aliasing it to HEAD would mask the
        mistake. Past dates that predate the act's first appearance
        in the corpus raise with a hint pointing to ``get_law_history``
        so the AI can recover.

        Walks ``git log --follow`` on the manifest's current
        ``markdown_path`` to find the latest commit ≤ end-of-day on
        ``target_date`` that touched the file (or its predecessor
        through the Sprint-4 slug-rename migration), then ``git show``s
        the blob at that revision. The Markdown returned starts with
        the YAML frontmatter as it was at that revision (``retrieved_at``,
        ``xml_hash``, ``eu_basis``-or-absent, etc.), so consumers can
        distinguish "rendered from the same XML" vs "an actual content
        update" without re-deriving it from the body.
        """
        try:
            target = date.fromisoformat(target_date)
        except ValueError as exc:
            raise ValueError(
                f"target_date must be ISO date YYYY-MM-DD, got {target_date!r}",
            ) from exc
        today = datetime.now(UTC).date()
        if target > today:
            raise ValueError(
                f"target_date {target.isoformat()} is in the future "
                f"(today is {today.isoformat()}); "
                f"use get_law for the current version",
            )
        record = self._find_current_by_slug(slug)
        try:
            return get_law_at_revision(
                self.corpus_path,
                self._safe_relative(record.markdown_path),
                target,
            )
        except RevisionNotFoundError as exc:
            raise CorpusNotFoundError(
                f"law {slug!r} did not exist in the corpus on {target.isoformat()}; "
                f"call get_law_history({slug!r}) to see when it first appeared",
            ) from exc

    @staticmethod
    def _history_event_to_version(event: dict[str, Any]) -> dict[str, Any]:
        """Project a history event into a ``list_law_versions`` entry."""
        return {
            "date": event["date"],
            "commit": event["commit"],
            "type": event["type"],
            "lines_added": event.get("lines_added"),
            "lines_removed": event.get("lines_removed"),
        }

    def list_law_versions(self, slug: str) -> list[dict[str, Any]]:
        """List the dates on which ``slug`` had distinct content versions.

        Reads the same ``history/<slug>.json`` that powers
        ``get_law_history`` and filters to events whose ``type`` is
        ``added`` or ``updated`` — the only event types that produce
        a different ``get_law_at`` result. Pure renames (filename
        slug change with identical XML) are skipped: they don't
        change the content so they don't add a "version" from the
        time-machine consumer's point of view.

        When two or more content changes land on the same UTC date,
        only the latest is listed. ``get_law_at`` resolves a date with
        end-of-day semantics, so an earlier same-day version is not
        reachable through the date interface; listing it would imply a
        precision the tool does not offer. ``get_law_history`` still
        carries the complete intra-day event trail for audit use.

        Returns events oldest-first (chronological reading order), the
        opposite of ``get_law_history`` which is newest-first
        (audit-trail order). Each entry: ``date`` (ISO YYYY-MM-DD —
        feed straight into ``get_law_at``), ``commit`` (short SHA),
        ``type`` (``added`` | ``updated``), ``lines_added`` /
        ``lines_removed`` (may be null for pre-Sprint-4 binary-classified
        commits; see history.py).
        """
        history = self.get_law_history(slug)
        seen_dates: set[str] = set()
        versions: list[dict[str, Any]] = []
        # get_law_history yields events newest-first, so the first event
        # seen for a date is the latest commit on that date — the one
        # get_law_at resolves to under end-of-day semantics.
        for event in history["events"]:
            if event["type"] not in ("added", "updated"):
                continue
            if event["date"] in seen_dates:
                continue
            seen_dates.add(event["date"])
            versions.append(self._history_event_to_version(event))
        versions.sort(key=lambda v: v["date"])
        return versions

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
        unambiguously not what an AI caller intends. Values above
        ``_MAX_RESULT_LIMIT`` are clamped.
        """
        limit = _bounded_limit(limit)
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
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Substring-match ``query`` against slug + title (case-insensitive).

        Matches against manifest data only (slug, title) — no body
        text scan in this MVP. Body-text search would be a separate
        sprint with its own indexing strategy.

        ``limit`` caps the result count (default 20), must be non-negative,
        and is clamped to ``_MAX_RESULT_LIMIT`` — without it a broad query
        returned the entire matching corpus in one response.
        """
        limit = _bounded_limit(limit)
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
        return results[:limit]

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

        ``limit`` caps the result count, must be non-negative, and is
        clamped to ``_MAX_RESULT_LIMIT``. ``dataset`` accepts
        ``lover`` / ``forskrifter`` (or the full Lovdata key).

        The body index is loaded lazily on the first call (~45 MB
        resident for the production 4522-doc corpus, ~3-5 s cold
        load) so server startup stays fast for clients that only
        query metadata.
        """
        limit = _bounded_limit(limit)
        if not query.strip():
            return []
        needle = query.lower()
        dataset_key = _resolve_dataset(dataset) if dataset is not None else None
        index = self._load_body_index()
        slug_index = self._load_slug_index()
        results: list[dict[str, Any]] = []
        for doc_id, record in self.manifest.documents.items():
            if record.status != "current" or record.slug is None:
                continue
            # On a duplicate slug only the slug-index owner (first
            # manifest entry) produces a hit — otherwise the same
            # body would be reported once per claiming record, with
            # doc_ids that point lookups cannot resolve.
            if slug_index.get(record.slug, (doc_id, record))[0] != doc_id:
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

    def get_eu_basis(self, slug: str) -> dict[str, Any]:
        """Return the EU / EEA CELEX identifiers a Norwegian act implements.

        ``slug`` is the act's kortform (same as for ``get_law``).
        Returns ``{slug, doc_id, title, dataset, eu_basis}`` where
        ``eu_basis`` is the list of CELEX ids stored in the manifest
        (e.g. ``["32016R0679", "32014L0090"]``). Empty list when the
        act has no EEA references or only an EØS-avtalen annex link
        without specific directives / regulations.

        Raises ``CorpusNotFoundError`` if the slug is unknown OR the
        manifest record predates the Sprint 8 PR-D backfill (i.e.
        ``eu_basis is None``) — that signal is corpus-staleness, not
        a missing field, so the AI should suggest ``corpus_status``
        + ``git pull`` to remediate rather than treating the field
        as 'unset'.
        """
        entry = self._load_slug_index().get(slug)
        if entry is None:
            raise CorpusNotFoundError(
                f"no current law with slug {slug!r}; "
                f"use search_laws or list_recent_changes to discover slugs",
            )
        doc_id, record = entry
        # eu_basis is None signals a pre-Sprint-8 manifest record.
        # The backfill migration runs on the next sync, but until
        # then the MCP server has no authoritative answer to give.
        # Returning [] would be a silent lie ("we know there's no
        # EU basis"); raising is the honest answer.
        if record.eu_basis is None:
            raise CorpusNotFoundError(
                f"eu_basis is unknown for {slug!r}; corpus predates Sprint 8 PR-D. "
                f"Run 'git pull' in the corpus to refresh.",
            )
        return {
            "slug": record.slug,
            "doc_id": doc_id,
            "title": record.title,
            "dataset": _subdir_for_dataset(record.source_dataset),
            "eu_basis": list(record.eu_basis),
        }

    def search_eu_implementations(self, eu_doc_id: str) -> list[dict[str, Any]]:
        """Reverse lookup: list Norwegian acts that implement a given EU
        document.

        ``eu_doc_id`` is a CELEX identifier (e.g. ``"32016R0679"`` for
        GDPR). Match is case-insensitive — Lovdata stores CELEX values
        lowercase but EU canonical form is uppercase, and lovspor
        normalizes to uppercase on extraction; we accept either form
        from the caller and compare in uppercase.

        Returns one row per implementing act: ``{slug, doc_id, title,
        dataset}``, sorted by slug for stable output. Empty list when
        no current act references the given CELEX.

        Records with ``eu_basis is None`` (pre-Sprint-8 manifest) are
        skipped silently — the migration will populate them on the
        next sync. Tombstones are skipped because removed acts no
        longer 'implement' anything; an EU document that was
        previously implemented by a now-removed Norwegian act should
        not appear in current results.
        """
        if not eu_doc_id.strip():
            return []
        needle = eu_doc_id.strip().upper()
        results: list[dict[str, Any]] = []
        for doc_id, record in self.manifest.documents.items():
            if record.status != "current":
                continue
            if record.eu_basis is None:
                continue
            if needle not in record.eu_basis:
                continue
            results.append(
                {
                    "slug": record.slug,
                    "doc_id": doc_id,
                    "title": record.title,
                    "dataset": _subdir_for_dataset(record.source_dataset),
                },
            )
        results.sort(key=lambda hit: hit["slug"] or "")
        return results

    def semantic_search(
        self,
        query: str,
        dataset: str | None = None,
        limit: int = 20,
        min_score: float = _SEMANTIC_MIN_SCORE_DEFAULT,
    ) -> dict[str, Any]:
        """Top-K cosine semantic search over per-section embeddings.

        Returns ``{results, notice}``. ``results`` is ranked by cosine
        similarity to ``query``; each hit carries ``slug``,
        ``section_id``, ``score``, ``title``, ``dataset``,
        ``citation_hint`` (a paste-in ``§ <id> <slug>`` string),
        ``heading`` (the section's real heading line), ``snippet``
        (the first ~200 chars of the section body — actual corpus
        text, so every hit is self-grounding), and ``last_changed``
        (the act's last content change, for currency caveats).
        ``heading`` / ``snippet`` are null when the embedded section
        id no longer exists in the rendered Markdown (stale .bin,
        corpus drift) — treat such hits with extra suspicion.

        Hits scoring below ``min_score`` are dropped. When nothing
        clears the floor, ``results`` is empty and ``notice`` says so
        explicitly (including the best rejected score) — the AI must
        report "no strong match" rather than cite from memory.
        Invariant: EVERY empty-``results`` response carries a
        non-null ``notice`` (empty query, zero limit, dataset
        without embeddings, or nothing above the floor); ``notice``
        is null whenever results exist.

        Score is a *similarity*, NOT a relevance proof. Treat results
        as candidates that need verification — the recommended
        pattern is:

        1. ``semantic_search(query)`` -> top candidates
        2. ``get_section(slug, section_id)`` for each top hit ->
           read the actual text
        3. (optional) ``verify_quote(...)`` if you quote anything
           verbatim before answering the user

        Raises ``CorpusNotFoundError`` when ``OPENAI_API_KEY`` was not
        set at server startup (the embedder is unavailable) or when
        the corpus has no per-doc ``.bin`` files yet (early bootstrap;
        run ``lovspor sync`` to populate them).
        """
        if self._embedder is None:
            raise CorpusNotFoundError(
                "semantic_search is unavailable: OPENAI_API_KEY was not set "
                "at MCP server startup. Set the environment variable and "
                "restart the server.",
            )
        limit = _bounded_limit(limit)
        # Empty results ALWAYS carry a notice (documented contract) —
        # including these caller no-op cases, so the AI never has to
        # guess whether nothing matched or nothing was searched.
        if not query.strip():
            return {"results": [], "notice": "query is empty; nothing was searched."}
        if limit == 0:
            return {"results": [], "notice": "limit is 0; nothing was searched."}

        index = self._load_embedding_index()
        if not index:
            self._raise_for_empty_embedding_index()

        allowed_slugs = self._dataset_slugs(dataset)
        # Skip the (network) query-embedding call entirely when the
        # dataset filter cannot match any indexed row.
        if allowed_slugs is not None and allowed_slugs.isdisjoint(index.unique_slugs):
            return {
                "results": [],
                "notice": (
                    f"no embedded sections available in dataset {dataset!r}; "
                    f"embeddings may not be backfilled for it yet."
                ),
            }

        query_vector = self._embedder.encode([query])[0]
        hits = index.top_k(query_vector, k=limit, allowed_slugs=allowed_slugs)
        kept = [hit for hit in hits if hit.score >= min_score]
        if not kept:
            best = max((hit.score for hit in hits), default=None)
            return {"results": [], "notice": _no_strong_match_notice(min_score, best)}
        sections_memo: dict[str, dict[str, dict[str, str]]] = {}
        return {
            "results": [self._grounded_hit(hit, sections_memo) for hit in kept],
            "notice": None,
        }

    def _raise_for_empty_embedding_index(self) -> None:
        if self._stale_bin_count > 0:
            raise CorpusNotFoundError(
                f"no usable embeddings: all {self._stale_bin_count} .bin file(s) "
                "are from an older model with a different dimension. The sync's "
                "staleness check keys on content hash, not dimension, so it will "
                "not re-embed these on its own — delete the stale "
                "<dataset>/embeddings/*.bin and run 'lovspor sync' with "
                "OPENAI_API_KEY set (missing files are re-embedded), then "
                "'git pull' in the corpus to refresh.",
            )
        raise CorpusNotFoundError(
            "no embeddings found in corpus; run 'lovspor sync' with OPENAI_API_KEY "
            "set to populate per-document .bin files, then 'git pull' in the "
            "corpus to refresh.",
        )

    def _dataset_slugs(self, dataset: str | None) -> set[str] | None:
        """Slugs belonging to ``dataset``, or None for 'no filter'."""
        if dataset is None:
            return None
        dataset_key = _resolve_dataset(dataset)
        return {
            slug
            for slug, (_doc_id, record) in self._load_slug_index().items()
            if record.source_dataset == dataset_key
        }

    def _grounded_hit(
        self,
        hit: SearchHit,
        sections_memo: dict[str, dict[str, dict[str, str]]],
    ) -> dict[str, Any]:
        """Project a search hit into the grounded result row.

        ``sections_memo`` deduplicates the per-doc section parse when
        several hits land in the same act within one query.
        """
        entry = self._load_slug_index().get(hit.slug)
        record = entry[1] if entry is not None else None
        subdir = ""
        section: dict[str, str] | None = None
        if record is not None:
            try:
                subdir = _subdir_for_dataset(record.source_dataset)
            except CorpusNotFoundError:
                subdir = ""
            if hit.slug not in sections_memo:
                sections_memo[hit.slug] = _parse_sections(self._body_for_record(record))
            section = sections_memo[hit.slug].get(hit.section_id)
        return {
            "slug": hit.slug,
            "section_id": hit.section_id,
            "score": hit.score,
            "title": record.title if record is not None else None,
            "dataset": subdir,
            "citation_hint": f"§ {hit.section_id} {hit.slug}",
            "heading": section["heading"] if section is not None else None,
            "snippet": _lead_snippet(section["body"]) if section is not None else None,
            "last_changed": record.last_changed if record is not None else None,
        }

    def verify_quote(
        self,
        slug: str,
        section_id: str,
        quote: str,
    ) -> dict[str, Any]:
        """Verify that ``quote`` is verbatim text from ``§ section_id`` of ``slug``.

        Anti-hallucination guard for the AI: before claiming *"§ 5-12
        of Skatteloven says X"*, call this with the verbatim text of
        X. Returns ``{verified, slug, section_id, reason}``. ``verified``
        is true only when the quote, after case, whitespace and
        typographic-punctuation normalization (see
        ``_normalize_for_quote_match``), appears as a substring of
        the section body.

        Catches the most common citation hallucination — the AI quotes
        words that are NOT in the section it cites (often pulled from
        a different section, paraphrased from memory, or invented).
        Does NOT catch paraphrases that genuinely capture the legal
        meaning; for those the AI must fall back to ``get_section``
        and present the original Norwegian text.

        Raises only on explicit programming errors (missing slug, etc.
        — surfaced via ``get_section``). Empty quote returns
        verified=False with a clear reason rather than raising.
        """
        section_id = _normalize_section_id(section_id)
        if not quote.strip():
            return {
                "verified": False,
                "slug": slug,
                "section_id": section_id,
                "reason": "quote is empty",
            }
        try:
            section = self.get_section(slug, section_id)
        except CorpusNotFoundError as exc:
            return {
                "verified": False,
                "slug": slug,
                "section_id": section_id,
                "reason": str(exc),
            }
        section_normalized = _normalize_for_quote_match(section["body"])
        quote_normalized = _normalize_for_quote_match(quote)
        if quote_normalized in section_normalized:
            return {
                "verified": True,
                "slug": slug,
                "section_id": section_id,
                "reason": None,
            }
        return {
            "verified": False,
            "slug": slug,
            "section_id": section_id,
            "reason": (
                f"quote not found in § {section_id} of {slug!r} after case, "
                f"whitespace and typographic-punctuation normalization. The quote "
                f"may be from a different section, paraphrased rather than "
                f"verbatim, or hallucinated. "
                f"Call get_section({slug!r}, {section_id!r}) to read the actual text."
            ),
        }

    def _load_embedding_index(self) -> EmbeddingIndex:
        """Lazy-build the per-section embedding index for ``semantic_search``.

        Walks every current manifest record and tries to load
        ``<dataset_subdir>/embeddings/<slug>.bin``. Missing files are
        silently skipped — the corpus may be partially backfilled
        (Sprint 9 PR-B2 onwards every sync writes embeddings, but
        documents older than that bootstrap point have no .bin until
        the migration touches them).

        Corrupt .bin files are also skipped (parse error caught) so
        one bad file cannot block ``semantic_search`` from working
        across the rest of the corpus. The cost of the silent skip:
        a stale .bin produces zero hits for that doc instead of a
        loud crash. Loud crash is worse here because production has
        ~4500 docs and any single corrupt file would take the whole
        tool offline.
        """
        self._refresh_if_stale()
        if self._embedding_index is None:
            entries: list[tuple[str, str, np.ndarray, float]] = []
            stale = 0
            for record in self.manifest.documents.values():
                if record.status != "current" or record.slug is None:
                    continue
                try:
                    subdir = _subdir_for_dataset(record.source_dataset)
                except CorpusNotFoundError:
                    continue
                try:
                    bin_path = self._safe_join(subdir, "embeddings", f"{record.slug}.bin")
                except CorpusNotFoundError:
                    continue
                if not bin_path.exists():
                    continue
                try:
                    embedding_file = read_embeddings(bin_path)
                except (ValueError, OSError) as exc:
                    print(
                        f"semantic_search: skipping corrupt {bin_path.name}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                if self._expected_dim is not None and embedding_file.dim != self._expected_dim:
                    print(
                        f"semantic_search: skipping {bin_path.name} with dim "
                        f"{embedding_file.dim} (embedder expects {self._expected_dim}); "
                        f"file is from an older model and will be re-embedded on next sync",
                        file=sys.stderr,
                        flush=True,
                    )
                    stale += 1
                    continue
                for section_id, vector in embedding_file.sections:
                    entries.append((record.slug, section_id, vector, embedding_file.scale))
            self._embedding_index = EmbeddingIndex.from_entries(entries)
            self._stale_bin_count = stale
        return self._embedding_index

    def _section_ids_for(self, slug: str) -> set[str]:
        """Section-id set for one act, parsed lazily and cached.

        Used by cross-reference validation: only the acts a section
        actually references get read and parsed, not the whole
        corpus. Unknown slugs resolve to an empty set — the caller
        distinguishes "known act, missing §" from "unknown act" via
        the slug index, not via this lookup.
        """
        cached = self._section_ids_cache.get(slug)
        if cached is None:
            entry = self._load_slug_index().get(slug)
            body = self._body_for_record(entry[1]) if entry is not None else ""
            cached = set(_parse_sections(body).keys())
            self._section_ids_cache[slug] = cached
        return cached

    def _body_for_record(self, record: ManifestRecord) -> str:
        """Frontmatter/H1-stripped body text for one doc.

        Reuses the corpus-wide body index when ``search_body`` has
        already paid for it; otherwise reads and caches just this
        doc's file. Missing or path-escaping files resolve to an
        empty body — the same defensive posture as the full index.
        """
        slug = record.slug or ""
        if self._body_index is not None:
            return self._body_index.get(slug, "")
        cached = self._doc_bodies.get(slug)
        if cached is None:
            cached = self._read_stripped_body(record)
            self._doc_bodies[slug] = cached
        return cached

    def _read_stripped_body(self, record: ManifestRecord) -> str:
        try:
            path = self._safe_join(record.markdown_path)
        except CorpusNotFoundError:
            return ""
        if not path.exists():
            return ""
        return _strip_frontmatter_and_h1(path.read_text(encoding="utf-8"))

    def _load_body_index(self) -> dict[str, str]:
        """Lazy-build the slug -> body-text dict for ``search_body``.

        Reads every current doc's Markdown file once on first call,
        strips the YAML frontmatter and the leading H1 title (both are
        metadata already searchable via ``search_laws`` — see
        ``_strip_frontmatter_and_h1``), caches the dict for the rest
        of the server's lifetime. Files that fail the path-containment
        check or are missing on disk are silently skipped — the same
        defensive posture as ``get_law``.

        First manifest entry wins on a duplicate slug — the same
        contract as ``_load_slug_index``. Without this, point lookups
        (which fall back to this index once it is loaded) would
        silently flip from the first record's content to the last
        record's after the first ``search_body`` call (Codex PR #62
        round 1 reproducer).
        """
        self._refresh_if_stale()
        if self._body_index is None:
            index: dict[str, str] = {}
            for record in self.manifest.documents.values():
                if record.status != "current" or record.slug is None:
                    continue
                if record.slug in index:
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

    def _load_slug_index(self) -> dict[str, tuple[str, ManifestRecord]]:
        """Build ``slug -> (doc_id, record)`` once for current records.

        First manifest entry wins on a duplicate slug, matching the
        linear-scan behavior this index replaced. Pinned until the
        corpus changes on disk — the same contract as the cached manifest.
        """
        self._refresh_if_stale()
        if self._slug_index is None:
            index: dict[str, tuple[str, ManifestRecord]] = {}
            for doc_id, record in self.manifest.documents.items():
                if record.status != "current" or record.slug is None:
                    continue
                index.setdefault(record.slug, (doc_id, record))
            self._slug_index = index
        return self._slug_index

    def _find_current_by_slug(self, slug: str) -> ManifestRecord:
        entry = self._load_slug_index().get(slug)
        if entry is None:
            suggestions = self._slug_suggestions(slug)
            hint = f"did you mean {', '.join(suggestions)}? " if suggestions else ""
            raise CorpusNotFoundError(
                f"no current law with slug {slug!r}; {hint}"
                f"use search_laws or list_recent_changes to discover slugs",
            )
        return entry[1]

    def _citation_suggestion_hint(self, citation_lower: str) -> str:
        """Near-miss hint for a citation whose act token matched nothing.

        Walks the citation's slug-shaped tokens (4+ chars, so filler
        like ``i`` / ``jf`` never queries) and collects close matches
        against the canonical slugs. Empty string when there is
        nothing to suggest, so pinned exact-reason contracts stay
        intact for token-less citations.
        """
        suggestions: list[str] = []
        for token in _SLUG_TOKEN_PATTERN.findall(citation_lower):
            if len(token) < _MIN_SUGGESTION_TOKEN_CHARS:
                continue
            for match in self._slug_suggestions(token):
                if match not in suggestions:
                    suggestions.append(match)
        if not suggestions:
            return ""
        return f"; did you mean {', '.join(suggestions[:3])}? Use search_laws for canonical slugs"

    def _slug_suggestions(self, slug: str) -> list[str]:
        """Up to three near-miss canonical slugs for an unknown input.

        The most common AI mistake is the colloquial kortform
        ('skatteloven') for the canonical slug ('skatteloven-sktl');
        offering the near-miss in the error lets the AI recover in
        one step instead of a search_laws round trip. Suggestions
        are advisory — the strict-match contract is unchanged.
        """
        return difflib.get_close_matches(
            slug.lower(),
            list(self._load_slug_index()),
            n=3,
            cutoff=0.6,
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

    def _safe_relative(self, *parts: str) -> str:
        """Containment-check ``parts`` like :meth:`_safe_join`, but return the
        normalized repo-relative path (the form git pathspecs need).

        ``get_law_at`` feeds the manifest's ``markdown_path`` into
        ``git log --follow`` / ``git show <sha>:<path>``. A raw path with
        ``..`` or an absolute prefix would not traverse the filesystem (git
        stays inside the repo), but it would crash or resolve to an
        unexpected tracked file — so validate and normalize it first.
        """
        absolute = self._safe_join(*parts)
        return str(absolute.relative_to(self.corpus_path.resolve()))


def _slug_token_in_citation(slug: str, citation_lower: str) -> bool:
    """Find ``slug`` as a token (word-boundaried substring) in
    ``citation_lower``.

    A token boundary exists at position N when:

    - ``N == 0`` (start of citation), or
    - ``N == len(citation_lower)`` (end of citation), or
    - ``citation_lower[N]`` is not a slug character (per
      ``_SLUG_CHARACTER`` — i.e. not in ``[a-z0-9æøåäøåäöü-]``).

    The slug matches as a token only when both ends of its occurrence
    are at boundaries. This rejects ``"skatteloven-sktl"`` matching
    inside ``"skatteloven-sktlX"`` — the trailing ``X`` is itself a
    slug character so the right end is not at a boundary.

    Walks all occurrences of ``slug`` in ``citation_lower`` because
    the first occurrence may not be a token (e.g. ``"presskatteloven-
    sktl notes about skatteloven-sktl"`` — first match is inside a
    longer word, second is a real token).
    """
    idx = citation_lower.find(slug)
    while idx >= 0:
        before_ok = idx == 0 or not _SLUG_CHARACTER.match(citation_lower[idx - 1])
        after_pos = idx + len(slug)
        after_ok = after_pos == len(citation_lower) or not _SLUG_CHARACTER.match(
            citation_lower[after_pos],
        )
        if before_ok and after_ok:
            return True
        idx = citation_lower.find(slug, idx + 1)
    return False


def _parse_sections(body: str) -> dict[str, dict[str, str]]:
    """Walk a frontmatter-stripped body and return a section map.

    Output shape: ``{section_id: {heading, parent_chapter, body}}``.

    Boundary rules (every transition closes the current section, if
    any, before opening the new context):

    - ``## Kapittel ...`` updates ``parent_chapter`` for subsequent
      sections; does not itself open a section.
    - ``### § N-M. ...`` closes the previous section (if open) and
      opens a new one keyed by ``N-M``.
    - ``### <other text>`` (subsection grouping without ``§``) closes
      the previous section but does not open a new one — the lines
      that follow are not attributed to any section until the next
      ``### § ...``.

    The body of a section is the text strictly between its heading
    line and the next boundary heading (``###`` or ``##``), stripped
    of leading / trailing whitespace.
    """
    sections: dict[str, dict[str, str]] = {}
    current_chapter = ""
    current_id: str | None = None
    current_data: dict[str, Any] | None = None

    def _close() -> None:
        if current_id is not None and current_data is not None:
            current_data["body"] = "\n".join(current_data.pop("body_lines")).strip()
            sections[current_id] = current_data

    for line in body.split("\n"):
        chapter = _CHAPTER_HEADING.match(line)
        if chapter:
            _close()
            current_id = None
            current_data = None
            current_chapter = chapter.group(1)
            continue
        section = _SECTION_HEADING.match(line)
        if section:
            _close()
            current_id = section.group(1)
            section_title = section.group(2)
            heading = (
                f"§ {current_id}. {section_title}"
                if section_title is not None
                else f"§ {current_id}"
            )
            current_data = {
                "heading": heading,
                "parent_chapter": current_chapter,
                "body_lines": [],
            }
            continue
        if line.startswith(_SUBSECTION_HEADING_PREFIX):
            _close()
            current_id = None
            current_data = None
            continue
        if current_data is not None:
            current_data["body_lines"].append(line)
    _close()
    return sections


def _natural_section_key(section_id: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key that orders ``5-2``, ``5-10``, ``5-11`` numerically
    rather than lexicographically. Falls back to string for any non-
    numeric component (e.g. trailing letter suffix in ``5-12a``).

    Each segment becomes a ``(kind, value)`` pair where ``kind`` is
    ``0`` for numeric and ``1`` for string. This keeps comparisons
    type-homogeneous: a numeric segment always sorts before a string
    segment (so ``5-12 < 5-12a``), and within either kind the natural
    int / str comparison applies. Without the kind tag, mixing
    ``(5, 12)`` and ``(5, '12a')`` in the same sort raises
    ``TypeError`` in Python 3 — exactly the crash that turned the
    available-sections recovery message into an unhelpful traceback
    on acts that contain both ``§ 5-12`` and ``§ 5-12a``.
    """
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in section_id.split("-"))


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


def _lead_snippet(text: str) -> str:
    """First ``_SEMANTIC_SNIPPET_CHARS`` chars of ``text`` as one line.

    Whitespace runs collapse to single spaces so the snippet renders
    on a single line; a trailing ``...`` marks truncation.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= _SEMANTIC_SNIPPET_CHARS:
        return collapsed
    return collapsed[:_SEMANTIC_SNIPPET_CHARS] + "..."


def _no_strong_match_notice(min_score: float, best: float | None) -> str:
    """Explicit anti-hallucination message for an empty semantic result.

    Includes the best rejected score so the AI (and the user) can
    judge how near the miss was, and spells out the required
    behavior: report no match, never substitute training-data
    memory for the corpus.
    """
    best_part = (
        f"best candidate scored {best:.2f}" if best is not None else "no candidates were scored"
    )
    return (
        f"no sections scored >= {min_score:.2f} for this query ({best_part}). "
        f"The corpus has no strong match — do NOT cite a law from memory. "
        f"Tell the user no strong match was found, or retry with different "
        f"wording, use search_body for exact keywords, or lower min_score."
    )


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


def _compute_match_owner_starts(
    body_lower: str,
    matches: list[re.Match[str]],
    known_slugs: set[str],
) -> dict[int, int]:
    """For each adjacent pair ``(matches[i-1], matches[i])`` detect a
    known slug-token immediately preceding ``matches[i]`` (whitespace
    only between the token and the ``§``). When found, that token
    "belongs to" ``matches[i]`` and the previous match's AFTER-window
    must stop at the token's start, not at ``matches[i].start()``.

    Returns ``{prev_match_idx: owner_start_pos_in_body}``. Codex round-1
    on PR #50 caught the bug this closes: a body like
    ``"Se § 5-13. Etter annen-lov § 9-3."`` would resolve § 5-13 to
    ``annen-lov`` because that slug fell inside § 5-13's AFTER-window
    even though the slug clearly attaches to the next ``§ 9-3``
    (slug-before-§ pattern).

    Restriction to known slugs intentional: ``samt`` / ``også`` /
    ``videre`` etc. are slug-shaped tokens but not actual slugs;
    treating them as owners would over-trim windows and cause new
    false negatives elsewhere.
    """
    owners: dict[int, int] = {}
    for i in range(1, len(matches)):
        prev = matches[i - 1]
        curr = matches[i]
        between = body_lower[prev.end() : curr.start()]
        last_known: re.Match[str] | None = None
        for token_match in _SLUG_TOKEN_PATTERN.finditer(between):
            if token_match.group(0) in known_slugs:
                last_known = token_match
        if last_known is None:
            continue
        if between[last_known.end() :].strip() == "":
            owners[i - 1] = prev.end() + last_known.start()
    return owners


def _extract_cross_references(
    body: str,
    current_slug: str,
    known_slugs: set[str],
    sections_for: Callable[[str], set[str]],
) -> list[dict[str, Any]]:
    """Extract validated cross-references from a section body.

    Walks the body for ``§ N-M`` patterns. For each match, scans a
    ``±_CROSS_REF_CONTEXT_CHARS`` window for slug tokens; if a token
    matches a current manifest slug different from ``current_slug``,
    the reference is treated as cross-act. Otherwise it is treated
    as a same-act reference (validated against ``current_slug``'s
    section set).

    ``known_slugs`` is the full set of current manifest slugs (cheap
    — no body parsing). ``sections_for`` resolves one slug to its
    section-id set lazily, so only acts actually referenced get
    parsed.

    Output is deduplicated by ``(target_slug, target_section_id)``
    so a section that mentions ``§ 5-12`` three times produces one
    entry. The first occurrence's verbatim ``text`` is kept.

    Output entries: ``text`` (verbatim ``§ N-M`` substring as it
    appears in the body), ``target_slug`` (resolved act, defaults
    to ``current_slug`` when no other slug appears in the window),
    ``target_section_id`` (the parsed id), ``valid`` (bool —
    target section exists in target slug's section set), ``reason``
    (null when valid; otherwise human-readable).

    Limitations (deliberate MVP scope, deferred to a possible
    follow-up):

    - References by descriptive name (``i lov om X`` without a
      canonical slug) are silently treated as same-act and may
      false-positive validate against the current act.
    - Chapter / part references (``kapittel 4``, ``del III``)
      are not extracted.
    - Paragraph qualifiers (``første ledd``) are stripped — only
      the section_id is reported. A consumer that needs to verify
      a paragraph-level quote falls back to ``verify_quote``.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    body_lower = body.lower()
    matches = list(_CROSS_REF_SECTION.finditer(body))
    owner_starts = _compute_match_owner_starts(body_lower, matches, known_slugs)
    for idx, match in enumerate(matches):
        section_id = match.group(1)
        # Bound the slug-resolution window by the surrounding ``§``
        # matches so adjacent refs do not share context. Without this
        # bound, ``"jf. § 5-13. Likevel kan det iht. § 9-3 i annen-
        # lov"`` would resolve § 5-13 to ``annen-lov`` (which sits
        # in § 9-3's clause) and falsely classify a same-act ref as
        # cross-act. ``owner_starts`` further trims the AFTER-window
        # when the next ``§`` has a slug owner that precedes it
        # (slug-before-§ pattern, e.g. ``"annen-lov § 9-3"``);
        # without that trim, the slug owner of the next ``§`` would
        # leak backward into the current ``§``'s window.
        prev_end = matches[idx - 1].end() if idx > 0 else 0
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        if idx in owner_starts:
            next_start = min(next_start, owner_starts[idx])
        start = max(prev_end, match.start() - _CROSS_REF_CONTEXT_CHARS)
        end = min(next_start, match.end() + _CROSS_REF_CONTEXT_CHARS)
        target_slug = _resolve_slug_in_window(
            body_lower[start:end],
            current_slug,
            known_slugs,
        )
        target_sections = sections_for(target_slug) if target_slug in known_slugs else set()
        valid = section_id in target_sections
        reason: str | None = None
        if not valid:
            reason = (
                f"§ {section_id} not found in {target_slug!r}"
                if target_slug in known_slugs
                else f"slug {target_slug!r} unknown"
            )
        key = (target_slug, section_id)
        if key not in seen:
            seen[key] = {
                "text": match.group(0),
                "target_slug": target_slug,
                "target_section_id": section_id,
                "valid": valid,
                "reason": reason,
            }
    return list(seen.values())


def _resolve_slug_in_window(
    window_lower: str,
    current_slug: str,
    known_slugs: set[str],
) -> str:
    """Return the cross-act target slug for a ``§`` match's
    surrounding window, or ``current_slug`` if no other slug
    appears.

    Strategy: extract every slug-shaped token from the window in a
    single regex pass, sort longest-first so canonical multi-
    segment slugs (``skatteloven-sktl``) win over plain prefixes
    (``skatteloven``) when both appear, then return the first
    token that matches a known slug AND is not equal to the
    current slug. Equal-to-current matches are skipped so a
    same-act ref that mentions its own slug in prose is not
    misclassified as cross-act.
    """
    tokens: list[str] = _SLUG_TOKEN_PATTERN.findall(window_lower)
    tokens.sort(key=len, reverse=True)
    for token in tokens:
        if token != current_slug and token in known_slugs:
            return token
    return current_slug


def _normalize_section_id(section_id: str) -> str:
    """Fold the obvious AI-written section-id variants to the bare id.

    ``"§ 5-12"``, ``"§5-12"``, ``"5-12."`` and surrounding whitespace
    all mean ``"5-12"`` — rejecting them costs an error round trip for
    no information gain. Anything beyond these (chapter words,
    ``ledd`` qualifiers) is left untouched and fails the section
    lookup with the available-ids recovery message.
    """
    normalized = section_id.strip()
    normalized = normalized.removeprefix("§").lstrip()
    return normalized.rstrip(".").strip()


_QUOTE_FOLD_TABLE = str.maketrans(
    {
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark / typographic apostrophe
        "\u201a": "'",  # single low-9 quotation mark
        "\u201b": "'",  # single high-reversed-9 quotation mark
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u201e": '"',  # double low-9 quotation mark
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2212": "-",  # minus sign
        "\u00ad": None,  # soft hyphen (invisible) dropped entirely
    },
)
"""Typographic punctuation folded to its ASCII equivalent before
quote matching. These are the characters chat UIs and renderers
rewrite between the corpus and what the AI pastes back; an honest
quote must not fail verification over typography. ``§`` and digits
are deliberately NOT in this table."""


def _normalize_for_quote_match(text: str) -> str:
    """Normalize text for ``verify_quote`` substring matching.

    Applies Unicode NFKC, folds typographic quotes/dashes to ASCII
    (``_QUOTE_FOLD_TABLE``), lowercases, and collapses every
    whitespace run (spaces, tabs, newlines, NBSP) to a single space.
    This rejects the false-negative classes that would otherwise
    plague legitimate quotes:

    - Case differences between AI-generated text and source text
      (Norwegian legal text uses sentence case; AIs sometimes
      capitalize for emphasis).
    - Newline / tab differences from copy-paste through different
      AI client UIs that re-wrap text.
    - Curly-vs-straight quotes, en/em-dash-vs-hyphen, and soft
      hyphens introduced by typography-aware clients. A guard that
      rejects honest quotes teaches the AI to skip the guard.

    Does NOT strip punctuation classes or accents wholesale — those
    are semantically significant in Norwegian legal text (``§`` is
    not the same as ``$``, ``§ 5-12`` is not the same as ``§ 512``).
    """
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTE_FOLD_TABLE)
    return " ".join(folded.lower().split())


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


# The MCP server is a single-threaded stdio process: a hung OpenAI request
# blocks every other tool for the rest of the session. semantic_search embeds
# one short query, so it gets a tight interactive budget instead of the
# engine's batch-embedding defaults (180s x 3 retries + backoff ~= 9 minutes
# worst case). 15s x 2 attempts caps a fully-hung call at ~32s.
_MCP_EMBED_TIMEOUT_SECONDS = 15.0
_MCP_EMBED_MAX_RETRIES = 2


def _build_embedder() -> EmbeddingModel | None:
    """Instantiate the OpenAI embedder if ``OPENAI_API_KEY`` is set,
    otherwise log a warning and return None. Reads either
    ``OPENAI_API_KEY`` or the underscore-less ``OPENAI_APIKEY`` to
    match what ``Settings.from_env`` accepts on the engine side.

    The embedder is built with an interactive timeout/retry budget so a
    hung OpenAI node cannot freeze the stdio server for minutes."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
    if not api_key:
        print(
            "lovspor mcp: OPENAI_API_KEY not set; semantic_search will be disabled "
            "but the other fourteen tools work normally. Set OPENAI_API_KEY "
            "and restart to enable semantic search.",
            file=sys.stderr,
            flush=True,
        )
        return None
    return OpenAIEmbedder(
        api_key=api_key,
        timeout_seconds=_MCP_EMBED_TIMEOUT_SECONDS,
        max_retries=_MCP_EMBED_MAX_RETRIES,
    )


def build_server(corpus_path: Path) -> FastMCP:
    """Build a FastMCP server bound to ``corpus_path``.

    The reader is constructed eagerly so configuration errors (missing
    corpus, missing manifest) surface at server startup rather than
    on the first tool invocation.

    The embedder for ``semantic_search`` is constructed lazily-eager:
    if ``OPENAI_API_KEY`` is set in the environment we instantiate
    the OpenAI embedder at startup (so a malformed key surfaces
    immediately, not on the first tool call); if it is not set we
    log a warning and disable ``semantic_search`` with a clear
    runtime error. The other fourteen tools do not need the embedder
    so they continue to work without an OpenAI key — refusing to
    start the whole server over one optional dependency would be
    user-hostile.
    """
    embedder = _build_embedder()
    reader = CorpusReader(corpus_path, embedder=embedder)
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
    def get_section(slug: str, section_id: str) -> dict[str, Any]:
        """Return a single ``§`` section of a Norwegian law or regulation.

        Use this when the user asks about a specific paragraph of an
        act (e.g. *"What does § 5-12 of Skatteloven say?"*) instead
        of fetching the whole law via ``get_law``. Cheaper for the
        AI's context window when the user wants surgical access.

        ``slug``: the act's slug (same as for ``get_law``).
        ``section_id``: the bare numeric / hyphenated identifier
        WITHOUT the ``§`` prefix or trailing dot — e.g. ``"5-12"``,
        ``"1"``, ``"5-12a"``. Norwegian acts use ``§ N`` for single-
        chapter acts and ``§ N-M`` (chapter N, section M) for
        multi-chapter acts; both are accepted.

        Returns ``slug``, ``section_id``, ``heading`` (the full
        ``§ N-M. Title`` line), ``parent_chapter`` (the most recent
        ``Kapittel`` heading for structural context), ``body`` (the
        section's text up to the next section / chapter boundary),
        and ``cross_references`` — a deduplicated list of every
        ``§ N-M`` reference in the body, already validated against
        the manifest. Each cross-ref carries ``text`` (verbatim
        substring as it appears in the body), ``target_slug``
        (defaults to the current act when no other slug appears in
        the surrounding ~80 chars), ``target_section_id``, ``valid``
        (true only when the target section exists), and ``reason``
        (null when valid; otherwise a short explanation). Use this
        list to decide whether a referenced section is safe to
        quote without a follow-up ``validate_citation`` call.

        Raises if the slug or the section is unknown — the error
        message lists the act's available section ids in natural
        order so the AI can recover without a separate ``get_law``
        call.
        """
        return reader.get_section(slug, section_id)

    @mcp.tool()
    def list_sections(slug: str) -> list[dict[str, str]]:
        """List an act's table of contents: every ``§`` section id and heading.

        Use this BEFORE ``get_section`` when you don't know the exact
        section id — it answers *"which section of Skatteloven covers
        X?"*-style navigation without pulling the whole act through
        ``get_law`` (hundreds of KB for the big codes, which would
        flood your context window).

        ``slug``: the act's slug (same as for ``get_law``).

        Returns one row per section, in document order:
        ``section_id`` (feed straight into ``get_section``),
        ``heading`` (the full ``§ N-M. Title`` line), and
        ``parent_chapter`` (the ``Kapittel`` the section belongs to).
        Empty list when the act has no ``§`` sections.

        Raises if the slug is unknown — the error suggests near-miss
        slugs and points to ``search_laws``.
        """
        return reader.list_sections(slug)

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
    def get_law_at(slug: str, target_date: str) -> str:
        """Return a law's full Markdown as it stood on a given calendar date.

        Time-machine companion to ``get_law``: where ``get_law(slug)``
        always returns the current version, ``get_law_at(slug, date)``
        returns the version that was current at end-of-day UTC on
        ``date``. Use it when the user asks *"what did Skatteloven
        say in 2018?"*, or when an answer needs to anchor to the
        version of an act in force at a specific historical moment
        (case decided 2019-03-12 -> what was the relevant § as of
        2019-03-12).

        ``slug``: the act's current slug (same as for ``get_law``).
        Even if the kortform was different in the past, you pass
        today's slug — the corpus's git history is rename-aware and
        traces predecessors automatically.

        ``target_date``: ISO date ``YYYY-MM-DD``. End-of-day semantics:
        ``"2026-04-15"`` returns the version that was current at
        23:59:59 UTC on April 15. Future dates are refused with
        ``ValueError`` because they're almost always typos; use
        ``get_law(slug)`` for the current version.

        Output mirrors ``get_law``: YAML frontmatter (as it was at
        that revision — ``retrieved_at`` / ``xml_hash`` / ``eu_basis``
        reflect that point in time, not today's manifest) followed by
        the legal text in Markdown.

        Raises if the slug is unknown or if the act first appeared in
        the corpus *after* ``target_date`` — the error message points
        to ``get_law_history`` so the AI can find the earliest
        available date.
        """
        return reader.get_law_at(slug, target_date)

    @mcp.tool()
    def list_law_versions(slug: str) -> list[dict[str, Any]]:
        """List the dates on which a law had distinct content versions.

        Companion to ``get_law_at``: this answers *"which dates can I
        time-travel to?"*. Each entry is a moment when the act's content
        actually changed — pure filename renames are filtered out
        because they don't yield different ``get_law_at`` output.

        Returns oldest-first so the AI can reason about the act's
        timeline naturally (initial appearance → updates → today).
        Each entry has ``date`` (ISO ``YYYY-MM-DD`` — feed straight
        into ``get_law_at``), ``commit`` (short SHA),
        ``type`` (``added`` | ``updated``), and ``lines_added`` /
        ``lines_removed`` (may be null for legacy bulk-mode commits).

        Raises if the slug is unknown or if the corpus pre-dates the
        Sprint 5 history layer (no ``history/<slug>.json``).
        """
        return reader.list_law_versions(slug)

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
    def search_laws(
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search the corpus for laws whose slug or title contains ``query``.

        Substring match, case-insensitive, against manifest metadata
        only (no body-text scan in this MVP). Returns slug, doc_id,
        title, dataset, last_changed, and total_changes for each hit.
        Use ``get_law(slug)`` to fetch the full text of any result.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` to filter.
        ``limit``: max results (default 20, capped).
        """
        return reader.search_laws(query, dataset=dataset, limit=limit)

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
    def semantic_search(
        query: str,
        dataset: str | None = None,
        limit: int = 20,
        min_score: float = _SEMANTIC_MIN_SCORE_DEFAULT,
    ) -> dict[str, Any]:
        """Semantic search by meaning, not by substring match.

        Use when the user's question uses different vocabulary than
        the law text — e.g. "what are renter's rights when the
        landlord doesn't fix things?" finds husleieloven sections
        about *manglende vedlikehold* even though the user said
        "rights" and "fix" rather than the Norwegian legal terms.
        Complement to ``search_body`` (keyword) and ``search_laws``
        (title/slug).

        IMPORTANT — score is similarity, not relevance. A high score
        means the section is *about a similar topic*; it does not
        prove the section answers the user's question. Always
        ``get_section`` the top hits to read the actual text before
        quoting. If you quote anything verbatim, run ``verify_quote``
        as a final safety check.

        Returns ``{results, notice}``. Each result has ``slug``,
        ``section_id``, ``score`` (cosine similarity; hits below
        ``min_score`` are dropped), ``title``, ``dataset``,
        ``citation_hint`` (a paste-ready ``§ <id> <slug>`` string),
        ``heading`` (the section's actual heading), ``snippet`` (the
        first ~200 chars of the section's real text), and
        ``last_changed`` (the act's last content change — mention it
        when currency matters). Null ``heading``/``snippet`` mean the
        embedding is stale for that doc; verify via ``get_section``
        before trusting such a hit.

        When ``results`` is empty, ``notice`` explains why (e.g. no
        section cleared ``min_score``). In that case say the corpus
        has no strong match — do NOT answer from memory.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` to filter.
        ``limit``: max results, default 20, must be non-negative.
        ``min_score``: similarity floor, default 0.25; pass 0.0 to
        see every candidate.

        Privacy: the ``query`` text is sent to the OpenAI embeddings
        API to be embedded — this is the only tool that leaves the
        local machine. Fine for public-law research; do not paste
        confidential text into the query.

        Raises if ``OPENAI_API_KEY`` was not set when the server
        started, or if the corpus has no per-doc ``.bin`` files
        yet (early bootstrap state — run ``lovspor sync``).
        """
        return reader.semantic_search(
            query,
            dataset=dataset,
            limit=limit,
            min_score=min_score,
        )

    @mcp.tool()
    def validate_citation(citation: str) -> dict[str, Any]:
        """Verify that a Norwegian-law citation string actually resolves.

        Use this before quoting a citation in a final answer to the
        user — *zero-hallucination* guard. If the AI is about to
        write *"per § 5-12 of Skatteloven, ..."*, calling
        ``validate_citation("§ 5-12 skatteloven-sktl")`` first
        confirms both the act and the section exist in the corpus.

        Accepts permissive citation forms (``"§ 5-12 skatteloven-sktl"``,
        ``"skatteloven-sktl § 5-12"``, ``"§ 5-12 i skatteloven-sktl"``).
        Returns:

        - ``valid`` — bool, true only when both slug and (if present)
          section resolve.
        - ``slug`` — the matched canonical slug or null.
        - ``section_id`` — extracted ``§`` id or null.
        - ``heading`` — full ``§ N-M. Title`` line if both slug and
          section resolved; null otherwise.
        - ``reason`` — null when valid; otherwise a human-readable
          explanation (unknown slug, missing section + available list,
          ``§``-only ambiguity, unparseable input). The AI can quote
          this verbatim to explain to the user why the citation
          couldn't be confirmed.

        Slug-only citations (``"skatteloven-sktl"``) are valid as long
        as the slug is known. ``§``-only citations (``"§ 5-12"``) are
        invalid because the section id is non-unique across acts.
        """
        return reader.validate_citation(citation)

    @mcp.tool()
    def verify_quote(slug: str, section_id: str, quote: str) -> dict[str, Any]:
        """Verify a verbatim quote actually appears in a specific section.

        Anti-hallucination guard. Before answering with text like
        *"§ 5-12 of Skatteloven says: 'Pracodawca ma obowiązek...'"*
        call this with the verbatim quote you intend to attribute to
        that section. Returns ``{verified, slug, section_id, reason}``.

        Match is case-insensitive, whitespace-tolerant, and folds
        typographic punctuation (curly vs straight quotes, en/em dash
        vs hyphen, soft hyphens) — Norwegian legal text is sentence
        case but AIs sometimes capitalize for emphasis, and chat
        clients rewrite quotes and dashes in transit. Beyond that,
        punctuation and accents are NOT normalized — ``§`` is not
        the same as ``$`` and ``§ 5-12`` is not the same as
        ``§ 512``.

        Catches the most common citation hallucination: AI quotes
        words that are NOT in the section it cites (often pulled
        from a different section, paraphrased from memory, or
        outright invented). Does NOT catch faithful paraphrases —
        for those you must fall back to ``get_section`` and quote
        the original.

        Empty quote returns ``verified=false`` with a clear reason
        rather than raising. Unknown slug or section returns
        ``verified=false`` with the get_section error message in
        ``reason`` (which already lists available sections).
        """
        return reader.verify_quote(slug, section_id, quote)

    @mcp.tool()
    def get_eu_basis(slug: str) -> dict[str, Any]:
        """Return the EU / EEA legal basis of a Norwegian law or regulation.

        Use this when the user asks about a Norwegian act's relationship
        to EU / EEA law — e.g. *"Which EU directives does
        Personopplysningsloven implement?"* — or when an answer needs
        to anchor a Norwegian act in its EU origin (GDPR, MiFID II,
        REACH, etc.).

        ``slug``: the act's slug (same as for ``get_law``).

        Returns ``slug``, ``doc_id``, ``title``, ``dataset``, and
        ``eu_basis`` — a list of CELEX identifiers
        (e.g. ``["32016R0679", "32014L0090"]``). Empty list when the
        act has no EEA references in Lovdata's source XML.

        CELEX format: ``3<year><type-letter><number>`` — type letter
        ``R`` for regulation, ``L`` for directive, ``D`` for decision,
        etc. Example: ``32016R0679`` is Regulation 2016/679 (GDPR);
        ``32014L0090`` is Directive 2014/90/EU.

        Raises ``CorpusNotFoundError`` if the slug is unknown OR the
        corpus predates Sprint 8 PR-D — in the second case the manifest
        carries ``eu_basis: null``; suggest the user run the
        ``refresh_command`` from ``corpus_status`` to refresh.
        """
        return reader.get_eu_basis(slug)

    @mcp.tool()
    def search_eu_implementations(eu_doc_id: str) -> list[dict[str, Any]]:
        """Reverse-lookup Norwegian acts that implement a given EU document.

        Complement to ``get_eu_basis``: that one goes Norwegian-act
        -> EU-basis; this one goes EU-document -> Norwegian acts.
        Use when the user asks *"Which Norwegian laws implement
        GDPR?"* or *"What did Norway do about Directive 2014/90?"*.

        ``eu_doc_id``: a CELEX identifier (e.g. ``"32016R0679"`` for
        GDPR, ``"32014L0090"`` for Directive 2014/90/EU). Case-
        insensitive — uppercase / lowercase / mixed all match.

        Returns one row per implementing current act: ``slug``,
        ``doc_id``, ``title``, ``dataset``. Sorted by slug for stable
        output. Empty list when no current act references the given
        CELEX.

        Use the returned slugs with ``get_law`` or ``get_section`` to
        fetch the implementing text.
        """
        return reader.search_eu_implementations(eu_doc_id)

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

    Loads ``.env`` first: the ``lovspor mcp`` command does not build
    :class:`Settings`, so without this the server would read ``os.environ``
    with no ``.env`` applied and silently start with ``semantic_search``
    disabled whenever ``OPENAI_API_KEY`` lives in ``.env`` rather than an
    exported variable.

    Blocks until the MCP client disconnects.
    """
    load_env()
    build_server(corpus_path).run()
