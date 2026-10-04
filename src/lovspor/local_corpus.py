"""The local-regulations dataset as the MCP server reads it (ADR-0016 3, 5; slice S5).

``lovverk/lokale-forskrifter/`` is a dataset of its own, beside the central
``manifest.json`` and never in it: an engine that predates it reads the root
manifest only and never learns the directory exists. This module is the
read side; :mod:`lovspor.promotion` is the only writer, and the manifest is
read with its model (:class:`~lovspor.promotion.corpus.LocalManifest`).

A local document is addressed as ``<authority_id>/<slug>`` or by its id
(``lf-yyyymmdd-nnnn``, ``lk-<authority_id>-<h12>``). A bare slug never
names one: the central namespace stays exactly what it was, so no existing
call can start answering with observed material.

Every served local document carries an :class:`Observation`: what was
observed, where and when, and ``asserted: false`` as a constant — the model
cannot hold ``true``. Its fields come from the document's front matter and
its ``observations/<slug>.json``; files that disagree with the manifest, or
with each other about which document they describe, are a
:class:`~lovspor.errors.LocalCorpusError`, never served unlabelled.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import AmbiguousSlugError, CorpusNotFoundError, LocalCorpusError
from lovspor.promotion.corpus import (
    LOCAL_DATASET,
    LOCAL_DIR,
    MANIFEST_NAME,
    LocalManifest,
    LocalRecord,
)

__all__ = [
    "LOCAL_DATASET",
    "OBSERVATION_NOTICE",
    "LocalDataset",
    "LocalRecord",
    "Observation",
    "ServedLocal",
    "is_local_address",
]

OBSERVATION_NOTICE = (
    "Observert på myndighetens nettsted; ikke kontrollert mot Norsk Lovtidend eller Lovdata. "
    "Lovspor hevder ikke at forskriften gjelder. / Observed on the authority's website; "
    "not verified against Norsk Lovtidend or Lovdata. "
    "Lovspor does not assert that this regulation is in force."
)

_LOCAL_ID = re.compile(r"lf-\d{8}-\d{4,}|lk-\d{2}(?:\d{2})?-[0-9a-f]{12}")
_FLOW_PAIR = re.compile(r'(\w+): ("(?:[^"\\]|\\.)*"|null)')
_FRONT_MATTER_END = "\n---\n"

LocalEntry = tuple[str, LocalRecord]


def is_local_address(address: str) -> bool:
    """True for ``<authority_id>/<slug>`` or a local id; a bare slug is never local."""
    return "/" in address or _LOCAL_ID.fullmatch(address) is not None


class ObservedAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    type: str
    name: str


class Observation(BaseModel):
    """The label on every response carrying local content (ADR-0016 5), in its key order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: Literal["observed"] = "observed"
    asserted: Literal[False] = False
    authority: ObservedAuthority
    source_url: str
    observed_at_first: str
    observed_at_last: str
    notice: str = OBSERVATION_NOTICE


