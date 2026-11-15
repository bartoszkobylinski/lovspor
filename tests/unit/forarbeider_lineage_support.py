"""Synthetic observations and lineage chains for the FC8 tests (#738).

Every page is the invented Prop. 901 L of :mod:`tests.unit.forarbeider_version_support`;
no real government text is used. A "replay" is the same bytes read again at a
later time; a "new version" changes the bytes of one chapter or attachment.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from lovspor.forarbeider.document_extract import SourcePage
from lovspor.forarbeider.publication_policy import PublicationPolicy
from lovspor.forarbeider.version_lineage import (
    Observation,
    Recorded,
    entry_text,
    parse_entry,
    record_observation,
)
from lovspor.forarbeider.version_lineage_models import LineageEntry
from lovspor.forarbeider.version_manifest import Evidence
from tests.unit.forarbeider_version_support import (
    PDF,
    PDF_URL,
    capture,
    evidence,
    pages,
    with_body,
)
from tests.unit.test_forarbeider_document_extract import WHEN

HOST = "https://www.regjeringen.no"
LATER = WHEN + timedelta(days=1)
LATEST = WHEN + timedelta(days=2)
CHANGED_BODY = "<p>Endret oppdiktet tekst i kapittel 2.</p>"
OTHER_PDF = b"%PDF-1.7\n% another invented attachment\n%%EOF\n"


def at(source: Evidence, when: datetime) -> Evidence:
    """``source`` with every page, the landing page and each capture read at ``when``."""

    def moved(page: SourcePage) -> SourcePage:
        return page._replace(retrieved_at=when)

    landing = None if source.landing is None else moved(source.landing)
    return Evidence(
        tuple(moved(p) for p in source.pages), landing, tuple(moved(p) for p in source.captures)
    )


def original(when: datetime = WHEN) -> Evidence:
    return at(evidence(), when)


def changed(when: datetime = LATER) -> Evidence:
    """Chapter 2's paragraph replaced: a new content version."""
    return at(evidence([*pages(1), with_body(2, CHANGED_BODY), *pages(3, 4)]), when)


def linked(payload: bytes = PDF, when: datetime = WHEN) -> Evidence:
    """Chapter 2 links one attachment, captured as ``payload``."""
    link = f'<p>Se <a href="{PDF_URL.removeprefix(HOST)}">vedlegg</a> .</p>'
    given = [*pages(1), with_body(2, link), *pages(3, 4)]
    return at(evidence(given, captures=(capture(PDF_URL, payload),)), when)


class Chain:
    """An append-only chain built by :func:`record_observation`, and every artifact it named."""

    def __init__(self) -> None:
        self.raws: list[bytes] = []
        self.artifacts: dict[str, bytes] = {}
        self.last: Recorded | None = None

    def add(self, source: Evidence, policy: PublicationPolicy | None = None) -> Recorded:
        return self.observe(Observation(source, policy))

    def observe(self, observation: Observation) -> Recorded:
        recorded = record_observation(tuple(self.raws), observation)
        if recorded.appended:
            self.raws.append(recorded.raw)
        self.artifacts.update(recorded.artifacts)
        self.last = recorded
        return recorded

    def entries(self) -> list[LineageEntry]:
        return [parse_entry(raw) for raw in self.raws]


def chain(*sources: Evidence) -> Chain:
    built = Chain()
    for source in sources:
        built.add(source)
    return built


def forged(entry: LineageEntry, **changes: Any) -> bytes:
    """``entry`` with ``changes``, re-serialized canonically (still a valid record)."""
    return entry_text(LineageEntry.model_validate({**entry.model_dump(), **changes})).encode()


def without(artifacts: Mapping[str, bytes], digest: str) -> dict[str, bytes]:
    return {key: value for key, value in artifacts.items() if key != digest}


def codes(verification: Any) -> set[tuple[str, str]]:
    return {(finding.code, finding.effect) for finding in verification.findings}
