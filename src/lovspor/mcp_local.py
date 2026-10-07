"""Which corpus a ``get_law`` / ``get_section`` / ``search_laws`` call reads (ADR-0016 5).

Local regulations are opt-in (ADR-0016 Option D1): a ``<authority_id>/<slug>``
or ``lf-``/``lk-`` address, or ``dataset="lokale-forskrifter"``, reaches
:class:`~lovspor.local_corpus.LocalDataset`; everything else takes exactly
the central path it took before the dataset existed, so existing calls stay
byte-identical. A local answer always carries the observation label and
never the central temporal notice: the source markers that notice reads are
Lovdata's, and no valid-time fact is evaluated for local text (ADR-0016 2).

Two time axes reach a local document, never fused (ADR-0016 2):
``recorded_at`` on ``get_section`` is the corpus (git) axis and reads the
document from the resolved commit, exactly as for a central act (ADR-0011);
``observed_at`` on ``get_observation_history`` is the observation axis and
reads only the observation intervals (:mod:`lovspor.observation_history`).

A sibling of ``mcp.py`` rather than a section of it, for the reason
``tool_surface.py`` gives (issue #102). It builds on ``mcp.py``'s section
parser, so ``build_server`` imports it at call time.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from lovspor.errors import LocalScopeError
from lovspor.local_corpus import (
    LOCAL_DATASET,
    LocalDataset,
    ObservationLabel,
    ServedLocal,
    is_local_address,
)
from lovspor.mcp import (
    CorpusReader,
    SectionIndex,
    _bounded_limit,
    _cross_references_for,
    _normalize_section_id,
    _parse_valid_at,
    _section_from_body,
    _section_index_from_body,
    _section_result,
    _stamp_not_evaluated,
    _strip_frontmatter_and_h1,
    _with_section_notice,
)
from lovspor.observation_history import observation_history
from lovspor.temporal import append_notice, evaluation_date_today

ToolDecorator = Callable[[], Callable[[Callable[..., Any]], Callable[..., Any]]]

_AUTHORITY_SCOPE = f"authority filters only dataset={LOCAL_DATASET!r} (ADR-0016 5)"
_KLASS_CODE = re.compile(r"\d{2}|\d{4}")


class _Central(NamedTuple):
    """The central namespace a local section's cross-references are validated against."""

    slugs: Callable[[], Iterable[str]]
    index_for: Callable[[str], SectionIndex]


