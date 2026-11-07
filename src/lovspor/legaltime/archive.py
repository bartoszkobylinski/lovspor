"""Evidence for one act, read from pinned publicData archives.

One pass over each archive builds a small header index (refid, title,
``dateInForce``, ``changesToDocuments``, publication time) that is cached
beside the archive under the archive's own sha256, so a second run opens
nothing it does not need. The act's enacting member, every announcing law
whose header lists the act under ``Endrer``, and every commencement decision
whose title cites one of those laws are then read in a second pass. Bytes
never leave the gitignored cache.
"""

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from lovspor.errors import ExtractionError
from lovspor.extraction.tarball import iter_tarball_xml
from lovspor.legaltime.commencement import is_decision_for
from lovspor.legaltime.document import Document, Frozen, parse_document
from lovspor.legaltime.errors import LegalTimeDocumentError, LegalTimeInputError

INDEX_VERSION = "legaltime-index-2"


class MemberRecord(Frozen):
    name: str
    sha256: str
    refid: str
    title: str
    date_in_force: str
    published_at: str | None
    changes_to_documents: tuple[str, ...]


class ArchiveIndex(Frozen):
    archive: str
    sha256: str
    index_version: str
    members: tuple[MemberRecord, ...]
    skipped: tuple[str, ...] = ()

    @property
    def horizon(self) -> str | None:
        """The latest publication time any member carries: the archive's knowledge horizon."""
        stamps = [m.published_at for m in self.members if m.published_at]
        return max(stamps) if stamps else None


class Evidence(Frozen):
    act: str
    archives: tuple[ArchiveIndex, ...]
    baseline: Document
    amenders: tuple[Document, ...]
    decisions: tuple[Document, ...]


Indexed = list[tuple[Path, ArchiveIndex]]


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, hashlib.sha256).hexdigest()


def _record(name: str, body: bytes) -> MemberRecord | None:
    try:
        document = parse_document(body, name)
    except LegalTimeDocumentError:
        return None
    header = document.header
    return MemberRecord(
        name=name, sha256=document.sha256, refid=header.refid, title=header.title,
        date_in_force=header.date_in_force,
        published_at=header.published_at.isoformat() if header.published_at else None,
        changes_to_documents=header.changes_to_documents,
    )  # fmt: skip


def _index_path(archive: Path, cache_dir: Path | None) -> Path:
    return (cache_dir or archive.parent) / f"{archive.name}.{INDEX_VERSION}.json"


def _cached_index(path: Path, digest: str) -> ArchiveIndex | None:
    if not path.exists():
        return None
    try:
        index = ArchiveIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return index if index.sha256 == digest and index.index_version == INDEX_VERSION else None


def _scan(archive: Path) -> tuple[list[MemberRecord], list[str]]:
    records: list[MemberRecord] = []
    skipped: list[str] = []
    try:
        for member in iter_tarball_xml(archive):
            record = _record(member.name, member.content)
            (records.append(record) if record else skipped.append(member.name))
    except ExtractionError as exc:
        raise LegalTimeInputError(f"unreadable archive {archive.name}: {exc}") from exc
    names = [r.name for r in records]
    if len(set(names)) != len(names):
        raise LegalTimeInputError(f"duplicate member names in {archive.name}")
    return records, sorted(skipped)


def index_archive(archive: Path, cache_dir: Path | None = None) -> ArchiveIndex:
    """Header index of every XML member, built once per archive digest."""
    if not archive.is_file():
        raise LegalTimeInputError(f"archive not found: {archive}")
    digest = file_sha256(archive)
    cache = _index_path(archive, cache_dir)
    cached = _cached_index(cache, digest)
    if cached is not None:
        return cached
    records, skipped = _scan(archive)
    index = ArchiveIndex(
        archive=archive.name, sha256=digest, index_version=INDEX_VERSION,
        members=tuple(records), skipped=tuple(skipped),
    )  # fmt: skip
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(index.model_dump_json(indent=1), encoding="utf-8")
    return index


def read_members(archive: Path, names: Iterable[str]) -> dict[str, Document]:
    """Parse exactly the named members in one pass; a name not found is an input error."""
    wanted = set(names)
    found: dict[str, Document] = {}
    for member in iter_tarball_xml(archive):
        if member.name in wanted:
            found[member.name] = parse_document(member.content, member.name)
    missing = wanted - set(found)
    if missing:
        raise LegalTimeInputError(f"members missing from {archive.name}: {sorted(missing)}")
    return found


def _is_law(record: MemberRecord) -> bool:
    return record.refid.startswith("lov/")


