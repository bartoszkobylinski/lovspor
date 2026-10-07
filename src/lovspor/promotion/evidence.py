"""``evidence/<slug>.json``: what a version's text states, verbatim (ADR-0016 1e, 2; S10).

A sidecar beside the rendering, never inside it: the Markdown and its
``content_hash`` (the hash of the extracted text, never of the front matter)
are exactly what they were before this file existed, so no approval — pinned
to ``content_hash`` and ``extractor_version`` — goes stale because the
evidence is written. ``EVIDENCE_VERSION`` versions this derivation on its own.

The file holds, for the current version:

* ``relations`` — every source-explicit relation (:mod:`.relations`), each
  with its verbatim sentence, linked to the one corpus document its target
  names or kept as text with the reason;
* ``vedtatt`` / ``ikraft`` — every statement of each date (:mod:`.stated_dates`),
  and whether they agree; a disagreement is ``held``, never resolved.

It is machine-read, not reviewed: ``basis: "source_explicit"`` and
``reviewed: false`` say so on disk. Targets are resolved against the corpus
as it stands at promotion; a target promoted later resolves on the next
write of the document.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.promotion.corpus import LOCAL_DATASET, CentralEntry, CorpusCheckout, LocalManifest
from lovspor.promotion.models import ExtractedRegulation
from lovspor.promotion.relations import (
    LinkedDocument,
    LovdataKey,
    Relation,
    TargetIndex,
    TargetKind,
    resolve,
    short_name_key,
    stated_relations,
)
from lovspor.promotion.stated_dates import StatedDate, stated_ikraft, stated_vedtatt

EVIDENCE_VERSION = 1

_DOC_ID = re.compile(r"(nl|sf|lf)-(\d{4})(\d{2})(\d{2})-(\d+)")
_SHORT_TITLE = re.compile(r"\(([^()]+)\)\s*$")
_KIND: dict[str, TargetKind] = {"nl": "lov", "sf": "forskrift", "lf": "forskrift"}


class EvidenceFile(BaseModel):
    """``evidence/<slug>.json`` of one document's current version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    evidence_version: int = EVIDENCE_VERSION
    doc_id: str
    version: int
    content_hash: str
    basis: Literal["source_explicit"] = "source_explicit"
    reviewed: Literal[False] = False
    vedtatt: StatedDate
    ikraft: StatedDate
    relations: tuple[Relation, ...]


class EvidenceSubject(BaseModel):
    """The version the evidence is read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    version: int
    content_hash: str
    regulation: ExtractedRegulation


def evidence_file(subject: EvidenceSubject, index: TargetIndex) -> EvidenceFile:
    regulation = subject.regulation
    return EvidenceFile(
        doc_id=subject.doc_id,
        version=subject.version,
        content_hash=subject.content_hash,
        vedtatt=stated_vedtatt(regulation),
        ikraft=stated_ikraft(regulation),
        relations=tuple(
            resolve(stated, index, subject.doc_id) for stated in stated_relations(regulation)
        ),
    )


def evidence_path(authority_id: str, slug: str) -> str:
    return f"{LOCAL_DATASET}/{authority_id}/evidence/{slug}.json"


def corpus_index(corpus: CorpusCheckout) -> TargetIndex:
    """Every current document of the checkout a target can resolve to."""
    return target_index(corpus.central_entries(), corpus.local_manifest())


def target_index(central: dict[str, CentralEntry], local: LocalManifest) -> TargetIndex:
    by_lovdata: dict[LovdataKey, LinkedDocument] = {}
    by_name: defaultdict[str, list[LinkedDocument]] = defaultdict(list)
    for doc_id, linked, title in _linkable(central, local):
        key = _lovdata_key(doc_id)
        if key is not None:
            by_lovdata[key] = linked
        short = _SHORT_TITLE.search(title or "") if doc_id.startswith("nl-") else None
        if short is not None:
            by_name[short_name_key(short.group(1))].append(linked)
    names = {name: tuple(found) for name, found in by_name.items()}
    return TargetIndex(by_lovdata=by_lovdata, by_short_name=names)


def _linkable(
    central: dict[str, CentralEntry], local: LocalManifest
) -> list[tuple[str, LinkedDocument, str | None]]:
    found: list[tuple[str, LinkedDocument, str | None]] = []
    for doc_id, entry in sorted(central.items()):
        dataset = _central_dataset(entry)
        if entry.status == "current" and entry.slug and dataset:
            linked = LinkedDocument(doc_id=doc_id, dataset=dataset, address=entry.slug)
            found.append((doc_id, linked, entry.title))
    for doc_id, record in sorted(local.documents.items()):
        if record.status == "current":
            address = f"{record.authority_id}/{record.slug}"
            linked = LinkedDocument(doc_id=doc_id, dataset="lokale-forskrifter", address=address)
            found.append((doc_id, linked, None))
    return found


def _central_dataset(entry: CentralEntry) -> Literal["lover", "forskrifter"] | None:
    path = entry.markdown_path or ""
    if path.startswith("lover/"):
        return "lover"
    return "forskrifter" if path.startswith("forskrifter/") else None


def _lovdata_key(doc_id: str) -> LovdataKey | None:
    match = _DOC_ID.fullmatch(doc_id)
    if match is None:
        return None
    prefix, year, month, day, number = match.groups()
    try:
        stated = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return _KIND[prefix], stated, int(number)
