"""Stdio MCP server exposing the lovverk corpus to AI consumers.

Bundles sixteen read-only tools over a local clone of the lovverk
Markdown corpus (produced by the lovspor sync engine). Each tool
answers a class of question an AI agent would naturally ask about
Norwegian law:

    get_law(slug)                       -> "Show me Skatteloven"
    get_law_at(slug, "2026-05-01")      -> "Skatteloven as the corpus held it on 2026-05-01"
    list_law_versions(slug)             -> "When did the corpus record changes to Skatteloven?"
    diff_law_versions(slug, a, b)       -> "What changed in Skatteloven between two dates?"
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

import asyncio
import collections
import difflib
import functools
import json
import re
import shlex
import subprocess
import sys
import tarfile
import threading
import unicodedata
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple, Self, TypedDict, final

import numpy as np
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl, BaseModel, model_validator
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from lovspor.access import CredentialStore
from lovspor.embeddings import (
    EmbeddingConfig,
    EmbeddingFile,
    EmbeddingIndex,
    EmbeddingModel,
    SearchHit,
    create_embedder,
    read_embeddings,
    space_id_of,
)
from lovspor.errors import ConfigError, CorpusStateError, LovsporError
from lovspor.headings import (
    ANY_HEADING,
    BLOCK_ID_PREFIX,
    SECTION_HEADING,
    SECTION_ID,
    block_id,
    canonical_section_id,
    is_block_id,
    parse_section_heading,
)
from lovspor.quota import LimitsSource, QuotaEnforcer, QuotaExceededError
from lovspor.settings import load_env
from lovspor.snapshot import (
    CorpusSnapshot,
    CorpusStateRef,
    resolve_corpus_state,
)
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest
from lovspor.temporal import append_notice, build_notice, evaluation_date_today
from lovspor.timetravel import (
    RevisionNotFoundError,
    RevisionResult,
    get_law_at_revision,
    resolve_law_at_revision,
)
from lovspor.workos_auth import (
    DEFAULT_WORKOS_LIMITS,
    CompositeVerifier,
    WorkOSTokenVerifier,
)

_STALE_THRESHOLD_DAYS = 7
"""Manifest age beyond which corpus_status() flags the corpus as stale.

Chosen against the daily 04:00 UTC sync cadence: a 7-day-old manifest
means at least one full week of scheduled syncs failed to land in the
user's local clone (most likely they simply forgot to ``git pull``).
Adjustable later if production cadence changes — keep documented in
docs/mcp.md if changed."""

_GIT_HEAD_FIELDS = 3  # sha + ISO date + subject from the --format string

_CITATION_SECTION_ID = re.compile(rf"§\s*({SECTION_ID.pattern})")
"""Matcher for the ``§ <id>`` part of a citation string, built on the SAME
grammar the heading parsers use (:data:`lovspor.headings.SECTION_ID`).

Allows whitespace between ``§`` and the id; matches anywhere in the
input. Citations occur in many forms in real AI prompts —
``§ 5-12 skatteloven``, ``skatteloven § 5-12``, ``§ 5-12 i skatteloven``,
``§5-12``, etc. — so the parser doesn't pin a fixed order; it extracts
each component independently from wherever it appears.

The previous private pattern (``[\\d-]+[a-z]?``) was NARROWER than the
section grammar, and the gap was not a miss but a mis-hit: ``§ 9 a``
truncated to ``9`` and ``§ 17aa`` to ``17a``, so the validator could
confirm a DIFFERENT section than the one cited — an anti-hallucination
guard hallucinating. Sharing the grammar removes the divergence; what
the heading parsers can address, the citation parser can extract.

The shared grammar is anchored-use-only because of its spaced-letter
suffix (``§ 8-7 a``): unanchored, ``§ 12 i skatteloven`` reads as id
``12 i``. Here that ambiguity is resolved against the cited act itself
— see ``_resolve_cited_section`` — instead of by truncating the
pattern, because the corpus really does contain both readings
(15 sections carry a genuine `` i`` suffix)."""

_CITATION_PROSE_TAIL = re.compile(r"\s+i$")
"""The one spaced-letter tail that is also a Norwegian preposition.

``§ 12 i skatteloven`` means "§ 12 OF the tax act" — but ``§ 33 i``
is also a real section id shape (15 in the corpus). The tail is
therefore dropped only AFTER the full id failed to resolve in the
cited act (``_resolve_cited_section``); every other letter tail
(``§ 9 a``) fails closed rather than validating the shorter
neighbour. ``i`` is the only single-letter Norwegian word that
plausibly follows a section number in a citation, so the special
case stays exactly this narrow."""

_SLUG_CHARACTER = re.compile(r"[a-z0-9æøåäöü-]")
"""Single character that could be part of a lovspor-rendered slug.

Used by ``_slug_token_in_citation`` to detect token boundaries: a
slug match is valid only when neither the character before nor the
character after the match is itself a slug character. Without this,
``record.slug in citation_lower`` accepts garbage like
``"skatteloven-sktlX"`` because the trailing X is appended to the
slug, contradicting the strict-match contract."""

_SECTION_HEADING = SECTION_HEADING
"""Section-heading grammar, imported from :mod:`lovspor.headings`.

