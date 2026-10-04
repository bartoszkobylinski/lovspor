"""Which corpus a ``get_law`` / ``get_section`` / ``search_laws`` call reads (ADR-0016 5).

Local regulations are opt-in (ADR-0016 Option D1): a ``<authority_id>/<slug>``
or ``lf-``/``lk-`` address, or ``dataset="lokale-forskrifter"``, reaches
:class:`~lovspor.local_corpus.LocalDataset`; everything else takes exactly
the central path it took before the dataset existed, so existing calls stay
byte-identical. A local answer always carries the observation label and
never the central temporal notice: the source markers that notice reads are
Lovdata's, and no valid-time fact is evaluated for local text (ADR-0016 2).

A sibling of ``mcp.py`` rather than a section of it, for the reason
``tool_surface.py`` gives (issue #102). It builds on ``mcp.py``'s section
parser, so ``build_server`` imports it at call time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from lovspor.errors import LocalScopeError
from lovspor.local_corpus import LOCAL_DATASET, LocalDataset, ServedLocal, is_local_address
from lovspor.mcp import (
    CorpusReader,
    SectionIndex,
    _bounded_limit,
    _cross_references_for,
    _normalize_section_id,
    _section_from_body,
    _section_index_from_body,
    _section_result,
    _stamp_not_evaluated,
    _strip_frontmatter_and_h1,
    _with_section_notice,
)
from lovspor.temporal import append_notice, evaluation_date_today


class ServedCorpus:
    """The central reader and the local dataset of one corpus, routed per call."""

    def __init__(self, reader: CorpusReader, *, warm: bool) -> None:
        """``warm`` pre-builds the central indices (hosted mode, see ``CorpusReader.warm``)."""
        self._reader = reader
        self._local = LocalDataset(reader.corpus_path)
        if warm:
            reader.warm()

    def get_law(self, slug: str) -> str:
        if is_local_address(slug):
            return _with_observation(self._local.document(slug))
        return append_notice(self._reader.get_law(slug), evaluation_date_today())

    def get_section(
        self, slug: str, section_id: str, occurrence: int | None, recorded_at: str | None
    ) -> dict[str, Any]:
        if is_local_address(slug):
            if recorded_at is not None:
                raise LocalScopeError(
                    "recorded_at is not served for local regulations yet (ADR-0016 S5); "
                    "omit it to read the current version",
                )
            return self._local_section(slug, section_id, occurrence)
        if recorded_at is not None:
            state = self._reader.at_state(recorded_at)
            return _stamp_not_evaluated(state.get_section(slug, section_id, occurrence))
        section = self._reader.get_section(slug, section_id, occurrence)
        return _with_section_notice(section, evaluation_date_today())

    def search_laws(self, query: str, dataset: str | None, limit: int) -> list[dict[str, Any]]:
        if dataset != LOCAL_DATASET:
            return self._reader.search_laws(query, dataset=dataset, limit=limit)
        bounded = _bounded_limit(limit)
        return [self._local.hit(i, r) for i, r in self._local.matches(query)[:bounded]]

    def search_body(
        self, query: str, dataset: str | None, limit: int, recorded_at: str | None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if recorded_at is not None:
            state = self._reader.at_state(recorded_at)
            return state.search_body(query, dataset=dataset, limit=limit)
        return self._reader.search_body(query, dataset=dataset, limit=limit)

    def validate_citation(self, citation: str, recorded_at: str | None) -> dict[str, Any]:
        if recorded_at is not None:
            return self._reader.at_state(recorded_at).validate_citation(citation)
        return self._reader.validate_citation(citation)

    def _local_section(
        self, address: str, section_id: str, occurrence: int | None
    ) -> dict[str, Any]:
        served = self._local.document(address)
        body = _strip_frontmatter_and_h1(served.markdown)
        canonical = _normalize_section_id(section_id)
        section, _ = _section_from_body(served.address, body, canonical, occurrence)
        known = {*self._reader._load_slug_index(), served.address}
        index_for = self._index_for(served.address, _section_index_from_body(body))
        references = _cross_references_for(section["body"], served.address, known, index_for)
        return {
            **_section_result(served.address, canonical, section, references),
            "doc_id": served.doc_id,
            "dataset": LOCAL_DATASET,
            "observation": served.observation.model_dump(mode="json"),
        }

    def _index_for(self, address: str, own: SectionIndex) -> Callable[[str], SectionIndex]:
        def index_for(slug: str) -> SectionIndex:
            return own if slug == address else self._reader._section_index_for(slug)

        return index_for


def _with_observation(served: ServedLocal) -> str:
    """The document, then its observation label as a heading and a JSON block."""
    block = json.dumps(
        {"observation": served.observation.model_dump(mode="json")}, indent=2, ensure_ascii=False
    )
    heading = "**Observation — observed on the authority's website, not asserted (ADR-0016).**"
    return f"{served.markdown.rstrip()}\n\n---\n\n{heading}\n\n```json\n{block}\n```\n"