def _amender_names(index: ArchiveIndex, act: str) -> list[str]:
    return [
        m.name
        for m in index.members
        if _is_law(m) and m.refid != act and act in m.changes_to_documents
    ]


def _title_document(record: MemberRecord) -> Document:
    """A header-only stand-in so the decision test runs on the index, not the bytes."""
    title = record.title.replace("&", "&amp;").replace("<", "&lt;")
    listed = "".join(f"<li>{refid}</li>" for refid in record.changes_to_documents)
    body = (
        f'<html><body><header><dl><dd class="refid">{record.refid}</dd>'
        f'<dd class="title">{title}</dd><dd class="changesToDocuments"><ul>{listed}</ul></dd>'
        "</dl></header><main/></body></html>"
    )
    return parse_document(body.encode(), record.name)


def _decision_names(index: ArchiveIndex, laws: set[str]) -> list[str]:
    decisions = []
    for member in index.members:
        if member.refid.startswith("forskrift/"):
            stand_in = _title_document(member)
            # Titles name one law; a decision commencing several lists them all as changed, so the
            # list is the superset read here and the body block titles decide at resolution time.
            touched = set(member.changes_to_documents)
            if any(is_decision_for(stand_in, law) or law in touched for law in laws):
                decisions.append(member.name)
    return decisions


def _baseline(indexed: Indexed, act: str) -> tuple[Path, str]:
    """The one enacting member of the act; two claimants are refused, never chosen between."""
    found = [
        (path, m.name) for path, index in indexed for m in index.members
        if m.refid == act and _is_law(m)
    ]  # fmt: skip
    if len(found) > 1:
        raise LegalTimeInputError(f"several enacting members claim {act}: {sorted(found)}")
    if not found:
        raise LegalTimeInputError(f"no enacting member for {act} in the given archives")
    return found[0]


def _ordered(documents: Iterable[Document]) -> tuple[Document, ...]:
    return tuple(sorted(documents, key=lambda d: (d.header.published_at or datetime.min, d.member)))


def _wanted(indexed: Indexed, act: str) -> dict[Path, tuple[list[str], list[str]]]:
    """Per archive: the amender member names and the decision member names."""
    amenders = {path: _amender_names(index, act) for path, index in indexed}
    laws = {act} | {
        m.refid for path, index in indexed for m in index.members if m.name in amenders[path]
    }
    return {path: (amenders[path], _decision_names(index, laws)) for path, index in indexed}


def _read_evidence(
    wanted: dict[Path, tuple[list[str], list[str]]], baseline: tuple[Path, str]
) -> tuple[list[Document], list[Document], Document]:
    """Read every wanted member archive by archive; the act's text only from its own archive.

    Member paths are archive-local, so the enacting member is read from the
    archive that claims it and never looked up across the others.
    """
    amenders: list[Document] = []
    decisions: list[Document] = []
    base_amenders, base_decisions = wanted[baseline[0]]
    base = read_members(baseline[0], {*base_amenders, *base_decisions, baseline[1]})
    for path, (amender_names, decision_names) in wanted.items():
        documents = (
            base if path == baseline[0] else read_members(path, {*amender_names, *decision_names})
        )
        amenders.extend(documents[n] for n in amender_names)
        decisions.extend(documents[n] for n in decision_names)
    return amenders, decisions, base[baseline[1]]


def collect(act: str, archives: list[Path], cache_dir: Path | None = None) -> Evidence:
    """Everything the archives hold about ``act``: its text, its amenders, their decisions."""
    indexed: Indexed = [(path, index_archive(path, cache_dir)) for path in archives]
    baseline = _baseline(indexed, act)
    amenders, decisions, enacting = _read_evidence(_wanted(indexed, act), baseline)
    return Evidence(
        act=act, archives=tuple(i for _, i in indexed), baseline=enacting,
        amenders=_ordered(amenders), decisions=_ordered(decisions),
    )  # fmt: skip


def load_oracle(archive: Path, act: str) -> Document:
    """The consolidated member of ``act`` from a gjeldende-lover archive."""
    for member in iter_tarball_xml(archive):
        try:
            document = parse_document(member.content, member.name)
        except LegalTimeDocumentError:
            continue
        if document.header.refid == act:
            return document
    raise LegalTimeInputError(f"no consolidated member for {act} in {archive.name}")


def pins(archives: Iterable[Path]) -> list[dict[str, object]]:
    """Filename, size and sha256 of each input: the identity a report is reproducible from."""
    return [
        {"filename": p.name, "size_bytes": p.stat().st_size, "sha256": file_sha256(p)}
        for p in archives
    ]


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True)