class ServedCorpus:
    """The central reader and the local dataset of one corpus, routed per call."""

    def __init__(self, reader: CorpusReader, *, warm: bool) -> None:
        """``warm`` pre-builds the central indices (hosted mode, see ``CorpusReader.warm``)."""
        self._reader = reader
        self._local = LocalDataset(reader.corpus_path)
        if warm:
            reader.warm()

    def register(self, tool: ToolDecorator) -> None:
        """Add, through ``build_server``'s decorator, the tools whose surface the local
        dataset shapes: the one only it has, and two with a parameter only it takes."""
        tool()(self.search_laws)
        tool()(self.corpus_status)
        tool()(self.get_observation_history)

    def get_law(self, slug: str) -> str:
        if is_local_address(slug):
            return _with_observation(self._local.document(slug))
        return append_notice(self._reader.get_law(slug), evaluation_date_today())

    def get_section(
        self, slug: str, section_id: str, occurrence: int | None, recorded_at: str | None
    ) -> dict[str, Any]:
        if is_local_address(slug):
            return self._local_section(slug, (section_id, occurrence), recorded_at)
        if recorded_at is not None:
            state = self._reader.at_state(recorded_at)
            return _stamp_not_evaluated(state.get_section(slug, section_id, occurrence))
        section = self._reader.get_section(slug, section_id, occurrence)
        return _with_section_notice(section, evaluation_date_today())

    def search_laws(
        self, query: str, dataset: str | None = None, limit: int = 20, authority: str | None = None
    ) -> list[dict[str, Any]]:
        """Search the corpus for laws whose slug or title contains ``query``.

        Substring match, case-insensitive, against manifest metadata
        only (no body-text scan in this MVP). Returns slug, doc_id,
        title, dataset, last_changed, and total_changes for each hit.
        Use ``get_law(slug)`` to fetch the full text of any result.

        ``dataset`` (optional): ``lover`` or ``forskrifter`` to filter.
        ``lokale-forskrifter`` searches ONLY the observed local regulations
        (never included otherwise); each hit's ``slug`` is its
        ``<authority_id>/<slug>`` and it carries ``observation``.
        ``limit``: max results (default 20, capped).
        ``authority`` (optional, only with ``dataset="lokale-forskrifter"``):
        the publishing authority's SSB KLASS code — four digits for a
        kommune (``"0301"``), two for a fylkeskommune — to search only the
        regulations it published; refused with any other ``dataset``.
        """
        if dataset != LOCAL_DATASET:
            if authority is not None:
                raise LocalScopeError(_AUTHORITY_SCOPE)
            return self._reader.search_laws(query, dataset=dataset, limit=limit)
        bounded = _bounded_limit(limit)
        found = self._local.matches(query, _klass_code(authority))
        return [self._local.hit(i, r) for i, r in found[:bounded]]

    def corpus_status(self, dataset: str | None = None) -> dict[str, Any]:
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

        ``dataset`` (optional): ``lokale-forskrifter`` adds ``local``, the
        observed local regulations: ``current_documents``,
        ``removed_documents`` (withdrawn), ``authorities`` (per KLASS code:
        ``authority_id``, ``authority_type``, ``current``, ``removed``),
        ``manifest_generated_at``, ``asserted: false`` and a ``notice``.
        Omitted, the answer is the central status alone. Coverage is not
        completeness: a regulation absent here may exist, and artifacts
        held before promotion are counted on the archive, not in the corpus.

        The server itself never mutates the corpus or fetches anything —
        the user runs the suggested ``refresh_command`` manually.
        """
        status = self._reader.corpus_status()
        if dataset is None:
            return status
        if dataset != LOCAL_DATASET:
            raise LocalScopeError(f"corpus_status takes dataset={LOCAL_DATASET!r} or none")
        return {**status, "local": self._local.status()}

    def search_body(
        self, query: str, dataset: str | None, limit: int, recorded_at: str | None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if recorded_at is not None:
            state = self._reader.at_state(recorded_at)
            return state.search_body(query, dataset=dataset, limit=limit)
        return self._reader.search_body(query, dataset=dataset, limit=limit)

    def get_temporal_events(
        self, slug: str, section_id: str | None, recorded_at: str | None, valid_at: str | None
    ) -> dict[str, Any]:
        state = self._reader if recorded_at is None else self._reader.at_state(recorded_at)
        evaluated = None if valid_at is None else _parse_valid_at(valid_at)
        return state.get_temporal_events(slug, section_id, evaluated)

    def validate_citation(self, citation: str, recorded_at: str | None) -> dict[str, Any]:
        if recorded_at is not None:
            return self._reader.at_state(recorded_at).validate_citation(citation)
        return self._reader.validate_citation(citation)

    def verify_quote(
        self, slug: str, section_id: str, quote: str, where: tuple[int | None, str | None]
    ) -> dict[str, Any]:
        """``where`` is ``(occurrence, recorded_at)``."""
        occurrence, recorded_at = where
        if recorded_at is not None:
            state = self._reader.at_state(recorded_at)
            return state.verify_quote(slug, section_id, quote, occurrence)
        return self._reader.verify_quote(slug, section_id, quote, occurrence)

    def get_observation_history(
        self, document: str, observed_at: str | None = None, include_text: bool = False
    ) -> dict[str, Any]:
        """Return the observation history of one LOCAL regulation (ADR-0016).

        Every version of the regulation observed on the authority's
        website, oldest first, each with the interval it was observed in.

        ``document``: a local regulation, ``<authority_id>/<slug>`` or its
        ``lf-``/``lk-`` id; find one with
        ``search_laws(dataset="lokale-forskrifter")``. A central law's slug
        is refused: its history is ``get_law_history``.

        Returns ``document``, ``doc_id``, ``dataset``, ``versions`` (each
        ``version``, ``content_hash``, ``observed_at_first``,
        ``observed_at_last``, ``observation_count``, ``primary_url``,
        ``corroborating_urls``), ``source_status`` (the last outcome at
        the primary URL), ``excluded`` (observations left out of the
        comparison, such as a tombstoned capture), ``observation``
        (``asserted: false``) and ``at``.

        An interval says only that these bytes could be retrieved at the
        observed instants. Nothing is asserted between two observations,
        and nothing here says when the regulation was adopted or in force.

        ``observed_at`` (optional): an instant with an offset, e.g.
        ``2026-09-01T12:00:00Z`` (a bare date is refused). ``at`` is then
        one typed ``outcome`` (``null`` without ``observed_at``):

        - ``contained``: inside a version's ``[observed_at_first,
          observed_at_last]``, bounds included; ``version`` names it.
        - ``between_observations``: after one version's last and before
          the next one's first observation; ``before`` and ``after`` name
          both, and neither is asserted at that instant.
        - ``before_first_observation``: before the document's
          ``observed_at_first``; observation began at
          ``observation_floor`` (2026-08-19).
        - ``after_last_observation``: after the last observation; ``last``
          names the version last seen, not asserted past
          ``observed_at_last``.

        ``observed_at`` is the observation axis only: when the
        authority's website was read. It never selects a corpus state —
        for what the corpus recorded on a date, call ``get_section`` with
        ``recorded_at``.

        ``include_text`` (default false; needs ``observed_at``): a
        ``contained`` outcome then carries ``text``, the Markdown of that
        version.
        """
        return observation_history(self._local, document, observed_at, include_text)

    def _local_section(
        self, address: str, where: tuple[str, int | None], recorded_at: str | None
    ) -> dict[str, Any]:
        """Live, or from the corpus commit ``recorded_at`` resolves to (ADR-0011)."""
        if recorded_at is None:
            served = self._local.document(address)
            reader = self._reader
            central = _Central(reader._load_slug_index, reader._section_index_for)
            return _local_section(served, where, central)
        state = self._reader.at_state(recorded_at)
        served = self._local.at(state._data.snapshot, f"at {recorded_at}").document(address)
        central = _Central(state._slug_index, state._section_index_for)
        section = state._stamp(_local_section(served, where, central), None)
        return {**section, "content_hash": served.record.content_hash}


def _local_section(
    served: ServedLocal, where: tuple[str, int | None], central: _Central
) -> dict[str, Any]:
    section_id, occurrence = where
    body = _strip_frontmatter_and_h1(served.markdown)
    canonical = _normalize_section_id(section_id)
    section, _ = _section_from_body(served.address, body, canonical, occurrence)
    index_for = _index_for(served.address, _section_index_from_body(body), central.index_for)
    known = {*central.slugs(), served.address}
    references = _cross_references_for(section["body"], served.address, known, index_for)
    return {
        **_section_result(served.address, canonical, section, references),
        "doc_id": served.doc_id,
        "dataset": LOCAL_DATASET,
        "observation": served.observation.label(),
    }


def _index_for(
    address: str, own: SectionIndex, central: Callable[[str], SectionIndex]
) -> Callable[[str], SectionIndex]:
    def index_for(slug: str) -> SectionIndex:
        return own if slug == address else central(slug)

    return index_for


def _klass_code(authority: str | None) -> str | None:
    """``authority`` as a KLASS code, refused when it cannot be one."""
    if authority is not None and _KLASS_CODE.fullmatch(authority) is None:
        msg = (
            f"authority is a KLASS code: 4 digits (kommune) or 2 (fylkeskommune), got {authority!r}"
        )
        raise LocalScopeError(msg)
    return authority


def _with_observation(served: ServedLocal) -> str:
    """The document, then its observation label as a heading and a JSON block."""
    block = ObservationLabel(observation=served.observation).model_dump_json(indent=2)
    heading = "**Observation — observed on the authority's website, not asserted (ADR-0016).**"
    return f"{served.markdown.rstrip()}\n\n---\n\n{heading}\n\n```json\n{block}\n```\n"