Shared with ``lovspor.embeddings.sections`` so the structured
accessors and the embedding index cannot recognize different sets of
headings — see that module for why a divergence here is unrecoverable
rather than merely degraded."""

_CHAPTER_HEADING = re.compile(r"^## (.+?)\s*$")
"""Matches a chapter heading (``## Kapittel N. Title``). Captured for
the ``parent_chapter`` field returned by ``get_section`` so the AI has
context for where in the act the section lives."""

_SNIPPET_CONTEXT_CHARS = 50
"""Characters of context on each side of a body-search match in the
returned snippet. 50 chars on each side + match length ~= 100-130 chars
total, which fits a single AI message line and gives enough context
to judge relevance without overwhelming the response."""

CORPUS_SCOPE_NOTE = (
    "Corpus scope: acts (lover) and central regulations (forskrifter) from "
    "Lovdata's public data. It does NOT contain agency circulars "
    "(NAV/Helsedirektoratet rundskriv), Trygderetten or court decisions, "
    "forarbeider, or municipal regulations. A rule can be binding and absent "
    "here — an empty result is not evidence that no such rule exists."
)
"""Scope disclaimer attached to empty results.

An AI that searches a legal corpus, finds nothing, and reports "there is
no such rule" has made a claim the corpus cannot support. The gap is
routine rather than exotic: the fees a Norwegian GP is paid for issuing
a sykmelding are set by L-takster in a NAV rundskriv whose hjemmel is
folketrygdloven § 21-4 — binding, in force, and outside every dataset
this server ingests. Saying so costs a sentence; the alternative is a
confident denial."""

_TABLE_ROW_SNIPPET_CHARS = 1200
"""Cap on a whole-table-row snippet.

Generous, because the value a caller is after usually sits in the last
column and truncating before it recreates the exact failure this
branch exists to fix. Bounded all the same: the corpus contains rows
of several thousand characters (takst rows listing every valid
combination code), and a handful of those would swamp a result set."""

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


class CorpusAmbiguousSectionError(LovsporError):
    """Raised when a ``section_id`` matches more than one ``§`` in one act.

    Section ids are NOT unique within a document. Two causes in the real
    corpus (7 acts as of 2026-07-12): appendices that restart their own
    numbering (``førerkortforskriften`` — ``Vedlegg 1`` and ``Vedlegg 2`` each
    open with a ``§ 1``), and a genuine upstream repeat
    (``betalingssystemloven`` — a ``§ 6-2`` in Kapittel 6 and another in
    Kapittel 7).

    Guessing here is a hallucination vector: an AI asking for ``§ 6-2`` would
    be handed ``Endringer i andre lover`` and quote it as the answer. So the
    caller is told what the options are and must pick one via ``occurrence``.
    """


_LOOKS_LIKE_SECTION_HEADING = re.compile(r"^#{2,6} § ")
"""Looser than :data:`_SECTION_HEADING`, and deliberately so.

A line that looks like a section heading but does not parse means this
act's section index is incomplete. That fact has to reach the cross-
reference validator, because "not in the index" and "not in the act"
are only the same statement when the index is known to be whole."""


class SectionIndex(NamedTuple):
    """An act's section ids, carried together with whether they are all of them.

    Bundled rather than passed as two arguments so a caller cannot take
    the ids and quietly drop the caveat that comes with them.
    """

    ids: set[str]
    complete: bool


class ParsedSection(TypedDict):
    """One ``§`` section as parsed from a rendered act.

    ``occurrence`` is 1-based *within its own ``section_id``*, in document
    order — it is 1 for every section whose id is unique (the overwhelming
    majority), and only climbs where an id genuinely repeats. It is what makes
    an otherwise-ambiguous section addressable.

    ``layer`` classifies the document region the section sits in, from its
    governing chapter heading: ``"main"`` (the act's own body),
    ``"vedlegg"`` (an appendix — often normative, frequently with its own
    ``§`` numbering that legitimately collides with the main body), or
    ``"veileder"`` (embedded guidance/commentary whose headings mirror the
    act's sections without being provisions themselves). Purely additive:
    nothing is dropped or renumbered — a ``veileder`` heading still parses,
    still counts an occurrence, and stays addressable; the layer lets
    consumers tell a genuine duplicate provision from a commentary echo
    (LLHB RC3, 2026-08-05).
    """

    section_id: str
    occurrence: int
    heading: str
    parent_chapter: str
    layer: str
    body: str


_VEILEDER_CHAPTER = re.compile(r"^\s*veileder\b", re.IGNORECASE)
_VEDLEGG_CHAPTER = re.compile(r"^\s*vedlegg\b", re.IGNORECASE)


def _layer_for_chapter(chapter: str) -> str:
    """Classify a governing chapter heading into a document layer.

    Conservative on purpose: only headings that ANNOUNCE themselves as
    ``Vedlegg …`` or ``Veileder …`` leave the ``main`` layer. A chapter the
    rule cannot classify stays ``main`` — mislabelling a real provision as
    commentary would be the harmful direction.
    """
    if _VEILEDER_CHAPTER.match(chapter):
        return "veileder"
    if _VEDLEGG_CHAPTER.match(chapter):
        return "vedlegg"
    return "main"


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
        # Which vector space this embedder speaks. Dimension cannot stand in
        # for it: two unrelated models agree on 3072 all the time, and cosine
        # similarity across their spaces returns confident nonsense instead of
        # an error. Every candidate sidecar's recorded space is compared
        # against this before its vectors may meet a query, and a record with
        # no recorded space is Unknown — refused, never assumed compatible.
        self._embedder_space_id: str | None = space_id_of(embedder)
        # Documents left out of the last index build, by reason. Surfaced in
        # semantic_search's notice so reduced coverage is never silent.
        self._excluded_bins: collections.Counter[str] = collections.Counter()
        self._manifest: Manifest | None = None  # pragma: no mutate
        # Body-text index for search_body; lazy-loaded on first call so
        # MCP server startup stays fast for clients that only query
        # metadata. ~45 MB resident once populated for the production
        # 4522-doc corpus — acceptable for a long-lived stdio process.
        self._body_index: dict[str, str] | None = None  # pragma: no mutate
        # Per-section int8 embedding index for semantic_search; lazy-
        # loaded on first call. ~200 MB resident for the production
        # corpus at 3072-dim int8 (one contiguous matrix inside
        # EmbeddingIndex). Kept separate from _body_index because the
        # two indices have very different load profiles (binary parse
        # vs. text strip) and most consumers will use only one of them.
        self._embedding_index: EmbeddingIndex | None = None  # pragma: no mutate
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
        self._slug_index: dict[str, tuple[str, ManifestRecord]] | None = None  # pragma: no mutate
        # mtime of manifest.json as of the last cache load. Guards every
        # cache against a ``git pull`` landing underneath the long-lived
        # server (see _refresh_if_stale). None until the first read.
        self._manifest_mtime_ns: int | None = None  # pragma: no mutate
        # Cache generation, bumped on every invalidation. The per-doc caches
        # are filled from work done OUTSIDE the lock (a doc read + parse can
        # be ~1 MB and would stall every other tool call under it), so a value
        # can arrive describing a corpus that has since been refreshed away.
        # Writers snapshot this first and drop the write if it moved — the
        # write itself is atomic, but the DATA would be stale, and a stale
        # cache entry is served until the next refresh. Codex caught this on
        # PR #139: the naive version cached a pre-refresh body forever.
        self._epoch: int = 0
        # Serializes cache invalidation (_refresh_if_stale) and every lazy
        # index build. The stdio transport runs one tool at a time, but the
        # hosted HTTP transport offloads blocking tool bodies to worker
        # threads (and refreshes the corpus on a background thread), so two
        # callers could otherwise tear a half-reset cache set or each pay to
        # build the same ~45 MB / ~200 MB index. Reentrant because the
        # loaders acquire it while already holding it via ``self.manifest``.
        self._lock = threading.RLock()
        # Resolved historical states (ADR-0011 ``recorded_at``), keyed by
        # commit SHA. Deliberately NOT dropped by ``_refresh_if_stale``:
        # a ``git pull`` adds commits but cannot change what an existing
        # commit contains, so these caches can never go stale — only
        # unused. Bounded FIFO (``_MAX_SNAPSHOT_STATES``).
        self._snapshot_states: dict[str, _SnapshotData] = {}

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
        with self._lock:
            if mtime == self._manifest_mtime_ns:
                return
            # Stamp the mtime and drop every cache as one atomic step: a
            # concurrent caller must see either the whole pre-refresh set or
            # the whole reset set, never a half-nulled mix.
            self._manifest_mtime_ns = mtime
            self._epoch += 1
            self._manifest = None
            self._slug_index = None
            self._body_index = None
            self._embedding_index = None
            self._stale_bin_count = 0
            # Cleared with the index it describes. A counter left over from the
            # previous manifest would report exclusions for documents the new
            # corpus may have already fixed.
            self._excluded_bins = collections.Counter()
            self._doc_bodies = {}
            self._section_ids_cache = {}

    @property
    def manifest(self) -> Manifest:
        self._refresh_if_stale()
        if self._manifest is None:
            with self._lock:
                if self._manifest is None:
                    self._manifest = read_manifest(self.corpus_path / "manifest.json")
        return self._manifest

    def warm(self) -> None:
        """Pre-build the lazy indices so no request pays a cold build.

        The hosted HTTP server shares one reader across clients, and a cold
        build holds the cache lock for the whole build (~45 MB body index),
        stalling every concurrent request behind it — measured at ~1.6 s of
        lock wait for a trivial ``corpus_status`` racing a first
        ``search_body``. Warming before the server accepts traffic keeps the
        lock uncontended in steady state.

        stdio deliberately stays lazy: a client that only queries metadata
        should not pay a 3-5 s startup for an index it may never touch.

        Embeddings warm only when an embedder is configured — ``semantic_search``
        is disabled without one, so the ~200 MB index would be dead weight.
        """
        self._load_slug_index()
        self._load_body_index()
        if self._embedder is not None:
            self._load_embedding_index()

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

    def get_section(
        self,
        slug: str,
        section_id: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
        """Return a single ``§`` section of an act.

        ``section_id`` is the bare numeric / hyphenated identifier
        (``"5-12"`` or ``"1"``). The obvious AI-written variants —
        leading ``§``, trailing dot, surrounding whitespace — are
        normalized away rather than costing an error round trip; the
        response always carries the canonical bare id.

        ``occurrence`` disambiguates the rare act where one id names more
        than one ``§``. Leave it unset: the unique case (virtually every
        call) resolves without it, and an ambiguous id raises
        ``CorpusAmbiguousSectionError`` listing the candidates rather than
        silently handing back one of them. The response always reports the
        ``occurrence`` it resolved to.

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
        record, epoch = self._resolve_current(slug)
        # _find_current_by_slug only returns records whose slug == the
        # query slug, so record.slug is non-None here even though the
        # type annotation allows None.
        body = self._body_for_record(record, epoch)
        section, by_id = _section_from_body(slug, body, section_id, occurrence)
        # The act's own section-id set is a free by-product of the
        # parse above — seed the cache so same-act cross-refs don't
        # re-parse the body.
        self._remember_section_ids(record.slug or "", set(by_id), epoch)
        cross_references = _cross_references_for(
            section["body"],
            record.slug or "",
            set(self._load_slug_index()),
            self._section_index_for,
        )
        return _section_result(record.slug, section_id, section, cross_references)

    def list_sections(self, slug: str) -> list[dict[str, Any]]:
        """Table of contents for one act, in document order.

        One row per ``§`` section: ``{section_id, occurrence, heading,
        parent_chapter, layer, kind}``. The cheap navigation companion to
        ``get_section`` — an AI that doesn't know the exact section
        id can fetch the TOC instead of pulling the whole act
        through ``get_law`` (hundreds of KB for the big codes).

        One row per *section*, not per id: where an id repeats, every
        occurrence gets its own row (distinguished by ``occurrence``, and
        usually by ``parent_chapter`` too). The map-keyed predecessor emitted
        one row per id, so a repeated id meant a section was missing from the
        TOC altogether — the AI could not learn it existed.

        Empty list for an act with no ``§`` sections. Unknown slug
        raises with the usual recovery hints.

        Reads only this act's Markdown file; the parsed section-id
        set is seeded into the cross-reference cache as a free
        by-product.
        """
        record, epoch = self._resolve_current(slug)
        sections = _parse_sections(self._body_for_record(record, epoch))
        self._remember_section_ids(
            record.slug or "",
            {section["section_id"] for section in sections},
            epoch,
        )
        return [
            {
                "section_id": section["section_id"],
                "occurrence": section["occurrence"],
                "heading": section["heading"],
                "parent_chapter": section["parent_chapter"],
                # Document region of the section: main | vedlegg | veileder.
                # A veileder-layer row mirrors an act section without being a
                # provision — callers deciding "is this id genuinely
                # duplicated?" must not count it (RC3).
                "layer": section["layer"],
                # "block" entries are addressable content that is not a § of
                # the act (a takst table, a vedlegg, a convention article).
                # Flagged so a caller never cites one as a paragraph of law.
                "kind": "block" if is_block_id(section["section_id"]) else "section",
            }
            for section in sections
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

        The section id is read at its LONGEST and resolved against the
        cited act (``_resolve_cited_section``): an id the act does not
        contain fails closed instead of being truncated to a shorter
        neighbour that happens to exist. ``§§`` range citations are
        refused outright — the grammar does not model ranges, and
        validating one endpoint of a range as if it were the citation
        would confirm something the caller never cited.
        """
        # Slug matching is token-based and longest-wins; the reasoning
        # lives on ``_citation_verdict`` and ``_slug_token_in_citation``.
        return _citation_verdict(
            citation,
            self._load_slug_index(),
            self._resolve_cited_section,
        )

    def _resolve_cited_section(
        self,
        slug: str,
        section_id: str,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        """Resolve a citation-extracted id against the cited act itself.

        The extracted candidate is the LONGEST read of the citation
        (``§ 12 i skatteloven`` extracts ``12 i``), because the corpus
        contains genuine `` i``-suffixed sections and truncating up front
        would validate a different section than the one cited. Order:

        1. The full candidate resolves — done. ``§ 33 i`` finds § 33 i.
        2. It does not, and its tail is the preposition ``i`` — retry
           without the tail: ``§ 12 i skatteloven`` falls back to § 12.
        3. Any other unresolved candidate fails closed with get_section's
           available-sections message. ``§ 9 a`` in an act with only a
           § 9 is NOT reduced to § 9 — citing a section that does not
           exist must say so, never confirm a shorter neighbour.

        A duplicate id (``CorpusAmbiguousSectionError`` — the act really
        has several sections sharing it) is invalid-with-reason, not an
        exception: ``validate_citation`` promises a result dict, and the
        citation names a real id without singling out one section, the
        same failure class as a ``§``-only citation. No tail strip on
        that path — the id exists; re-reading it would change what the
        caller cited.
        """
        return _resolve_cited_section_with(self.get_section, slug, section_id)

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

    def at_state(self, recorded_at: str) -> "_SnapshotState":
        """Resolve ``recorded_at`` to a historical state view (ADR-0011).

        ``recorded_at`` is a state selector, not a state identity: the
        resolved commit is the identity, and the view stamps it into
        every answer as ``corpus_commit`` so a bundle's members can prove
        they share one state. Same axis, cutoff and refusal posture as
        ``get_law_at`` (ADR-0002 semantics, ``_parse_recorded_at``).

        Per-commit caches are shared across calls and dates that resolve
        to the same commit; immutable state needs no epoch guard.
        """
        target = _parse_recorded_at(recorded_at)
        ref = resolve_corpus_state(self.corpus_path, target)
        with self._lock:
            data = self._snapshot_states.get(ref.sha)
            if data is None:
                if len(self._snapshot_states) >= _MAX_SNAPSHOT_STATES:
                    self._snapshot_states.pop(next(iter(self._snapshot_states)))
                data = _SnapshotData(CorpusSnapshot(self.corpus_path, ref.sha), ref)
                self._snapshot_states[ref.sha] = data
        return _SnapshotState(data, target)

    def get_law_at(self, slug: str, target_date: str) -> str:
        """Return the rendered Markdown of ``slug`` as the corpus held it
        at the end of ``target_date`` (UTC).

        Temporal contract (ADR-0002): this is corpus history, not
        legal-validity history — the result does not establish which
        provisions were legally in force on that date, and the history
        reaches back only to when Lovspor first recorded the document.

        ``target_date`` is an ISO ``YYYY-MM-DD`` string; end-of-day
        UTC semantics ("close of business on that day"). Future dates
        are refused — a calendar date past today's UTC date is almost
        always a typo, and silently aliasing it to HEAD would mask the
        mistake. Past dates that predate the act's first appearance
        in the corpus raise with a hint pointing to ``get_law_history``
        so the AI can recover. A shallow checkout whose history does
        not reach the date raises ``ShallowHistoryError`` untranslated
        — never the "did not exist in the corpus" claim (ADR-0003).

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
    def _parse_diff_date(field: str, value: str) -> date:
        """Parse an ISO diff endpoint, rejecting future dates as a typo guard
        (same posture as ``get_law_at``, but named for the diff parameter)."""
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{field} must be ISO date YYYY-MM-DD, got {value!r}",
            ) from exc
        today = datetime.now(UTC).date()
        if parsed > today:
            raise ValueError(
                f"{field} {parsed.isoformat()} is in the future (today is {today.isoformat()})",
            )
        return parsed

    def _resolve_diff_side(self, slug: str, rel: str, target: date) -> RevisionResult:
        """Resolve one diff endpoint, translating a missing revision into the
        same user-facing ``CorpusNotFoundError`` that ``get_law_at`` raises."""
        try:
            return resolve_law_at_revision(self.corpus_path, rel, target)
        except RevisionNotFoundError as exc:
            raise CorpusNotFoundError(
                f"law {slug!r} did not exist in the corpus on {target.isoformat()}; "
                f"call get_law_history({slug!r}) to see when it first appeared",
            ) from exc

    def diff_law_versions(
        self,
        slug: str,
        date_a: str,
        date_b: str,
    ) -> dict[str, Any]:
        """Diff a law between the versions current on ``date_a`` and ``date_b``.

        Both are ISO ``YYYY-MM-DD`` strings with the same end-of-day UTC,
        rename-following resolution as ``get_law_at``. Each date is mapped to
        the latest commit on or before it; ``resolved_commit_a`` /
        ``resolved_commit_b`` report which two commits were actually compared
        (a date rarely coincides with the day the law changed). ``date_a`` is
        the "before" side — passing a later ``date_a`` yields a reverse diff.

        Compares rendered Markdown section-by-section (frontmatter stripped, so
        ``retrieved_at`` / hash churn never shows). Each changed / added /
        removed ``§`` section carries a stdlib unified diff of its heading and
        body. Sections identical on both dates are omitted.

        The response carries ADR-0002 temporal markers (``temporal_basis``,
        ``cutoff_timezone``, ``legal_validity_determined``) stating that both
        sides are corpus states, not legally-in-force reconstructions.

        Raises ``CorpusNotFoundError`` for an unknown slug or a date before the
        law first appeared; ``ValueError`` for a non-ISO or future date.
        """
        parsed_a = self._parse_diff_date("date_a", date_a)
        parsed_b = self._parse_diff_date("date_b", date_b)
        record = self._find_current_by_slug(slug)
        rel = self._safe_relative(record.markdown_path)
        rev_a = self._resolve_diff_side(slug, rel, parsed_a)
        rev_b = self._resolve_diff_side(slug, rel, parsed_b)
        sections_a = _parse_sections(_strip_frontmatter_and_h1(rev_a.content))
        sections_b = _parse_sections(_strip_frontmatter_and_h1(rev_b.content))
        diff = _diff_section_maps(sections_a, sections_b)
        return {
            "slug": record.slug,
            "date_a": parsed_a.isoformat(),
            "date_b": parsed_b.isoformat(),
            "resolved_commit_a": rev_a.sha,
            "resolved_commit_b": rev_b.sha,
            # ADR-0002 temporal markers: both sides are corpus states at
            # end-of-day UTC, not reconstructions of legally-in-force text.
            # Additive keys — existing consumers pin individual keys, never
            # the full key set, so this does not break the response schema.
            "temporal_basis": "corpus_history",
            "cutoff_timezone": "UTC",
            "legal_validity_determined": False,
            "summary": diff["summary"],
            "sections": diff["sections"],
        }

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
        time-machine consumer's point of view. Dates are the UTC dates
        on which the corpus recorded the change, not entry-into-force
        dates (ADR-0002).

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

        A rendered footnote marker (``[^1]``) may interrupt the match
        anywhere — see ``_marker_tolerant_query``. Without that the
        scan silently loses every phrase the renderer annotated.

        ``limit`` caps the result count, must be non-negative, and is
        clamped to ``_MAX_RESULT_LIMIT``. ``dataset`` accepts
        ``lover`` / ``forskrifter`` (or the full Lovdata key).

        The body index is loaded lazily on the first call so server
        startup stays fast for clients that only query metadata. It is
        not small: on the renderer-5 corpus (5,913 docs, 113.5 M chars)
        it is ~210 MB of strings, ~270 MB peak RSS, ~1 s to load off a
        warm page cache.
        """
        index = self._load_body_index()
        docs = _iter_search_docs(self.manifest.documents, self._load_slug_index(), index.get)
        return _search_body_hits(query, docs, dataset, limit)

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
        coverage = self._coverage_notice()
        # Skip the (network) query-embedding call entirely when the
        # dataset filter cannot match any indexed row.
        if allowed_slugs is not None and allowed_slugs.isdisjoint(index.unique_slugs):
            bootstrap = (
                f"no embedded sections available in dataset {dataset!r}; "
                f"embeddings may not be backfilled for it yet."
            )
            # The exclusion reason has to survive this early return too. Without
            # it a dataset whose documents were dropped for identity reasons
            # reports the bootstrap "not backfilled yet" story instead, which
            # names the wrong cause and the wrong remedy.
            return {
                "results": [],
                "notice": f"{bootstrap} {coverage}" if coverage else bootstrap,
            }

        query_vector = self._embedder.encode([query])[0]
        hits = index.top_k(query_vector, k=limit, allowed_slugs=allowed_slugs)
        kept = [hit for hit in hits if hit.score >= min_score]
        if not kept:
            best = max((hit.score for hit in hits), default=None)
            notice = _no_strong_match_notice(min_score, best)
            return {
                "results": [],
                "notice": f"{notice} {coverage}" if coverage else notice,
            }
        sections_memo: dict[str, dict[str, list[ParsedSection]]] = {}
        return {
            "results": [self._grounded_hit(hit, sections_memo) for hit in kept],
            # Non-empty results still carry a notice when part of the corpus was
            # excluded: the answer is trustworthy, its coverage is not complete,
            # and the caller must be told the difference.
            "notice": coverage,
        }

    def _raise_for_empty_embedding_index(self) -> None:
        excluded = getattr(self, "_excluded_bins", None) or {}
        unknown = excluded.get("unknown_space", 0)
        mismatched = excluded.get("different_space", 0)
        if unknown or mismatched:
            # Remedies differ, so they are named separately. A document
            # recording a *different* space is stale and an ordinary keyed sync
            # re-embeds it. A document recording *no* space is deliberately not
            # stale — sync leaves it alone — so telling the operator to re-run
            # sync would promise a fix that cannot happen.
            remedies = []
            if mismatched:
                remedies.append(
                    f"{mismatched} record a different space; re-running 'lovspor sync' "
                    "with the configured provider re-embeds and re-stamps those",
                )
            if unknown:
                remedies.append(
                    f"{unknown} record no embedding space at all — written keyless or "
                    "by an embedder that declared no identity. A keyed 'lovspor sync' "
                    "re-embeds and stamps the ones whose content is out of date, but "
                    "an ordinary sync will NOT change up-to-date records, because "
                    "absent identity alone is never staleness — those stay excluded "
                    "until re-embedded under an identity-declaring provider "
                    "configuration",
                )
            raise CorpusNotFoundError(
                "semantic_search is unavailable: no document in this corpus is known to "
                "share the configured embedding space, and a vector from an unknown or "
                "different space cannot be compared with a query — doing so returns "
                "confident but meaningless results. " + "; ".join(remedies) + ".",
            )
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
        sections_memo: dict[str, dict[str, list[ParsedSection]]],
    ) -> dict[str, Any]:
        """Project a search hit into the grounded result row.

        ``sections_memo`` deduplicates the per-doc section parse when
        several hits land in the same act within one query.

        ``ambiguous_section`` is true when the act has more than one ``§`` under
        the hit's id. The embedding store cannot say which one matched, so
        ``heading``, ``snippet`` and ``occurrence`` are withheld rather than
        guessed — see the comment below.
        """
        record, epoch = self._lookup_current(hit.slug)
        subdir = ""
        section: ParsedSection | None = None  # pragma: no mutate
        ambiguous = False
        if record is not None:
            try:
                subdir = _subdir_for_dataset(record.source_dataset)
            except CorpusNotFoundError:
                subdir = ""
            if hit.slug not in sections_memo:
                sections_memo[hit.slug] = _sections_by_id(
                    _parse_sections(self._body_for_record(record, epoch)),
                )
            # A hit carries a bare section_id, and the embedding store keys by
            # that same bare id. Where an id repeats within an act, nothing in
            # the hit says WHICH § 6-2 the matching vector came from. The row
            # ordinal cannot stand in for the occurrence either: a long section
            # is chunked into several vectors under that same id (see
            # `_write_embeddings_for_doc`), so on disk "chunk 2 of § 5-12" and
            # "occurrence 2 of § 6-2" have the same shape.
            #
            # It is not strictly unrecoverable. Rows are written in section order
            # and chunk order within a section, so replaying `iter_sections` +
            # `split_to_token_chunks` over the current body would rebuild the same
            # id sequence and map a row back to its occurrence. But that is a
            # RECONSTRUCTION, not a proof: the .bin carries no chunker version and
            # no chunk count, so a change to the chunker would silently re-align
            # every row and ground hits to the wrong § with no signal at all.
            # `embedding_hash == xml_hash` pins the vectors to the current content
            # but says nothing about the chunker that produced them.
            #
            # On the corpus's most safety-critical path, a reconstruction that can
            # fail silently is not worth a snippet. So do not guess: report the
            # score and the id, withhold what cannot be known, and let the caller
            # resolve it via get_section with an explicit occurrence. Making this
            # sound means the format carries the occurrence (or a chunker version)
            # — a corpus-wide re-embed, out of scope here.
            matches = sections_memo[hit.slug].get(hit.section_id, [])
            section = matches[0] if len(matches) == 1 else None
            ambiguous = len(matches) > 1
        return {
            "slug": hit.slug,
            "section_id": hit.section_id,
            "occurrence": section["occurrence"] if section is not None else None,
            "ambiguous_section": ambiguous,
            "score": hit.score,
            "title": record.title if record is not None else None,
            "dataset": subdir,
            # A block is not a §; emitting "§ #takster-fra-1-juli-2026" as a
            # paste-ready citation would manufacture a paragraph of law that
            # does not exist.
            "citation_hint": (
                f"{hit.slug} > {hit.section_id.removeprefix(BLOCK_ID_PREFIX)}"
                if is_block_id(hit.section_id)
                else f"§ {hit.section_id} {hit.slug}"
            ),
            "heading": section["heading"] if section is not None else None,
            "snippet": _lead_snippet(section["body"]) if section is not None else None,
            "last_changed": record.last_changed if record is not None else None,
        }

    def verify_quote(
        self,
        slug: str,
        section_id: str,
        quote: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
        """Verify that ``quote`` is verbatim text from ``§ section_id`` of ``slug``.

        Anti-hallucination guard for the AI: before claiming *"§ 5-12
        of Skatteloven says X"*, call this with the verbatim text of
        X. Returns ``{verified, slug, section_id, reason}``. ``verified``
        is true only when the quote, after case, whitespace and
        typographic-punctuation normalization (see
        ``_normalize_for_quote_match``), appears as a substring of
        the section body.

        ``occurrence`` disambiguates the rare act where one id names
        more than one ``§`` — same one-based semantics as
        ``get_section``. Leave it unset for the unique case. A
        duplicate id without an occurrence returns ``verified=false``
        with the candidate list in ``reason`` — never a guess, never a
        raise, and never a search across the candidates: a guard that
        quietly picked one occurrence (or matched the quote against
        all of them) would convert ambiguity into exactly the
        hallucination it exists to catch. With an occurrence set, the
        quote is checked against that occurrence's body ONLY — a quote
        that lives in the other duplicate returns ``verified=false``.

        Catches the most common citation hallucination — the AI quotes
        words that are NOT in the section it cites (often pulled from
        a different section, paraphrased from memory, or invented).
        Does NOT catch paraphrases that genuinely capture the legal
        meaning; for those the AI must fall back to ``get_section``
        and present the original Norwegian text.

        Never raises on citation content: empty quote, unknown slug or
        section, ambiguous id, and out-of-range occurrence all return
        verified=False with a recovery-oriented reason.
        """
        return _quote_verdict(
            _QuoteQuery(slug, section_id, quote, occurrence),
            self.get_section,
        )

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
        if self._embedding_index is not None:
            return self._embedding_index
        with self._lock:
            if self._embedding_index is not None:
                return self._embedding_index
            entries: list[tuple[str, str, np.ndarray, float]] = []
            stale = 0
            excluded: collections.Counter[str] = collections.Counter()
            for record in self.manifest.documents.values():
                if record.status != "current" or record.slug is None:
                    continue
                # Every `continue` below this point MUST count the record.
                # A current, slugged document that is neither indexed nor
                # counted has silently left the searchable corpus, and the
                # coverage notice — the only signal a caller gets — would say
                # nothing. Three separate exclusions reached production
                # uncounted before this rule was written down.
                try:
                    subdir = _subdir_for_dataset(record.source_dataset)
                except CorpusNotFoundError:
                    # A dataset this build does not know: a corpus written by a
                    # newer engine, read by an older one.
                    excluded["unknown_dataset"] += 1
                    continue
                try:
                    bin_path = self._safe_join(subdir, "embeddings", f"{record.slug}.bin")
                except CorpusNotFoundError:
                    excluded["unsafe_path"] += 1
                    continue
                if not bin_path.exists():
                    # Counted, not merely skipped. A document with no sidecar is
                    # as absent from the answer as one excluded for identity, and
                    # leaving it uncounted meant a half-embedded corpus returned
                    # results with no notice at all — silent recall loss in the
                    # plainest possible form.
                    excluded["missing"] += 1
                    continue
                # Embedding-space identity is checked BEFORE the file is read.
                # The manifest is the only thing that can answer which space a
                # sidecar belongs to (ADR-0005 Stage 1); the bytes cannot, and
                # dimension is a shape check, never evidence of compatibility.
                if record.embedding_space_id is None:
                    excluded["unknown_space"] += 1
                    continue
                if record.embedding_space_id != self._embedder_space_id:
                    excluded["different_space"] += 1
                    continue
                try:
                    # UnsupportedSidecarVersionError is deliberately NOT caught
                    # here: it is not a ValueError, so a corpus written in a
                    # newer format fails this whole call loudly instead of
                    # being silently skipped file-by-file (ADR-0005 §3).
                    embedding_file = read_embeddings(bin_path)
                except (ValueError, OSError) as exc:
                    print(
                        f"semantic_search: skipping corrupt {bin_path.name}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    excluded["corrupt"] += 1
                    continue
                _require_sidecar_matches_manifest(embedding_file, record, bin_path)
                if self._expected_dim is not None and embedding_file.dim != self._expected_dim:
                    print(
                        f"semantic_search: skipping {bin_path.name} with dim "
                        f"{embedding_file.dim} (embedder expects {self._expected_dim}); "
                        f"file is from an older model and will be re-embedded on next sync",
                        file=sys.stderr,
                        flush=True,
                    )
                    stale += 1
                    excluded["wrong_dimension"] += 1
                    continue
                for section_id, vector in embedding_file.sections:
                    entries.append((record.slug, section_id, vector, embedding_file.scale))
            self._embedding_index = EmbeddingIndex.from_entries(entries)
            self._stale_bin_count = stale
            self._excluded_bins = excluded
            return self._embedding_index

    def _coverage_notice(self) -> str | None:
        """Describe documents left out of the index, or ``None`` if none were.

        Exclusions are reported in-band rather than only to stderr. A search
        that quietly covers less of the corpus than the caller believes is the
        failure this whole identity mechanism exists to prevent, and a stderr
        line inside a stdio subprocess is not a signal any MCP client sees.
        """
        excluded = getattr(self, "_excluded_bins", None)
        if not excluded:
            return None
        # Each reason carries its own remedy. A blanket "run sync" would be
        # wrong for the unknown-space case, which sync deliberately leaves
        # alone, and pointing an operator at an action that cannot fix the
        # state is worse than saying nothing.
        reasons = {
            "missing": (
                "have no embedding sidecar yet — 'lovspor sync' with the configured "
                "provider embeds them"
            ),
            "unknown_dataset": (
                "belong to a dataset this build does not recognise — the corpus may have "
                "been written by a newer lovspor; upgrade it"
            ),
            "unsafe_path": "resolve to a path outside the corpus and were not read",
            "unknown_space": (
                "have no recorded embedding space — written keyless or by an embedder "
                "that declared no identity; a keyed 'lovspor sync' stamps the ones "
                "whose content is stale, and deliberately leaves up-to-date records "
                "excluded (absent identity alone is never staleness)"
            ),
            "different_space": (
                "were built by a different embedding model — 'lovspor sync' with the "
                "configured provider re-embeds them"
            ),
            "corrupt": "could not be read — 'lovspor sync' regenerates them",
            "wrong_dimension": (
                "have a different vector dimension — 'lovspor sync' regenerates them"
            ),
        }
        parts = [
            f"{count} document(s) {reasons[reason]}"
            for reason, count in sorted(excluded.items())
            if reason in reasons
        ]
        return (
            "coverage is incomplete — these results do not cover the whole corpus: "
            + "; ".join(parts)
            + "."
        )

    def _resolve_current(self, slug: str) -> tuple[ManifestRecord, int]:
        """Resolve a slug to a current record, paired with its own generation.

        The record and the epoch MUST be read under one lock. Resolving the
        record first and snapshotting the epoch after leaves a window where a
        refresh lands between the two: the caller then holds a *pre*-refresh
        record stamped with the *post*-refresh epoch, so everything derived
        from that record (notably its ``markdown_path``) sails through the
        write-back guard and poisons the fresh cache. That is the second bug
        Codex found on PR #139, and it is why there is no way to obtain an
        epoch that is not already bound to a record.

        Raises ``CorpusNotFoundError`` for an unknown slug.
        """
        with self._lock:
            return self._find_current_by_slug(slug), self._epoch

    def _lookup_current(self, slug: str) -> tuple[ManifestRecord | None, int]:
        """``_resolve_current`` for callers that tolerate an unknown slug:
        returns ``(None, epoch)`` instead of raising. Same atomicity contract."""
        with self._lock:
            entry = self._load_slug_index().get(slug)
            return (entry[1] if entry is not None else None), self._epoch

    def _remember_body(self, slug: str, body: str, epoch: int) -> None:
        """Cache a doc body unless the corpus refreshed since ``epoch``.

        Dropping the write costs one re-read; keeping it would serve the
        pre-refresh text until the next refresh. See ``_epoch``.
        """
        with self._lock:
            if epoch == self._epoch:
                self._doc_bodies.setdefault(slug, body)

    def _remember_section_ids(self, slug: str, ids: set[str], epoch: int) -> None:
        """Cache an act's section-id set unless the corpus refreshed since
        ``epoch``. Same staleness reasoning as ``_remember_body``."""
        with self._lock:
            if epoch == self._epoch:
                self._section_ids_cache.setdefault(slug, ids)

    def _section_index_for(self, slug: str) -> SectionIndex:
        """Section-id set for one act, plus whether that set is complete.

        Completeness is not a detail the caller can shrug off. The whole
        point of ``valid`` on a cross-reference is to tell an AI whether a
        citation is real, and a "not found" derived from an index that is
        known to be missing entries is not evidence of absence — it is the
        guard against hallucination emitting one.
        """
        return SectionIndex(
            ids=self._section_ids_for(slug),
            complete=self._section_index_is_complete(slug),
        )

    def _section_index_is_complete(self, slug: str) -> bool:
        """True when every §-shaped heading in ``slug`` actually parsed.

        Cheap because the body is already cached by ``_section_ids_for``;
        this is a second pass over lines that are in memory.
        """
        record, epoch = self._lookup_current(slug)
        body = self._body_for_record(record, epoch) if record is not None else ""
        return not any(
            _LOOKS_LIKE_SECTION_HEADING.match(line) and not _SECTION_HEADING.match(line)
            for line in body.split("\n")
        )

    def _section_ids_for(self, slug: str) -> set[str]:
        """Section-id set for one act, parsed lazily and cached.

        Used by cross-reference validation: only the acts a section
        actually references get read and parsed, not the whole
        corpus. Unknown slugs resolve to an empty set — the caller
        distinguishes "known act, missing §" from "unknown act" via
        the slug index, not via this lookup.
        """
        with self._lock:
            cached = self._section_ids_cache.get(slug)
            if cached is not None:
                return cached
        record, epoch = self._lookup_current(slug)
        body = self._body_for_record(record, epoch) if record is not None else ""
        ids = {section["section_id"] for section in _parse_sections(body)}
        self._remember_section_ids(slug, ids, epoch)
        return ids

    def _body_for_record(self, record: ManifestRecord, epoch: int) -> str:
        """Frontmatter/H1-stripped body text for one doc.

        Reuses the corpus-wide body index when ``search_body`` has
        already paid for it; otherwise reads and caches just this
        doc's file. Missing or path-escaping files resolve to an
        empty body — the same defensive posture as the full index.

        ``epoch`` is the generation ``record`` was resolved in — it must come
        from ``_resolve_current`` / ``_lookup_current`` alongside the record,
        never be snapshotted here. Reading it here would stamp a caller's stale
        record with the current generation and cache the wrong body under it.
        """
        slug = record.slug or ""
        with self._lock:
            body_index = self._body_index
            if body_index is not None:
                return body_index.get(slug, "")
            cached = self._doc_bodies.get(slug)
            if cached is not None:
                return cached
        # Read off-lock: an act can be ~1 MB, and stripping it while holding
        # the lock would stall every other tool call behind this one doc.
        body = self._read_stripped_body(record)
        self._remember_body(slug, body, epoch)
        return body

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
        if self._body_index is not None:
            return self._body_index
        with self._lock:
            if self._body_index is not None:
                return self._body_index
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
            # Freshness and coverage are different questions, and a caller that
            # confirms the first often assumes the second. "Current" says the
            # corpus matches Lovdata today; it says nothing about the rules
            # Lovdata's public datasets never carried.
            "scope": CORPUS_SCOPE_NOTE,
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
        if self._slug_index is not None:
            return self._slug_index
        with self._lock:
            if self._slug_index is not None:
                return self._slug_index
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
        return _citation_hint_from(self._load_slug_index(), citation_lower)

    def _slug_suggestions(self, slug: str) -> list[str]:
        """Up to three near-miss canonical slugs for an unknown input.

        The most common AI mistake is the colloquial kortform
        ('skatteloven') for the canonical slug ('skatteloven-sktl');
        offering the near-miss in the error lets the AI recover in
        one step instead of a search_laws round trip. Suggestions
        are advisory — the strict-match contract is unchanged.
        """
        return _slug_suggestions_from(self._load_slug_index(), slug)

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


def _require_sidecar_matches_manifest(
    embedding_file: EmbeddingFile,
    record: ManifestRecord,
    bin_path: Path,
) -> None:
    """ADR-0005 Stage 2: the file's own identity must agree with the manifest.

    A version-2 sidecar carries its ESI in the header, so a substituted
    file at a manifest-referenced path is finally detectable. A mismatch is
    a tamper/corpus-corruption signal, not a per-file condition — refuse the
    whole call rather than silently shrink the searched corpus. Version-1
    files carry no identity (``embedding_space_id is None``) and pass
    through: for them the manifest remains the sole authority, exactly the
    Stage 1 status quo.
    """
    if (
        embedding_file.embedding_space_id is not None
        and embedding_file.embedding_space_id != record.embedding_space_id
    ):
        raise CorpusStateError(
            f"{bin_path.name}: sidecar-carried embedding space "
            f"{embedding_file.embedding_space_id} does not match the "
            f"manifest's {record.embedding_space_id} for {record.slug!r} — "
            f"the file is not the one the manifest describes; refusing to "
            f"search",
        )


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


def _parse_sections(body: str) -> list[ParsedSection]:
    """Walk a frontmatter-stripped body and return its sections, in order.

    Returns a **list**, not a map keyed by ``section_id``, because section ids
    are not unique within a document — see ``CorpusAmbiguousSectionError``. The
    previous dict-keyed version assigned last-wins, silently discarding
    ``§ 6-2. Forskrifter`` from ``betalingssystemloven`` and every appendix
    section whose id collided with the main body's. Callers that need lookup
    build a map with ``_sections_by_id``.

    Boundary rules (every transition closes the current section, if
    any, before opening the new context):

    - ``### § N-M. ...`` (chaptered act) or ``## § N. ...`` (flat act,
      no chapter level) closes the previous section and opens a new one
      keyed by its id. The section pattern is tested before the chapter
      pattern precisely because a flat act's ``## §`` also looks like a
      chapter heading.
    - ``## Kapittel ...`` (an ``##`` line that is NOT a ``§`` section)
      updates ``parent_chapter`` for subsequent sections; does not
      itself open a section.
    - ``### <other text>`` (subsection grouping without ``§``) closes
      the previous section but does not open a new one — the lines
      that follow are not attributed to any section until the next
      ``§`` heading.

    The body of a section is the text strictly between its heading
    line and the next boundary heading (``###`` or ``##``), stripped
    of leading / trailing whitespace.
    """
    sections: list[ParsedSection] = []
    seen: dict[str, int] = {}
    current_chapter = ""
    current_id: str | None = None  # pragma: no mutate
    current_data: dict[str, Any] | None = None  # pragma: no mutate

    def _close() -> None:
        if current_id is None or current_data is None:
            return
        seen[current_id] = seen.get(current_id, 0) + 1
        sections.append(
            ParsedSection(
                section_id=current_id,
                occurrence=seen[current_id],
                heading=current_data["heading"],
                parent_chapter=current_data["parent_chapter"],
                layer=_layer_for_chapter(current_data["parent_chapter"]),
                body="\n".join(current_data["body_lines"]).strip(),
            ),
        )

    for line in body.split("\n"):
        # Section check MUST precede the chapter check: a flat act's ``## § 1.``
        # heading also matches _CHAPTER_HEADING (any ``## ...``), so testing the
        # section pattern first is what stops it being misread as a chapter.
        section = parse_section_heading(line)
        if section:
            _close()
            written_id, section_title = section
            # Key by the canonical id so every spelling of one section resolves
            # to one entry, but build the display heading from the id AS WRITTEN
            # — `§ 8-7 a` must not be quoted back to the caller as `§ 8-7a`.
            current_id = canonical_section_id(written_id)
            heading = (
                f"§ {written_id}. {section_title}"
                if section_title is not None
                else f"§ {written_id}"
            )
            current_data = {
                "heading": heading,
                "parent_chapter": current_chapter,
                "body_lines": [],
            }
            continue
        chapter = _CHAPTER_HEADING.match(line)
        if chapter:
            _close()
            current_id = None
            current_data = None
            current_chapter = chapter.group(1)
            continue
        block = ANY_HEADING.match(line)
        if block:
            # An H3-H6 heading without a ``§`` opens a content BLOCK rather than
            # merely closing the previous section. Treating it as a pure
            # boundary is what made ~40% of corpus text unaddressable: the
            # takst tables, the ECHR articles inside menneskerettsloven, every
            # vedlegg. The lines belong to something; this names it.
            _close()
            block_heading = line[block.end() :].strip()
            current_id = block_id(block_heading) or None
            current_data = (
                None
                if current_id is None
                else {
                    "heading": block_heading,
                    "parent_chapter": current_chapter,
                    "body_lines": [],
                }
            )
            continue
        if current_data is not None:
            current_data["body_lines"].append(line)
    _close()
    return sections


def _available_ids_message(by_id: dict[str, list[ParsedSection]]) -> str:
    """Render the recovery list for a failed section lookup.

    Enumerates ``§`` ids, and only counts content blocks. An act like
    takstforskriften has a handful of ``§`` and dozens of blocks, so
    listing both inline would bury the thing the caller asked for. The
    count still has to be there: this message is what an AI reads as the
    act's inventory, and silently omitting a whole category of
    addressable content is how it concludes something is not in the
    corpus when it is.
    """
    sections = sorted((sid for sid in by_id if not is_block_id(sid)), key=_natural_section_key)
    blocks = sum(1 for sid in by_id if is_block_id(sid))
    listed = ", ".join(f"§ {sid}" for sid in sections)
    if blocks:
        suffix = f"{blocks} non-§ content block(s) — call list_sections to see them"
        return f"{listed}; {suffix}" if listed else suffix
    return listed or "(no sections in this act)"


def _sections_by_id(sections: list[ParsedSection]) -> dict[str, list[ParsedSection]]:
    """Group parsed sections by id, preserving document order within each id.

    A one-element list is the normal case. A longer one means the id repeats,
    and the caller must disambiguate rather than pick.
    """
    grouped: dict[str, list[ParsedSection]] = {}
    for section in sections:
        grouped.setdefault(section["section_id"], []).append(section)
    return grouped


def _select_occurrence(
    slug: str,
    section_id: str,
    matches: list[ParsedSection],
    occurrence: int | None,
) -> ParsedSection:
    """Pick one section from the occurrences sharing ``section_id``.

    With no ``occurrence`` the unique case resolves silently (the 99.9% path).
    An ambiguous id raises rather than guessing, and the message carries every
    candidate so an AI caller can re-ask correctly in one round trip instead of
    unknowingly quoting the wrong provision.
    """
    if occurrence is None:
        if len(matches) == 1:
            return matches[0]
        options = "; ".join(
            f"occurrence={m['occurrence']}: {m['heading']}"
            + (f" [{m['parent_chapter']}]" if m["parent_chapter"] else "")
            for m in matches
        )
        raise CorpusAmbiguousSectionError(
            f"section {section_id!r} is ambiguous in {slug!r}: {len(matches)} sections "
            f"share that id — {options}. Re-call with occurrence=N to choose one.",
        )
    for match in matches:
        if match["occurrence"] == occurrence:
            return match
    raise CorpusNotFoundError(
        f"occurrence {occurrence} of section {section_id!r} not found in {slug!r}; "
        f"it has {len(matches)} (valid: 1-{len(matches)})",
    )


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


def _section_text(data: ParsedSection) -> str:
    """Heading line + body as one block.

    Diffing the heading together with the body means a retitle (the section
    keeps its id and body but its ``§ N. Title`` changes) surfaces as a
    change — a body-only comparison would silently miss that legal edit.
    """
    body = data["body"]
    return f"{data['heading']}\n{body}" if body else data["heading"]


def _classify_section_change(
    before: ParsedSection | None,
    after: ParsedSection | None,
) -> str | None:
    """Return ``added`` / ``removed`` / ``changed``, or ``None`` if identical."""
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "changed" if _section_text(before) != _section_text(after) else None


def _pair_occurrences(
    before: list[ParsedSection],
    after: list[ParsedSection],
) -> list[tuple[ParsedSection | None, ParsedSection | None]]:
    """Align the occurrences that share one ``section_id`` across two versions.

    ``occurrence`` is a POSITION, not a stable identity: delete the first of two
    ``§ 6-2`` and the survivor renumbers from 2 to 1. Pairing on the number alone
    therefore diffs the surviving section against the deleted one's text and
    reports a bogus changed+removed instead of a single removal.

    So align on content and fall back to position only for what is left over:
    identical sections pair first, then same-heading (a body edit), then whatever
    remains in document order (a retitle). Unmatched leftovers are the genuine
    additions and removals.
    """
    unmatched_a, unmatched_b = list(before), list(after)
    pairs: list[tuple[ParsedSection | None, ParsedSection | None]] = []  # pragma: no mutate
    for key in (_section_text, lambda section: section["heading"]):
        for candidate in list(unmatched_b):
            match = next((s for s in unmatched_a if key(s) == key(candidate)), None)
            if match is not None:
                unmatched_a.remove(match)
                unmatched_b.remove(candidate)
                pairs.append((match, candidate))
    while unmatched_a and unmatched_b:
        pairs.append((unmatched_a.pop(0), unmatched_b.pop(0)))
    pairs.extend((section, None) for section in unmatched_a)
    pairs.extend((None, section) for section in unmatched_b)
    return pairs


def _diff_section_maps(
    sections_a: list[ParsedSection],
    sections_b: list[ParsedSection],
) -> dict[str, Any]:
    """Section-by-section diff of two parsed section lists (``_parse_sections``
    output). Sections present in both with identical heading+body are omitted;
    every other one yields an entry carrying a stdlib unified diff. Entries are
    emitted in natural section order so the output is byte-stable for a given
    pair of inputs.

    Sections are grouped by ``section_id`` and then aligned within that group by
    ``_pair_occurrences`` — an act with two ``§ 6-2`` has two sections to diff,
    and keying by id alone would collapse them and hide a change to the first.
    """
    by_id_a, by_id_b = _sections_by_id(sections_a), _sections_by_id(sections_b)
    summary = {"sections_added": 0, "sections_removed": 0, "sections_changed": 0}
    entries: list[dict[str, Any]] = []
    for sid in by_id_a.keys() | by_id_b.keys():
        for before, after in _pair_occurrences(by_id_a.get(sid, []), by_id_b.get(sid, [])):
            change = _classify_section_change(before, after)
            if change is None:
                continue
            summary[f"sections_{change}"] += 1
            source = after if after is not None else before
            entries.append(
                {
                    "section_id": sid,
                    "occurrence": source["occurrence"] if source is not None else 1,
                    "heading": source["heading"] if source is not None else "",
                    "change_type": change,
                    "unified_diff": _section_unified_diff(before, after),
                },
            )
    entries.sort(key=lambda e: (_natural_section_key(e["section_id"]), e["occurrence"]))
    return {"summary": summary, "sections": entries}


def _section_unified_diff(
    before: ParsedSection | None,
    after: ParsedSection | None,
) -> str:
    """Deterministic unified diff between two section-text blocks; an absent
    side (added / removed) diffs against the empty document."""
    before_text = _section_text(before) if before is not None else ""
    after_text = _section_text(after) if after is not None else ""
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        ),
    )


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
        f"wording, use search_body for exact keywords, or lower min_score. "
        f"{CORPUS_SCOPE_NOTE}"
    )


def _table_row_bounds(body: str, match_idx: int) -> tuple[int, int] | None:
    """Bounds of the Markdown table row containing ``match_idx``, if any."""
    start = body.rfind("\n", 0, match_idx) + 1
    if not body.startswith("|", start):
        return None
    end = body.find("\n", match_idx)
    return start, len(body) if end < 0 else end


def _snippet(body: str, match_idx: int, match_len: int) -> str:
    """Extract a ``~100`` char window around ``match_idx`` in ``body``.

    Whitespace (including newlines) is collapsed to single spaces so
    the snippet renders as a single readable line in the AI's response.
    Adds leading ``...`` if not at the start, trailing ``...`` if not
    at the end, so the AI can see the snippet is a fragment.

    A match inside a Markdown table row returns the WHOLE row instead.
    In a table the queried term is in one column and the answer is in
    another — searching takstforskriften for ``jobbmestring`` matched
    the description column of the ``2j`` row and cut off 550 characters
    before the fee. The hit was real, the snippet carried none of the
    payload, and a hit that looks like coverage while withholding the
    answer is worse than a miss: nothing prompts a follow-up call.
    Table rows are the one place in the corpus where a fixed character
    window is systematically the wrong unit.
    """
    row = _table_row_bounds(body, match_idx)
    if row is not None:
        start, end = row
        return " ".join(body[start:end].split())[:_TABLE_ROW_SNIPPET_CHARS]
    start = max(0, match_idx - _SNIPPET_CONTEXT_CHARS)
    end = min(len(body), match_idx + match_len + _SNIPPET_CONTEXT_CHARS)
    snippet = " ".join(body[start:end].split())
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{snippet}{suffix}"


def _original_offsets(body: str) -> list[int]:
    """Map every offset in ``body.lower()`` back to its offset in ``body``.

    ``str.lower`` is not length-preserving: ``"\\u0130".lower()`` is two
    characters, so a single Turkish dotted capital I earlier in a document
    shifts every later match one position right in the lowercased copy.

    U+0130 is the ONLY code point that does this — brute-forced over all
    1,114,112 of them on CPython 3.12: one expands, none collapse. So this
    translation covers the whole class rather than the one character it was
    found with, and a future Unicode revision is the only thing that could
    widen it.
    Slicing the original at that shifted offset cuts the snippet window in
    the wrong place — silently, since the match itself is still found and
    ``match_count`` stays right.

    Not hypothetical. One document in the renderer-5 corpus does this:
    ``sanksjonsforskrift-ukraina-territoriell-integritet-mv`` carries 27 of
    them in the Turkish addresses of sanctioned entities, so every hit past
    the first one had its window shifted by up to 27 characters — in the one
    act where reading an address correctly matters most.

    Built only when the two lengths actually differ, so the other 5,912
    documents pay a length comparison and nothing else.
    """
    offsets = [index for index, char in enumerate(body) for _ in char.lower()]
    offsets.append(len(body))
    return offsets


def _body_match_summary(
    body: str,
    needle: str,
    tolerant: re.Pattern[str] | None,
) -> tuple[int, str] | None:
    """Occurrences of ``needle`` in ``body`` plus a snippet of the first.

    ``None`` when the body does not contain the query at all.

    Bodies carrying no footnote marker keep the plain substring scan:
    on those the tolerant pattern finds the very same spans — every
    optional group fails — at a fraction of the speed, and 81.7% of the
    body index (4,832 of 5,913 documents) carries no marker. The
    snippet window is measured from the matched span, not from
    ``len(needle)``, so a match the marker lengthened is not cut short.

    ``tolerant`` is ``None`` for a query too long to be worth compiling
    a pattern for (``_MAX_MARKER_TOLERANT_QUERY``).

    Matching happens in the lowercased copy but the snippet is cut from
    the original, so the offsets have to be translated back whenever
    lowercasing changed the length (see ``_original_offsets``).
    """
    haystack = body.lower()
    if tolerant is None or _FOOTNOTE_MARKER_OPENER not in haystack:
        count = haystack.count(needle)
        if count == 0:
            return None
        first = haystack.find(needle)
        start, end = first, first + len(needle)
    else:
        spans = [match.span() for match in tolerant.finditer(haystack)]
        if not spans:
            return None
        count = len(spans)
        start, end = spans[0]
    if len(haystack) != len(body):
        offsets = _original_offsets(body)
        start, end = offsets[start], offsets[end]
    return count, _snippet(body, start, end - start)


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
        last_known: re.Match[str] | None = None  # pragma: no mutate
        for token_match in _SLUG_TOKEN_PATTERN.finditer(between):
            if token_match.group(0) in known_slugs:
                last_known = token_match
        if last_known is None:
            continue
        if between[last_known.end() :].strip() == "":
            owners[i - 1] = prev.end() + last_known.start()
    return owners


def _verdict(
    section_id: str,
    target_slug: str,
    index: SectionIndex,
    *,
    known: bool,
) -> tuple[bool | None, str | None]:
    """Decide whether a reference is valid, invalid, or unverifiable.

    ``None`` is not a third flavour of failure — it is the honest answer
    when the evidence cannot support either verdict. A cross-reference is
    reported invalid only when the target act's section index is known to
    be complete; if the act contains a heading the grammar could not
    parse, the id may well be in the act and simply absent from the index,
    and saying ``false`` would be asserting something unproven.

    That distinction is the whole value of the field. Missing data is
    visible to whoever reads the result; a wrong ``valid: false`` reads as
    a verified denial and is acted on as one.
    """
    if not known:
        return False, f"slug {target_slug!r} unknown"
    if section_id in index.ids:
        return True, None
    if not index.complete:
        return None, (
            f"§ {section_id} is not in the parsed section index of {target_slug!r}, "
            "and that index is incomplete — the act contains at least one heading "
            "this parser cannot read, so absence here is not evidence of absence"
        )
    return False, f"§ {section_id} not found in {target_slug!r}"


def _extract_cross_references(
    body: str,
    current_slug: str,
    known_slugs: set[str],
    section_index: Callable[[str], SectionIndex],
) -> list[dict[str, Any]]:
    """Extract validated cross-references from a section body.

    Walks the body for ``§ N-M`` patterns. For each match, scans a
    ``±_CROSS_REF_CONTEXT_CHARS`` window for slug tokens; if a token
    matches a current manifest slug different from ``current_slug``,
    the reference is treated as cross-act. Otherwise it is treated
    as a same-act reference (validated against ``current_slug``'s
    section set).

    ``known_slugs`` is the full set of current manifest slugs (cheap
    — no body parsing). ``section_index`` resolves one slug to its
    section-id set lazily, so only acts actually referenced get
    parsed, and carries whether that set is complete.

    Output is deduplicated by ``(target_slug, target_section_id)``
    so a section that mentions ``§ 5-12`` three times produces one
    entry. The first occurrence's verbatim ``text`` is kept.

    Output entries: ``text`` (verbatim ``§ N-M`` substring as it
    appears in the body), ``target_slug`` (resolved act, defaults
    to ``current_slug`` when no other slug appears in the window),
    ``target_section_id`` (the parsed id), ``valid`` (``True`` the
    target section exists, ``False`` it provably does not, ``None``
    the target act's index is incomplete so neither can be shown),
    ``reason`` (null when valid; otherwise human-readable).

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
        known = target_slug in known_slugs
        index = section_index(target_slug) if known else SectionIndex(ids=set(), complete=True)
        valid, reason = _verdict(section_id, target_slug, index, known=known)
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
    no information gain. Spacing and case inside the id are folded too
    (``"§ 8-7 a"`` and ``"8-7a"`` name one section), which is what makes
    a caller's citation match the corpus's own link targets. Anything
    beyond a section id (chapter words, ``ledd`` qualifiers) is left
    untouched and fails the lookup with the available-ids message.
    """
    return canonical_section_id(section_id)


# ---- state-agnostic primitive cores (ADR-0011) -------------------------
#
# The four retrieval primitives must give the same answer whether they
# read the live working tree or a resolved historical corpus state
# (snapshot closure, ADR-0011 point 5). These cores hold the one copy of
# each algorithm; the live reader and the snapshot state both feed them
# their own lookups. Anything reader-specific (epoch caches, disk reads,
# git blobs) stays OUT of here.


def _section_from_body(
    slug: str,
    body: str,
    section_id: str,
    occurrence: int | None,
) -> tuple[ParsedSection, dict[str, list[ParsedSection]]]:
    """Locate ``§ section_id`` in one act's body, or raise with the act's
    available ids — the recovery message both states share."""
    by_id = _sections_by_id(_parse_sections(body))
    matches = by_id.get(section_id)
    if not matches:
        raise CorpusNotFoundError(
            f"section {section_id!r} not found in {slug!r}; "
            f"available: {_available_ids_message(by_id)}",
        )
    return _select_occurrence(slug, section_id, matches, occurrence), by_id


def _section_result(
    slug: str | None,
    section_id: str,
    section: ParsedSection,
    cross_references: list[dict[str, Any]],
) -> dict[str, Any]:
    """The ``get_section`` response shape, shared by both states."""
    return {
        "slug": slug,
        "section_id": section_id,
        "occurrence": section["occurrence"],
        "heading": section["heading"],
        "parent_chapter": section["parent_chapter"],
        "layer": section["layer"],
        "body": section["body"],
        "cross_references": cross_references,
    }


def _cross_references_for(
    section_body: str,
    slug: str,
    known_slugs: set[str],
    section_index_for: Callable[[str], SectionIndex],
) -> list[dict[str, Any]]:
    """Cross-references of one section, validated against the SAME state
    the section came from — the caller supplies that state's slug set and
    section indexes, never a mix (ADR-0011 point 5)."""
    if not _CROSS_REF_SECTION.search(section_body):
        return []
    return _extract_cross_references(section_body, slug, known_slugs, section_index_for)


def _section_index_from_body(body: str) -> SectionIndex:
    """Parse one act's body into its ``SectionIndex``.

    Snapshot counterpart of the live reader's cached
    ``_section_ids_for`` / ``_section_index_is_complete`` pair — same
    parse, same completeness rule, no cache to go stale.
    """
    ids = {section["section_id"] for section in _parse_sections(body)}
    complete = not any(
        _LOOKS_LIKE_SECTION_HEADING.match(line) and not _SECTION_HEADING.match(line)
        for line in body.split("\n")
    )
    return SectionIndex(ids=ids, complete=complete)


def _slug_suggestions_from(slugs: Iterable[str], slug: str) -> list[str]:
    """Up to three near-miss canonical slugs from one state's namespace."""
    return difflib.get_close_matches(slug.lower(), list(slugs), n=3, cutoff=0.6)


def _citation_hint_from(slugs: Collection[str], citation_lower: str) -> str:
    """Near-miss hint for a citation whose act token matched nothing,
    drawn from one state's slug namespace."""
    suggestions: list[str] = []
    for token in _SLUG_TOKEN_PATTERN.findall(citation_lower):
        if len(token) < _MIN_SUGGESTION_TOKEN_CHARS:
            continue
        for match in _slug_suggestions_from(slugs, token):
            if match not in suggestions:
                suggestions.append(match)
    if not suggestions:
        return ""
    return f"; did you mean {', '.join(suggestions[:3])}? Use search_laws for canonical slugs"


def _citation_invalid(
    reason: str,
    slug: str | None = None,
    section_id: str | None = None,
) -> dict[str, Any]:
    """An invalid ``validate_citation`` verdict, in the pinned shape."""
    return {
        "valid": False,
        "slug": slug,
        "section_id": section_id,
        "heading": None,
        "reason": reason,
    }


def _citation_verdict(
    citation: str,
    slugs: Collection[str],
    resolve: Callable[[str, str], tuple[str, dict[str, Any] | None, str | None]],
) -> dict[str, Any]:
    """The whole ``validate_citation`` algorithm against one state.

    ``slugs`` is that state's slug namespace; ``resolve`` is that state's
    cited-section resolution (the ``_resolve_cited_section`` contract).
    """
    if "§§" in citation:
        return _citation_invalid(
            "range citations (§§) are not supported; cite a single § section instead",
        )
    section_match = _CITATION_SECTION_ID.search(citation)
    section_id = section_match.group(1) if section_match else None
    citation_lower = citation.lower()
    candidates = [slug for slug in slugs if _slug_token_in_citation(slug, citation_lower)]
    matched_slug = max(candidates, key=len) if candidates else None
    if matched_slug is None and section_id is None:
        return _citation_invalid(
            f"could not parse citation {citation!r}: no § id and no known slug found"
            f"{_citation_hint_from(slugs, citation_lower)}",
        )
    if matched_slug is None:
        return _citation_invalid(
            f"ambiguous citation: § {section_id} found but no act "
            f"identifier; many acts have a section by that id"
            f"{_citation_hint_from(slugs, citation_lower)}",
            section_id=section_id,
        )
    if section_id is None:
        return {
            "valid": True,
            "slug": matched_slug,
            "section_id": None,
            "heading": None,
            "reason": None,
        }
    resolved_id, section, reason = resolve(matched_slug, section_id)
    if section is None:
        return _citation_invalid(reason or "", slug=matched_slug, section_id=resolved_id)
    return {
        "valid": True,
        "slug": matched_slug,
        "section_id": resolved_id,
        "heading": section["heading"],
        "reason": None,
    }


def _resolve_cited_section_with(
    get_section: Callable[[str, str], dict[str, Any]],
    slug: str,
    section_id: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Resolve a citation-extracted id via one state's ``get_section``.

    Same longest-read-first / `` i``-tail fallback contract as the live
    ``_resolve_cited_section`` (whose docstring holds the reasoning);
    parameterized on the section lookup so a snapshot resolves against
    its own state.
    """
    try:
        return section_id, get_section(slug, section_id), None
    except CorpusAmbiguousSectionError as exc:
        return section_id, None, str(exc)
    except CorpusNotFoundError as exc:
        stripped = _CITATION_PROSE_TAIL.sub("", section_id)
        if stripped == section_id:
            return section_id, None, str(exc)
        try:
            return stripped, get_section(slug, stripped), None
        except CorpusAmbiguousSectionError as ambiguous:
            return stripped, None, str(ambiguous)
        except CorpusNotFoundError:
            return stripped, None, str(exc)


class _QuoteQuery(NamedTuple):
    """One ``verify_quote`` request, bundled to travel as a unit."""

    slug: str
    section_id: str
    quote: str
    occurrence: int | None


def _quote_result(
    verified: bool,
    slug: str,
    section_id: str,
    reason: str | None,
) -> dict[str, Any]:
    """The ``verify_quote`` response shape, shared by both states."""
    return {
        "verified": verified,
        "slug": slug,
        "section_id": section_id,
        "reason": reason,
    }


def _quote_verdict(
    query: _QuoteQuery,
    get_section: Callable[[str, str, int | None], dict[str, Any]],
) -> dict[str, Any]:
    """The whole ``verify_quote`` algorithm against one state's sections."""
    section_id = _normalize_section_id(query.section_id)
    if not query.quote.strip():
        return _quote_result(False, query.slug, section_id, "quote is empty")
    try:
        section = get_section(query.slug, section_id, query.occurrence)
    except (CorpusAmbiguousSectionError, CorpusNotFoundError) as exc:
        return _quote_result(False, query.slug, section_id, str(exc))
    section_normalized = _normalize_for_quote_match(section["body"])
    quote_normalized = _normalize_for_quote_match(query.quote)
    if quote_normalized in section_normalized:
        return _quote_result(True, query.slug, section_id, None)
    return _quote_result(
        False,
        query.slug,
        section_id,
        f"quote not found in § {section_id} of {query.slug!r} after case, "
        f"whitespace and typographic-punctuation normalization. The quote "
        f"may be from a different section, paraphrased rather than "
        f"verbatim, or hallucinated. "
        f"Call get_section({query.slug!r}, {section_id!r}) to read the actual text.",
    )


def _iter_search_docs(
    documents: dict[str, ManifestRecord],
    slug_index: dict[str, tuple[str, ManifestRecord]],
    body_for: Callable[[str], str | None],
) -> Iterator[tuple[str, ManifestRecord, str]]:
    """Yield ``(doc_id, record, body)`` for one state's searchable docs.

    Applies the shared eligibility rules — current status, a slug, the
    slug-index owner on duplicates (otherwise one body reports once per
    claiming record), a body the state can actually produce.
    """
    for doc_id, record in documents.items():
        if record.status != "current" or record.slug is None:
            continue
        if slug_index.get(record.slug, (doc_id, record))[0] != doc_id:
            continue
        body = body_for(record.slug)
        if body is None:
            continue
        yield doc_id, record, body


def _search_body_hits(
    query: str,
    docs: Iterator[tuple[str, ManifestRecord, str]],
    dataset: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """The whole ``search_body`` algorithm over one state's doc stream."""
    limit = _bounded_limit(limit)
    if not query.strip():
        return []
    needle = query.lower()
    tolerant = _marker_tolerant_query(needle) if len(needle) <= _MAX_MARKER_TOLERANT_QUERY else None
    dataset_key = _resolve_dataset(dataset) if dataset is not None else None
    results: list[dict[str, Any]] = []
    for doc_id, record, body in docs:
        if dataset_key is not None and record.source_dataset != dataset_key:
            continue
        summary = _body_match_summary(body, needle, tolerant)
        if summary is None:
            continue
        count, snippet = summary
        results.append(
            {
                "slug": record.slug,
                "doc_id": doc_id,
                "title": record.title,
                "dataset": _subdir_for_dataset(record.source_dataset),
                "match_count": count,
                "snippet": snippet,
            },
        )
    results.sort(key=lambda hit: (-hit["match_count"], hit["slug"] or ""))
    return results[:limit]


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

_FOOTNOTE_REFERENCE_RE = re.compile(r"\[\^[^\]\s]{1,16}\]")
"""A rendered footnote marker (``[^1]``), dropped before quote matching.

The marker is the renderer's own annotation, not words of the statute: it sits
mid-sentence, flush against the word it annotates, and no one quoting the law
reproduces it. Leaving it in makes an honest quote fail — the state this guard
must never reach, because a guard that rejects real text teaches the AI to stop
calling it. Verified on the live corpus before renderer 5: the faithful quote of
``§ 8`` in ``lov-om-omordning-af-det-civile-embedsverk`` came back
``verified: false`` while only the marker-carrying variant passed.

Dropped rather than folded to a space: the marker interrupts the sentence
without being a word boundary of it, so removing it rejoins the text exactly as
the statute reads. Bounded and whitespace-free so the pattern cannot swallow a
span of real legal text between two unrelated brackets."""

_FOOTNOTE_MARKER_OPENER = "[^"
"""Cheap precondition for the marker-tolerant scan: no ``[^`` in a body means
no marker can be interrupting a phrase in it."""

_OPTIONAL_FOOTNOTE_REFERENCE = f"(?:{_FOOTNOTE_REFERENCE_RE.pattern})?"

_MAX_MARKER_TOLERANT_QUERY = 2000
"""Above this many characters a query is matched literally, as before.

The tolerant pattern costs ~25 bytes of regex per query character, so an
uncapped query hands a caller of this public server a linear multiplier on
compile time: 20,000 characters turn a 0.55 s call into 1.55 s, and the
growth does not stop there. 2,000 characters is an order of magnitude past
any phrase someone searches for and still compiles in ~35 ms."""


def _marker_tolerant_query(needle: str) -> re.Pattern[str]:
    """Compile ``needle`` so a footnote marker may interrupt it anywhere.

    ``search_body`` is the same defect class as ``verify_quote`` was:
    the renderer writes the marker flush against the word it annotates
    (``tidspunktet[^1] Kongen``), nobody searching for that sentence
    types it, and a plain substring scan then loses the phrase without
    saying so. Measured on the renderer-5 body index (5,913 documents):
    11,839 markers in 1,081 of them, and the character after a marker
    is whitespace 10,131 times, ``,`` 728, ``.`` 511.

    The optional marker group goes between every pair of adjacent
    characters, not only at word boundaries. Exactly one marker in the
    corpus splits a word (``HORRAT[^\\*]R``, a table cell), and a rule
    covering the other 11,838 but not that one would be arbitrary
    rather than narrow.
    """
    return re.compile(_OPTIONAL_FOOTNOTE_REFERENCE.join(map(re.escape, needle)))


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
    folded = _FOOTNOTE_REFERENCE_RE.sub("", folded)
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
    """Build the configured embedding adapter, or return None if unusable.

    Configuration comes from :meth:`EmbeddingConfig.from_env`, the same
    resolution the engine uses — including the credential env-var fallbacks
    this function used to re-implement provider-side knowledge of.

    Returns None rather than raising in every failure mode, because the server
    hosts fifteen tools that need no embedding provider at all: a missing
    credential or a misconfigured provider must cost ``semantic_search`` and
    nothing else. The reason is printed once at startup and repeated in
    ``semantic_search``'s own error, so it is not lost in a scrollback.

    The adapter gets an interactive timeout/retry budget so a hung provider
    node cannot freeze the stdio server for minutes."""
    try:
        config = EmbeddingConfig.from_env()
        embedder = create_embedder(
            config,
            timeout_seconds=_MCP_EMBED_TIMEOUT_SECONDS,
            max_retries=_MCP_EMBED_MAX_RETRIES,
        )
    except ConfigError as exc:
        print(
            f"lovspor mcp: embedding provider not configured: {exc} "
            "semantic_search will be disabled; the other fifteen tools work normally.",
            file=sys.stderr,
            flush=True,
        )
        return None
    if embedder is None:
        print(
            "lovspor mcp: OPENAI_API_KEY not set; semantic_search will be disabled "
            "but the other fifteen tools work normally. Set OPENAI_API_KEY "
            "and restart to enable semantic search.",
            file=sys.stderr,
            flush=True,
        )
    return embedder


@final
class HttpConfig(BaseModel):
    """Bind address and access control for the Streamable HTTP transport.

    Passing this to :func:`build_server` selects hosted mode: tool bodies are
    offloaded to worker threads and the corpus indices are warmed before the
    server accepts traffic. Omit it for stdio, which stays lazy and inline.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    # None disables authentication entirely, which serve_http refuses unless
    # allow_insecure is also set. The default is "no credentials configured",
    # so forgetting the flag fails to start rather than silently serving the
    # whole corpus surface to anyone who can reach the port.
    credentials_path: Path | None = None
    allow_insecure: bool = False
    # WorkOS AuthKit as the rented OAuth authorization server for self-service
    # connectors. Both set => hosted-OAuth mode: verify WorkOS-issued JWTs and
    # advertise WorkOS via RFC 9728 discovery. Neither set => opaque-token,
    # resource-server-only mode (developer clients paste a token). One without the
    # other is rejected outright, see the validator below. ``public_url`` is
    # lovspor's own ``/mcp`` URL — the RFC 8707 resource the token is bound to.
    authkit_domain: str | None = None
    public_url: str | None = None

    @model_validator(mode="after")
    def _reject_half_configured_oauth(self) -> Self:
        """Refuse one of the OAuth pair without the other, at construction.

        Half a config used to boot: the server silently fell back to the legacy
        opaque-token mode with discovery off and WorkOS JWTs never verified, so a
        typo'd deploy unit looks healthy while every self-service connector fails
        to authenticate. An auth boundary must not degrade quietly.

        This catches the mistake early — at CLI parse, before anything binds — but
        it is not the only guard; see :meth:`oauth_pair`.
        """
        self.oauth_pair()
        return self

    def oauth_pair(self) -> tuple[str, str] | None:
        """``(issuer, resource)`` when hosted OAuth is on, ``None`` for opaque mode.

        Raises :class:`ConfigError` on a half-configured pair. Re-checking here
        rather than trusting the validator is deliberate: pydantic's non-validating
        escape hatches (``model_construct``, ``model_copy(update=...)``, plain
        attribute assignment) all skip validators, and any of them could hand a
        half pair straight to the callers below. This is the one place that decides
        the mode and both callers consult it before anything else, so:

            for any ``HttpConfig`` whose ``oauth_pair`` attribute still resolves to
            ``HttpConfig.oauth_pair``, exactly one non-empty OAuth field raises
            ``ConfigError`` — including when authentication is disabled.

        That qualifier is load-bearing. The guard is an ordinary method, so it is
        both virtual (a subclass can override it) and shadowable (``model_copy``
        will write a callable straight over the attribute). ``@final`` makes the
        subclass case a mypy error; nothing makes the shadowing case impossible.
        Neither is reachable from CLI flags or env vars, and both require
        deliberately replacing the guard — the marker exists so the invariant
        cannot be lost by accident, not so it cannot be defeated on purpose.
        """
        issuer, resource = self.authkit_domain, self.public_url
        if bool(issuer) != bool(resource):
            raise ConfigError(
                "hosted OAuth needs --authkit-domain and --public-url together "
                "(LOVSPOR_AUTHKIT_DOMAIN / LOVSPOR_PUBLIC_URL). Pass both to enable "
                "self-service connectors, or neither for opaque-token mode.",
            )
        if not issuer or not resource:
            return None
        return issuer, resource


def _auth_kwargs(bind: HttpConfig, verifier: TokenVerifier | None) -> dict[str, Any]:
    """FastMCP auth kwargs for a bind config, or empty when unauthenticated.

    ``auth`` and ``token_verifier`` must be passed together or not at all — the
    SDK raises on either alone. Two shapes:

    * **Hosted OAuth** (``authkit_domain`` + ``public_url`` set): advertise WorkOS
      as the authorization server (``issuer_url``) and lovspor's ``/mcp`` URL as
      the RFC 9728 resource (``resource_server_url``). That turns the
      ``.well-known/oauth-protected-resource`` discovery route ON, so ChatGPT and
      Claude find WorkOS and run the OAuth flow; ``verifier`` validates the
      WorkOS-issued JWTs they then present.
    * **Opaque token, RS-only** (neither set): a bare ``token_verifier`` — no
      authorization server, no ``/token`` / ``/authorize`` / discovery routes,
      just RequireAuth over ``/mcp``. ``resource_server_url=None`` keeps
      ``.well-known`` off; the issuer is inert but must be a valid URL for the SDK.
    """
    # Before the unauthenticated early return, not after: a half pair is a broken
    # config whether or not auth is switched on, and it should read the same way
    # in both cases rather than only being caught when a verifier happens to exist.
    pair = bind.oauth_pair()
    if verifier is None:
        return {}
    if pair is not None:
        issuer, resource = pair
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=None,
        )
    else:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(f"http://{bind.host}:{bind.port}/"),
            resource_server_url=None,
            required_scopes=None,
        )
    return {"token_verifier": verifier, "auth": auth}


def _build_verifier(
    bind: HttpConfig, store: CredentialStore | None
) -> tuple[TokenVerifier | None, LimitsSource | None]:
    """Return ``(token_verifier, limits_source)`` for a bind config.

    Hosted-OAuth mode wraps the credential store in a :class:`CompositeVerifier`
    so WorkOS JWT connectors and opaque developer tokens both authenticate, and
    self-service WorkOS users meter against a shared default quota. Otherwise the
    store is both verifier and limits source, or ``None`` when running insecure.
    """
    # Checked before the insecure early return — see the note in _auth_kwargs.
    pair = bind.oauth_pair()
    if store is None:
        return None, None
    if pair is not None:
        issuer, resource = pair
        composite = CompositeVerifier(
            credentials=store,
            workos=WorkOSTokenVerifier(
                authkit_domain=issuer,
                resource_url=resource,
            ),
            workos_default_limits=DEFAULT_WORKOS_LIMITS,
        )
        return composite, composite
    return store, store


def _offload_to_thread(fn: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    """Wrap a synchronous tool body as an async tool run on a worker thread.

    mcp 1.27.0 calls a synchronous tool handler inline on the single event-
    loop thread, so one blocking call — a cold-cache ``search_body``, a
    ``semantic_search`` embedding round-trip, a ``git`` subprocess — would
    stall every other client on an HTTP server. Offloading to a thread lets
    the loop serve other requests while the body runs. ``functools.wraps``
    preserves ``__wrapped__`` so FastMCP still derives the tool's argument
    schema from the original signature; the wrapper is ``async`` so FastMCP
    awaits it instead of calling it inline.
    """

    @functools.wraps(fn)
    async def wrapper(**kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, **kwargs)

    return wrapper


def _with_quota(
    fn: Callable[..., Awaitable[Any]], enforcer: QuotaEnforcer
) -> Callable[..., Awaitable[Any]]:
    """Charge a tool call against its credential's limits before it runs.

    Wraps *outside* ``_offload_to_thread`` on purpose: the guard then refuses a
    throttled call on the event loop, before it can take one of the shared
    pool's worker threads. Inverting the two would make ``max_in_flight`` — the
    brake that exists to protect exactly that pool — occupy a thread in order to
    decide it should not have one.
    """

    @functools.wraps(fn)
    async def wrapper(**kwargs: Any) -> Any:
        token = get_access_token()
        if token is None:
            # Unreachable while RequireAuthMiddleware fronts /mcp, and fatal if
            # it ever is: an unidentified caller cannot be metered, so refuse
            # rather than serve one client's quota to everybody.
            raise QuotaExceededError("request carries no identified credential", 1)
        with enforcer.guard(token.client_id):
            return await fn(**kwargs)

    return wrapper


_MAX_SNAPSHOT_STATES = 4
"""Bound on cached historical states. Each holds a parsed manifest and,
after a historical ``search_body``, the state's whole body set (~200 MB on
the production corpus) — a handful covers a session revisiting the same
dates; an unbounded map would let a date-scanning client hold every state
ever asked for."""


def _parse_recorded_at(value: str) -> date:
    """Parse and bound an ADR-0011 ``recorded_at`` value.

    ISO calendar date, end-of-day semantics downstream; future dates are
    refused — same typo-guard posture as ``get_law_at``'s ``target_date``.
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"recorded_at must be ISO date YYYY-MM-DD, got {value!r}",
        ) from exc
    today = datetime.now(UTC).date()
    if parsed > today:
        raise ValueError(
            f"recorded_at {parsed.isoformat()} is in the future "
            f"(today is {today.isoformat()}); "
            f"omit recorded_at for the current corpus state",
        )
    return parsed


@dataclass
class _SnapshotData:
    """Per-commit caches shared by every ``recorded_at`` resolving to one state.

    Split from :class:`_SnapshotState` so two different dates that resolve
    to the same commit share the expensive parts (manifest, bodies) while
    each call still stamps the date it was actually asked for.
    """

    snapshot: CorpusSnapshot
    ref: CorpusStateRef
    bodies: dict[str, str | None] = field(default_factory=dict)
    section_indexes: dict[str, SectionIndex] = field(default_factory=dict)
    search_bodies: dict[str, str] | None = None


class _SnapshotState:
    """The four retrieval primitives against one resolved historical state.

    Snapshot closure (ADR-0011 point 5): every lookup an answer depends on
    — slug namespace, bodies, section indexes, citation resolution,
    suggestions — reads the same resolved commit, never the working tree.
    The algorithms themselves are the shared state-agnostic cores, so a
    historical answer can differ from a live one only by state.

    Answers are corpus statements, never legal-validity statements; no
    ``in_force_at`` evaluation happens here (ADR-0011 points 7 and 9).
    """

    def __init__(self, data: _SnapshotData, recorded_at: date) -> None:
        self._data = data
        self._recorded_at = recorded_at

    @property
    def corpus_commit(self) -> str:
        """The global state commit — the identity every answer reports."""
        return self._data.ref.sha

    def _slug_index(self) -> dict[str, tuple[str, ManifestRecord]]:
        return self._data.snapshot.slug_index

    def _unknown_slug_error(self, slug: str) -> CorpusNotFoundError:
        """Outcome 3 of the ADR-0011 taxonomy: the state resolved fine and
        the document is not in it — a historical negative, never a
        boundary claim."""
        suggestions = _slug_suggestions_from(self._slug_index(), slug)
        hint = f"did you mean {', '.join(suggestions)}? " if suggestions else ""
        return CorpusNotFoundError(
            f"no law with slug {slug!r} in the corpus state at "
            f"{self._recorded_at.isoformat()} (corpus_commit {self.corpus_commit}); "
            f"{hint}the document may have entered the corpus later — "
            f"call get_law_history({slug!r}) to see when it first appeared",
        )

    def _find_record(self, slug: str) -> ManifestRecord:
        entry = self._slug_index().get(slug)
        if entry is None:
            raise self._unknown_slug_error(slug)
        return entry[1]

    def _body_for_slug(self, slug: str) -> str | None:
        """Stripped body of one act at this state; ``None`` when the state
        has no such document (or its blob is absent from the tree)."""
        bodies = self._data.bodies
        if slug not in bodies:
            entry = self._slug_index().get(slug)
            text = self._data.snapshot.read_text(entry[1].markdown_path) if entry else None
            bodies[slug] = _strip_frontmatter_and_h1(text) if text is not None else None
        return bodies[slug]

    def _section_index_for(self, slug: str) -> SectionIndex:
        indexes = self._data.section_indexes
        if slug not in indexes:
            indexes[slug] = _section_index_from_body(self._body_for_slug(slug) or "")
        return indexes[slug]

    def _stamp(self, result: dict[str, Any], xml_hash: str | None) -> dict[str, Any]:
        """Attach the ADR-0011 evidence fields: the requested date, the
        resolved state, and — only when a document version was actually
        resolved — its ``xml_hash``."""
        result["recorded_at"] = self._recorded_at.isoformat()
        result["corpus_commit"] = self.corpus_commit
        if xml_hash is not None:
            result["xml_hash"] = xml_hash
        return result

    def get_section(
        self,
        slug: str,
        section_id: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
        """``get_section`` against this state — same algorithm, same error
        shapes, cross-references validated against this state's own
        indexes (never the current head)."""
        section_id = _normalize_section_id(section_id)
        record = self._find_record(slug)
        body = self._body_for_slug(slug) or ""
        section, _by_id = _section_from_body(slug, body, section_id, occurrence)
        cross_references = _cross_references_for(
            section["body"],
            record.slug or "",
            set(self._slug_index()),
            self._section_index_for,
        )
        result = _section_result(record.slug, section_id, section, cross_references)
        return self._stamp(result, record.xml_hash)

    def validate_citation(self, citation: str) -> dict[str, Any]:
        """``validate_citation`` against this state's slug namespace and
        sections. ``valid`` means "resolved in the corpus state at the
        requested date" — a corpus statement, not legal validity."""
        verdict = _citation_verdict(citation, self._slug_index(), self._resolve_cited)
        return self._stamp(verdict, None)

    def _resolve_cited(
        self,
        slug: str,
        section_id: str,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        return _resolve_cited_section_with(self.get_section, slug, section_id)

    def verify_quote(
        self,
        slug: str,
        section_id: str,
        quote: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
        """``verify_quote`` against this state's section text. ``verified``
        means the wording appeared in the corpus text at the requested
        date. ``xml_hash`` is stamped only when the document resolved."""
        verdict = _quote_verdict(
            _QuoteQuery(slug, section_id, quote, occurrence),
            self.get_section,
        )
        entry = self._slug_index().get(slug)
        return self._stamp(verdict, entry[1].xml_hash if entry else None)

    def search_body(
        self,
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """``search_body`` against this state, in the ADR-0011 point 8
        envelope — so even an empty result set carries the evidence
        stamp a bare list cannot."""
        docs = _iter_search_docs(
            self._data.snapshot.manifest.documents,
            self._slug_index(),
            self._search_body_lookup,
        )
        hits = _search_body_hits(query, docs, dataset, limit)
        return {
            "recorded_at": self._recorded_at.isoformat(),
            "corpus_commit": self.corpus_commit,
            "results": hits,
        }

    def _search_body_lookup(self, slug: str) -> str | None:
        if self._data.search_bodies is None:
            self._data.search_bodies = self._load_search_bodies()
        return self._data.search_bodies.get(slug)

    def _load_search_bodies(self) -> dict[str, str]:
        """Bulk-read every current doc's body at this state, in one pass.

        Per-file ``git show`` would cost two subprocesses per document —
        ~12,000 for one historical search on the production corpus.
        ``git archive`` streams the whole tree once; members are read in
        memory only, nothing touches the filesystem, so the tar
        path-traversal class (CVE-2007-4559) has no surface here.
        """
        wanted = {
            record.markdown_path: slug for slug, (_doc_id, record) in self._slug_index().items()
        }
        raw = subprocess.run(  # noqa: S603
            ["git", "archive", "--format=tar", self._data.ref.sha],  # noqa: S607
            cwd=self._data.snapshot.repo_path,
            capture_output=True,
            check=True,
        )
        bodies: dict[str, str] = {}
        with tarfile.open(fileobj=BytesIO(raw.stdout)) as tar:
            for member in tar:
                slug = wanted.get(member.name)
                if slug is None or not member.isfile():
                    continue
                blob = tar.extractfile(member)
                if blob is None:
                    continue
                bodies[slug] = _strip_frontmatter_and_h1(blob.read().decode("utf-8"))
        return bodies


def _with_section_notice(result: dict[str, Any], evaluation_date: date) -> dict[str, Any]:
    """Attach the ADR-0009 T0 notice to one ``get_section`` response.

    Serving-side composition per the owner ruling: the corpus bytes and the
    reader stay untouched; the notice is ``g(body, evaluation_date)`` with the
    evaluation date an explicit input. ``None`` when nothing in the section is
    marked not-in-force — absence of the notice is itself a statement, so the
    key is always present.
    """
    notice = build_notice(
        result["body"],
        evaluation_date,
        default_provision=f"§ {result['section_id']}",
    )
    result["temporal_notice"] = None if notice is None else notice.model_dump(mode="json")
    return result


def build_server(corpus_path: Path, *, http: HttpConfig | None = None) -> FastMCP:
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
    if http is not None:
        reader.warm()
    bind = http or HttpConfig()
    store = CredentialStore(bind.credentials_path) if bind.credentials_path else None
    verifier, metering = _build_verifier(bind, store)
    mcp = FastMCP("lovverk", host=bind.host, port=bind.port, **_auth_kwargs(bind, verifier))
    # No store means --allow-insecure: no credential to meter, so no brakes.
    # serve_http already refuses that combination unless it was asked for.
    enforcer = QuotaEnforcer(metering) if metering is not None else None

    def _tool() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a tool, offloading its blocking body to a worker thread
        in hosted mode (see ``_offload_to_thread``) and metering it against the
        caller's credential (see ``_with_quota``). stdio keeps the direct sync
        call: one tool at a time, no thread hop, one local user to meter."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if http is None:
                mcp.add_tool(fn)
                return fn
            hosted = _offload_to_thread(fn)
            mcp.add_tool(hosted if enforcer is None else _with_quota(hosted, enforcer))
            return fn

        return decorator

    @_tool()
    def get_law(slug: str) -> str:
        """Return the full Markdown (frontmatter + body) of a Norwegian law or regulation.

        The slug is the human-readable kortform identifier (e.g.
        ``skatteloven``, ``opplaeringslova``, ``trafikkforskriften``).
        Use ``search_laws`` or ``list_recent_changes`` to discover
        valid slugs.

        Output begins with YAML frontmatter (id, title, ministry,
        dates, license, ...) followed by the legal text in Markdown.

        When the source marks amendments in this act as announced (not
        yet in force), pending a delegated commencement, or never
        brought into force, a **Temporal notice** block is appended
        after the legal text, evaluated against today's date (stated in
        the block). Text listed there is not part of the law in force;
        do not present it as such (ADR-0009 §3b).
        """
        return append_notice(reader.get_law(slug), evaluation_date_today())

    @_tool()
    def get_section(
        slug: str,
        section_id: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
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
        and ``reason`` (null when valid; otherwise a short
        explanation). Use this list to decide whether a referenced
        section is safe to quote without a follow-up
        ``validate_citation`` call.

        ``valid`` is three-valued, and the third value matters:
        ``true`` the target section exists, ``false`` it provably does
        not, ``null`` the target act contains a heading this parser
        cannot read, so its section index is incomplete and neither
        verdict is supported. Treat ``null`` as "check it yourself"
        (``list_sections`` on the target act), never as ``false`` —
        the reference is more likely real than not.

        ``occurrence``: leave this unset. Section ids are almost always
        unique within an act, and the call resolves without it. A handful
        of acts repeat an id — an appendix that restarts its own numbering,
        or two genuinely different ``§ 6-2`` in one chapter — and for those
        the call raises, naming each candidate with its ``occurrence``
        number and parent chapter. Re-call with ``occurrence=N`` to pick
        one. It never guesses: being handed the wrong ``§ 6-2`` and quoting
        it as the law is worse than an error you can recover from.

        Raises if the slug or the section is unknown — the error
        message lists the act's available section ids in natural
        order so the AI can recover without a separate ``get_law``
        call.

        ``temporal_notice`` (always present, usually ``null``) carries
        what the source marks as not in force in THIS section:
        announced amendments, commencement dates that have not arrived
        at the stated ``evaluation_date``, delegated commencement
        (``pending_indeterminate``), or a provision never brought into
        force. Text listed there is not part of the law in force; do
        not present it as such (ADR-0009 §3b).
        """
        return _with_section_notice(
            reader.get_section(slug, section_id, occurrence),
            evaluation_date_today(),
        )

    @_tool()
    def list_sections(slug: str) -> list[dict[str, Any]]:
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

    @_tool()
    def get_law_history(slug: str) -> dict[str, Any]:
        """Return the per-act change history of a law as structured JSON.

        Each event has: date, commit hash, type (added | updated |
        renamed | removed), commit subject, optional from_path / to_path
        (for renames), optional lines_added / lines_removed. Newest
        event first.
        """
        return reader.get_law_history(slug)

    @_tool()
    def get_law_at(slug: str, target_date: str) -> str:
        """Return a law's full Markdown as the corpus recorded it on a date.

        Time-machine companion to ``get_law``: where ``get_law(slug)``
        always returns the current version, ``get_law_at(slug, date)``
        returns the version the corpus held at end-of-day UTC on
        ``date``. Use it to anchor an answer to a specific past corpus
        state — e.g. what this corpus served for an act on 2026-05-01,
        or what a consumer saw before a recent update.

        Temporal contract (ADR-0002): the result represents the version
        available in the Lovspor corpus at the end of the specified UTC
        date. It does not establish which legal provisions were legally
        in force on that date. Corpus history starts when Lovspor first
        recorded the document; earlier states are not retrievable. The
        ``date_in_force`` frontmatter field is descriptive metadata and
        is not used to reconstruct validity history.

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
        available date. On a shallow local checkout whose git history
        does not reach ``target_date``, it raises a distinct
        incomplete-history error instead: the corpus may contain the
        revision even though this clone cannot see it, so the tool
        never claims the law did not exist (ADR-0003).
        """
        return reader.get_law_at(slug, target_date)

    @_tool()
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

        Temporal contract (ADR-0002): listed dates are the UTC dates on
        which the Lovspor corpus recorded a content change — not
        entry-into-force dates. They do not establish when a provision
        became legally applicable, and the list reaches back only to
        when Lovspor first recorded the document.

        Raises if the slug is unknown or if the corpus pre-dates the
        Sprint 5 history layer (no ``history/<slug>.json``).
        """
        return reader.list_law_versions(slug)

    @_tool()
    def diff_law_versions(slug: str, date_a: str, date_b: str) -> dict[str, Any]:
        """Show what changed in a law between two dates, section by section.

        Builds on ``get_law_at`` + ``list_law_versions``: instead of fetching
        one historical version, it compares two. Use it when the user asks
        *"what changed in Skatteloven between 2018 and 2024?"* or wants to see
        the exact edit a reform introduced. Pick the two dates from
        ``list_law_versions`` (or any calendar dates) — each resolves to the
        version current at end-of-day UTC.

        ``slug``: the act's current slug (rename-aware, as for ``get_law_at``).
        ``date_a`` / ``date_b``: ISO ``YYYY-MM-DD``. ``date_a`` is the "before"
        side; a later ``date_a`` gives a reverse diff. Future dates are refused.

        Temporal contract (ADR-0002): the result represents the version
        available in the Lovspor corpus at the end of the specified UTC
        date. It does not establish which legal provisions were legally
        in force on that date. Corpus history starts when Lovspor first
        recorded the document; earlier states are not retrievable. The
        ``date_in_force`` frontmatter field is descriptive metadata and
        is not used to reconstruct validity history.

        Returns ``{slug, date_a, date_b, resolved_commit_a, resolved_commit_b,
        temporal_basis, cutoff_timezone, legal_validity_determined, summary,
        sections}``. ``resolved_commit_a/b`` are the commits the dates
        actually mapped to (a date rarely equals the day the law changed).
        The three temporal markers are machine-readable restatements of the
        contract above: ``temporal_basis`` is always ``"corpus_history"``,
        ``cutoff_timezone`` is always ``"UTC"``, and
        ``legal_validity_determined`` is always ``false``.
        ``summary`` counts ``sections_added`` / ``sections_removed`` /
        ``sections_changed``. ``sections`` lists each affected ``§`` with its
        ``section_id``, ``heading``, ``change_type``, and a unified diff of its
        heading and body; unchanged sections are omitted. Metadata-only churn
        (``retrieved_at``, hashes) is stripped and never appears.

        Raises if the slug is unknown or if either date predates the act's
        first appearance in the corpus (the message points to
        ``get_law_history``). On a shallow local checkout, a date beyond the
        clone's history raises a distinct incomplete-history error rather
        than a claim that the law did not exist (ADR-0003).
        """
        return reader.diff_law_versions(slug, date_a, date_b)

    @_tool()
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

    @_tool()
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

    @_tool()
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
        and a ``snippet``: a ~100 char context window around the FIRST
        match, or — when the match lands inside a Markdown table — the
        whole row, because in a table the term you searched for and the
        value you want sit in different columns. Sorted by match_count
        descending, then by slug.

        A footnote marker in the rendered text (``[^1]``) does not break
        the match: the corpus reads ``tidspunktet[^1] Kongen bestemmer``
        and searching for that phrase without the marker still finds it.
        The snippet is returned as the corpus stores it, marker included.
        Above 2,000 characters the query is matched literally instead,
        so a marker inside a pasted-paragraph query does break it.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` (or the
        full Lovdata key) to restrict the scan.
        ``limit``: max results (default 20). Must be non-negative.

        An empty result does not mean the rule does not exist. This
        corpus is acts and central regulations only — not agency
        circulars (NAV/Helsedirektoratet rundskriv), court or
        Trygderetten practice, forarbeider, or municipal regulations.
        Say what was searched rather than concluding the rule is not
        there; call ``corpus_status`` for the full scope statement.

        Performance note: the body index is loaded lazily on the first
        call and stays resident. Measured on the renderer-5 corpus
        (5,913 docs, 113.5 M chars): ~270 MB peak RSS, ~1 s cold load,
        and every call after that is a full scan of that text —
        ~0.4 s, or ~0.5-0.6 s once the marker-tolerant pass runs over
        the 18.3% of documents that carry a footnote marker.
        """
        return reader.search_body(query, dataset=dataset, limit=limit)

    @_tool()
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
        first ~200 chars of the section's real text), ``occurrence``,
        ``ambiguous_section``, and ``last_changed`` (the act's last
        content change — mention it when currency matters). Null
        ``heading``/``snippet`` mean the embedding is stale for that
        doc; verify via ``get_section`` before trusting such a hit.

        ``ambiguous_section: true`` means the act has more than one ``§``
        under that id (a handful of acts repeat one — an appendix that
        restarts its numbering, or a genuine upstream repeat). The
        embedding index cannot say which of them the vector matched, so
        ``heading``, ``snippet`` and ``occurrence`` come back null rather
        than guessed. Do NOT quote such a hit from the snippet — there is
        none. Call ``list_sections`` to see the candidates, then
        ``get_section(slug, section_id, occurrence=N)`` to read the right
        one.

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

    @_tool()
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

    @_tool()
    def verify_quote(
        slug: str,
        section_id: str,
        quote: str,
        occurrence: int | None = None,
    ) -> dict[str, Any]:
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

        ``occurrence`` (one-based, same convention as ``get_section``)
        selects between duplicate ``§`` ids — the handful of acts where
        an appendix restarts numbering or an id genuinely repeats, the
        same case ``semantic_search`` flags with
        ``ambiguous_section: true``. A duplicate id WITHOUT an
        occurrence returns ``verified=false`` with every candidate
        listed in ``reason`` — re-call with ``occurrence=N``. The check
        then runs against that occurrence's text only: a quote that
        lives in the other duplicate returns ``verified=false``, so
        ambiguity can never silently widen into a search across
        occurrences.

        Empty quote returns ``verified=false`` with a clear reason
        rather than raising. Unknown slug or section, ambiguous id, and
        out-of-range occurrence likewise return ``verified=false`` with
        a recovery-oriented ``reason`` (the messages list available
        sections, candidate occurrences, or the valid occurrence range).
        """
        return reader.verify_quote(slug, section_id, quote, occurrence)

    @_tool()
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

        COVERAGE LIMIT — an empty ``eu_basis`` means no EU basis is
        recorded for this document; it is never evidence that the
        document implements no EU law. The value is extracted from the
        ``eeaReferences`` block of the source XML, and in the current
        corpus every document that has one is a ``lov``: no
        ``forskrift`` carries an ``eu_basis`` at all, though many cite
        EU documents inline in their text. For regulations, read the
        text with ``get_law`` instead of trusting this field.

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

    @_tool()
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
        output. Empty list when no current act records the given CELEX.

        COVERAGE LIMIT — this index is built from the same recorded
        ``eu_basis`` values as ``get_eu_basis``, and in the current
        corpus no ``forskrift`` has one. An empty result therefore does
        not establish that no Norwegian regulation implements the given
        EU document.

        Use the returned slugs with ``get_law`` or ``get_section`` to
        fetch the implementing text.
        """
        return reader.search_eu_implementations(eu_doc_id)

    @_tool()
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


def serve_http(corpus_path: Path, http: HttpConfig) -> None:
    """Start the MCP server over the Streamable HTTP transport.

    Exposes the same read-only tool surface as :func:`serve` (stdio) to remote
    clients. Tool bodies run on worker threads (see :func:`_offload_to_thread`)
    so one slow call never blocks other clients on the shared event loop.

    Requires a credential store unless ``allow_insecure`` is set: an
    unauthenticated server hands the whole tool surface to anyone who can reach
    the port, and that is not something to leave one forgotten flag away.

    TLS is still terminated upstream — run this behind a reverse proxy.
    Blocks until the process stops.
    """
    load_env()
    if http.credentials_path is None and not http.allow_insecure:
        raise ConfigError(
            "refusing to serve HTTP without authentication. Issue a credential "
            "with `lovspor tokens issue --label ...`, or pass --insecure-no-auth "
            "for a local development run.",
        )
    if http.credentials_path is None:
        print(
            "access: SERVING WITHOUT AUTHENTICATION (--insecure-no-auth). Every "
            "tool is open to anyone who can reach this port. Development only.",
            file=sys.stderr,
            flush=True,
        )
    server = build_server(corpus_path, http=http)
    _add_health_routes(server, corpus_path)
    server.run(transport="streamable-http")


def _add_health_routes(server: FastMCP, corpus_path: Path) -> None:
    """Attach ``/healthz`` (process up) and ``/readyz`` (corpus present).

    Kept deliberately cheap so a probe hammering them cannot stall the event
    loop: readiness only stats ``manifest.json`` rather than parsing it or
    shelling out to git. Richer freshness stays behind the ``corpus_status``
    tool. custom_route endpoints are unauthenticated by design (FastMCP).
    """

    # FastMCP's custom_route decorator is untyped; scope the ignore tightly.
    @server.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/readyz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def readyz(request: Request) -> Response:
        if (corpus_path / "manifest.json").is_file():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "unavailable"}, status_code=503)