class ServedLocal(BaseModel):
    """One local document as a tool serves it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    address: str
    record: LocalRecord
    markdown: str
    observation: Observation


class _FrontMatter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    authority: ObservedAuthority
    version: int
    observed_at_first: str
    source_url: str


class _ObservedVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int
    observed_at_last: str


class _Observations(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    doc_id: str
    versions: tuple[_ObservedVersion, ...]


class LocalDataset:
    """Read-only view of ``lokale-forskrifter/`` in one corpus checkout.

    A corpus without the directory serves an empty dataset: every corpus
    that predates ADR-0016 stays a valid corpus. The manifest is re-read
    when its mtime moves, the same change signal the central reader uses.
    """

    def __init__(self, corpus_path: Path) -> None:
        self._root = corpus_path
        self._cached: tuple[int, LocalManifest] | None = None

    def manifest(self) -> LocalManifest:
        path = self._root / LOCAL_DIR / MANIFEST_NAME
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            return LocalManifest()
        cached = self._cached
        if cached is not None and cached[0] == mtime:
            return cached[1]
        manifest = _read_manifest(path)
        self._cached = (mtime, manifest)
        return manifest

    def resolve(self, address: str) -> LocalEntry:
        """The one current record ``address`` names, by qualified slug or id."""
        current = [(i, r) for i, r in self.manifest().documents.items() if r.status == "current"]
        authority, _, slug = address.partition("/")
        if slug:
            found = [(i, r) for i, r in current if (r.authority_id, r.slug) == (authority, slug)]
        else:
            found = [(i, r) for i, r in current if i == address]
        if len(found) > 1:
            names = ", ".join(doc_id for doc_id, _ in found)
            raise AmbiguousSlugError(f"local address {address!r} names {len(found)}: {names}")
        if not found:
            raise CorpusNotFoundError(
                f"no current local regulation {address!r}; use search_laws with "
                f"dataset={LOCAL_DATASET!r} to discover <authority_id>/<slug> addresses",
            )
        return found[0]

    def document(self, address: str) -> ServedLocal:
        doc_id, record = self.resolve(address)
        markdown_path = self._inside(record.markdown_path)
        markdown = _read_text(markdown_path)
        return ServedLocal(
            doc_id=doc_id,
            address=f"{record.authority_id}/{record.slug}",
            record=record,
            markdown=markdown,
            observation=self._observation(doc_id, markdown_path, markdown),
        )

    def matches(self, query: str) -> list[LocalEntry]:
        """Current records whose qualified slug or title contains ``query``, in manifest order."""
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            (doc_id, record)
            for doc_id, record in self.manifest().documents.items()
            if record.status == "current"
            and needle in f"{record.authority_id}/{record.slug} {record.title}".lower()
        ]

    def hit(self, doc_id: str, record: LocalRecord) -> dict[str, Any]:
        """A ``search_laws`` result row for one local document, labelled."""
        served = self.document(doc_id)
        return {
            "slug": served.address,
            "doc_id": doc_id,
            "title": record.title,
            "dataset": LOCAL_DATASET,
            "authority_id": record.authority_id,
            "version": record.version,
            "observation": served.observation.model_dump(mode="json"),
        }

    def _observation(self, doc_id: str, markdown_path: Path, markdown: str) -> Observation:
        front = _front_matter(markdown_path, markdown)
        if front.id != doc_id:
            raise LocalCorpusError(f"{markdown_path} describes {front.id}, not {doc_id}")
        path = markdown_path.parent / "observations" / f"{markdown_path.stem}.json"
        observed_last = _observed_at_last(path, doc_id, front.version)
        return Observation(
            authority=front.authority,
            source_url=front.source_url,
            observed_at_first=front.observed_at_first,
            observed_at_last=observed_last,
        )

    def _inside(self, relative: str) -> Path:
        dataset = (self._root / LOCAL_DIR).resolve()
        target = (self._root / relative).resolve()
        if dataset not in target.parents:
            raise LocalCorpusError(f"manifest path {relative!r} is outside {LOCAL_DIR}/")
        return target


def _read_manifest(path: Path) -> LocalManifest:
    try:
        return LocalManifest.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise LocalCorpusError(f"{path} does not read as the local manifest: {exc}") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LocalCorpusError(
            f"the local manifest references {path} but the file is missing; "
            "run 'git pull' in the corpus to refresh",
        ) from exc


def _front_matter(path: Path, markdown: str) -> _FrontMatter:
    try:
        return _FrontMatter.model_validate(_front_matter_fields(markdown))
    except (ValueError, ValidationError) as exc:
        raise LocalCorpusError(f"{path} has no readable front matter: {exc}") from exc


def _front_matter_fields(markdown: str) -> dict[str, Any]:
    """The front matter the local renderer writes: JSON scalars, one flow mapping."""
    end = markdown.find(_FRONT_MATTER_END, 3)
    if not markdown.startswith("---\n") or end < 0:
        raise ValueError("no front matter block")
    fields: dict[str, Any] = {}
    for line in markdown[4:end].split("\n"):
        key, separator, value = line.partition(": ")
        if not separator:
            raise ValueError(f"not a front-matter field: {line!r}")
        fields[key] = _flow_mapping(value) if value.startswith("{") else json.loads(value)
    return fields


def _flow_mapping(value: str) -> dict[str, Any]:
    return {key: json.loads(literal) for key, literal in _FLOW_PAIR.findall(value)}


def _observed_at_last(path: Path, doc_id: str, version: int) -> str:
    try:
        observed = _Observations.model_validate_json(_read_text(path))
    except ValidationError as exc:
        raise LocalCorpusError(f"{path} does not read as the observations file: {exc}") from exc
    if observed.doc_id != doc_id:
        raise LocalCorpusError(f"{path} holds the observations of {observed.doc_id}")
    for entry in observed.versions:
        if entry.version == version:
            return entry.observed_at_last
    raise LocalCorpusError(f"{path} has no observations of version {version} of {doc_id}")
