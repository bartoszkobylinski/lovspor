"""Reconcile the corpus on disk against what the manifest claims.

Every change-detection mechanism in the engine compares *upstream* against the
*manifest*. Nothing compares the *manifest* against *disk* — so a file that has
fallen out of the manifest is invisible to all of them and can never self-heal.

That gap let 48 documents accumulate in `lovverk` between 2026-05-19 and
2026-07-03: repealed regulations whose files were committed, never deleted, and
whose manifest records were later dropped. They surfaced only because a section
count failed to add up. This module is the check that would have caught them on
day one.
"""

from __future__ import annotations

import re
from itertools import islice
from pathlib import Path

from pydantic import BaseModel

from lovspor.headings import SECTION_HEADING
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.document_io import dataset_dir

_EMBEDDINGS_SUBDIR = "embeddings"
_DATASET_SUBDIRS = ("lover", "forskrifter")

ADVISORY_KINDS = frozenset({"unparsed_section_heading"})
"""Findings that are registered follow-up work, not corpus corruption.

An unparsed heading means a section the grammar cannot reach yet — real,
worth fixing, and 18 of them predate the CI gate. Exiting non-zero on them
would keep that gate permanently red, and a gate that is always red is one
nobody reads: exactly how the 48 orphans of 2026-05/07 stayed invisible.
Every kind NOT in this set is an INTEGRITY finding — the corpus contradicts
its own manifest — and blocks by default. Unknown kinds therefore classify
as integrity: a new check someone forgets to register here fails closed
instead of passing silently."""

INTEGRITY_KINDS = frozenset(
    {
        "duplicate_path_ownership",
        "identity_mismatch",
        "missing_document",
        "orphan_document",
        "orphan_embedding",
        "stale_render",
        "tombstoned_but_present",
    }
)
"""Every kind this module can emit that contradicts the manifest's own claims.

Documentation of the current inventory — classification itself keys on
:data:`ADVISORY_KINDS` (see there for why unknown kinds fail closed)."""

_FRONTMATTER_ID = re.compile(r'^id: "([^"]*)"$')
"""The ``id`` field exactly as ``serialize_frontmatter`` emits it: first
frontmatter field, double-quoted scalar. Anything else is not our id line."""

_FRONTMATTER_SCAN_LINES = 40
"""How deep into a file the identity check reads. The id is the *first*
frontmatter field by model declaration order, so this is generous headroom —
the point is that the audit never streams whole acts to compare one field."""

_LOOKS_LIKE_SECTION_HEADING = re.compile(r"^#{2,6} § ")
"""Deliberately looser than :data:`lovspor.headings.SECTION_HEADING`.

The audit compares the two: a line any reader would call a section
heading, that the real grammar cannot parse, is the finding. Keeping
this pattern independent is the point — deriving it from the grammar
under test would make the check vacuous."""

# `history/<slug>.{json,md}` is deliberately NOT audited for orphans, and this is
# not an oversight. A correct removal deletes the act's Markdown and its embedding
# sidecar but *keeps* its history — that file is the legal audit trail, the record
# that the act existed and was repealed. Tombstoned acts in the live corpus still
# carry theirs. Flagging history as orphaned would invite a cleanup that destroys
# exactly the evidence the corpus exists to preserve.


class AuditFinding(BaseModel):
    """One reconciliation failure between the manifest and disk."""

    model_config = {"frozen": True}

    kind: str
    path: str
    doc_id: str | None = None
    detail: str = ""


class AuditReport(BaseModel):
    """Result of one audit run. Ordered, so two runs on the same corpus are
    byte-identical and the output can be diffed or gated in CI."""

    model_config = {"frozen": True}

    corpus_root: str
    documents_checked: int
    findings: tuple[AuditFinding, ...]

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def integrity_findings(self) -> tuple[AuditFinding, ...]:
        """Findings that mean the corpus contradicts its own manifest.

        Membership is *exclusion* from :data:`ADVISORY_KINDS`, so a kind
        nobody classified blocks rather than slips through (see there)."""
        return tuple(f for f in self.findings if f.kind not in ADVISORY_KINDS)

    @property
    def advisory_findings(self) -> tuple[AuditFinding, ...]:
        """Findings that are registered follow-up work, not corruption."""
        return tuple(f for f in self.findings if f.kind in ADVISORY_KINDS)


def _relative(corpus_root: Path, path: Path) -> str:
    return path.relative_to(corpus_root).as_posix()


def _markdown_on_disk(corpus_root: Path) -> set[str]:
    """Every rendered act on disk, as a repo-relative path. INDEX.md is
    generated metadata, not an act, so it never participates."""
    found: set[str] = set()
    for subdir in _DATASET_SUBDIRS:
        directory = corpus_root / subdir
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            if path.name != "INDEX.md":
                found.add(_relative(corpus_root, path))
    return found


def _embeddings_on_disk(corpus_root: Path) -> set[str]:
    found: set[str] = set()
    for subdir in _DATASET_SUBDIRS:
        directory = corpus_root / subdir / _EMBEDDINGS_SUBDIR
        if not directory.is_dir():
            continue
        for path in directory.glob("*.bin"):
            found.add(_relative(corpus_root, path))
    return found


def _expected_embedding(corpus_root: Path, record: ManifestRecord) -> str | None:
    """The sidecar path a current record owns, or None when it cannot own one."""
    if record.slug is None:
        return None
    try:
        directory = dataset_dir(corpus_root, record.source_dataset)
    except ValueError:
        return None
    return _relative(corpus_root, directory / _EMBEDDINGS_SUBDIR / f"{record.slug}.bin")


def _live_markdown_paths(manifest: Manifest) -> set[str]:
    """Paths a *current* record renders to.

    Two acts can slugify to one path: a new regulation replacing an old one
    under the same short title gets a new doc id but the same markdown_path,
    and the old record is tombstoned. The file then belongs to the live act, so
    the removal correctly leaves it alone — a tombstone pointing here is not
    drift, and reporting it invites a "cleanup" that deletes text in force.
    """
    return {
        record.markdown_path for record in manifest.documents.values() if record.status == "current"
    }


def _expected_markdown(corpus_root: Path, record: ManifestRecord) -> str | None:
    """The path a record's slug renders to, or None when it cannot be derived."""
    if record.slug is None:
        return None
    try:
        directory = dataset_dir(corpus_root, record.source_dataset)
    except ValueError:
        return None
    return _relative(corpus_root, directory / f"{record.slug}.md")


def _duplicate_path_findings(corpus_root: Path, manifest: Manifest) -> list[AuditFinding]:
    """One path, one owner — violated in either direction.

    The 2026-07-14 omregningsfaktorer case: a replacement act slugified onto a
    tombstone's ``markdown_path``, leaving two manifest ids pointing at one
    file. The file itself is fine (it belongs to the live act — deleting it
    would remove text in force), but the shared pointer fuses two documents'
    identities in every path-keyed consumer, e.g. ``git log --follow`` history.
    The fix is always manifest-side, never a file deletion.
    """
    return _shared_path_findings(manifest) + _split_ownership_findings(corpus_root, manifest)


def _shared_path_findings(manifest: Manifest) -> list[AuditFinding]:
    """More than one manifest record — any status mix — on one markdown_path."""
    owners_by_path: dict[str, list[str]] = {}
    for doc_id, record in manifest.documents.items():
        owners_by_path.setdefault(record.markdown_path, []).append(doc_id)
    findings: list[AuditFinding] = []
    for path, owners in owners_by_path.items():
        if len(owners) == 1:
            continue
        current = [d for d in owners if manifest.documents[d].status == "current"]
        listed = ", ".join(f"{d} ({manifest.documents[d].status})" for d in sorted(owners))
        findings.append(
            AuditFinding(
                kind="duplicate_path_ownership",
                path=path,
                doc_id=current[0] if len(current) == 1 else None,
                detail=f"{len(owners)} manifest records resolve to this path: {listed}",
            ),
        )
    return findings


def _split_ownership_findings(corpus_root: Path, manifest: Manifest) -> list[AuditFinding]:
    """One current doc id effectively owning two paths.

    A current record whose ``markdown_path`` disagrees with what its own slug
    renders to claims one file while the next re-render will write another —
    the mirror image of two ids on one path, from a half-applied rename."""
    findings: list[AuditFinding] = []
    for doc_id, record in manifest.documents.items():
        if record.status != "current":
            continue
        expected = _expected_markdown(corpus_root, record)
        if expected is None or expected == record.markdown_path:
            continue
        findings.append(
            AuditFinding(
                kind="duplicate_path_ownership",
                path=record.markdown_path,
                doc_id=doc_id,
                detail=(
                    f"one doc id, two paths: manifest points at {record.markdown_path} "
                    f"but slug {record.slug!r} renders to {expected}"
                ),
            ),
        )
    return findings


def _frontmatter_id(path: Path) -> str | None:
    """The document id the file itself declares, or None when it declares none.

    Reads at most :data:`_FRONTMATTER_SCAN_LINES` lines — the id is the first
    frontmatter field, so a file whose id is not in that window has no id."""
    with path.open(encoding="utf-8") as handle:
        for line in islice(handle, _FRONTMATTER_SCAN_LINES):
            match = _FRONTMATTER_ID.match(line.rstrip("\n"))
            if match:
                return match.group(1)
    return None


def _identity_findings(
    corpus_root: Path,
    manifest: Manifest,
    on_disk: set[str],
) -> list[AuditFinding]:
    """On-disk frontmatter id versus the manifest id owning that path.

    This is the invariant the 2026-07-14 overwrite would have tripped had the
    add and the tombstone landed in the other order: a file whose frontmatter
    says it is one document, owned in the manifest by another. Files that
    declare no id at all are skipped — absence of evidence is not a mismatch,
    and inventing one would be a fabricated finding."""
    findings: list[AuditFinding] = []
    for doc_id, record in manifest.documents.items():
        if record.status != "current" or record.markdown_path not in on_disk:
            continue
        declared = _frontmatter_id(corpus_root / record.markdown_path)
        if declared is None or declared == doc_id:
            continue
        findings.append(
            AuditFinding(
                kind="identity_mismatch",
                path=record.markdown_path,
                doc_id=doc_id,
                detail=f'file frontmatter declares id "{declared}" but the manifest '
                f"record owning this path is {doc_id}",
            ),
        )
    return findings


def _document_findings(
    manifest: Manifest,
    on_disk: set[str],
    renderer_version: int | None,
) -> list[AuditFinding]:
    """Drift in both directions between manifest records and rendered files."""
    live_paths = _live_markdown_paths(manifest)
    findings: list[AuditFinding] = []
    for doc_id, record in manifest.documents.items():
        path = record.markdown_path
        present = path in on_disk
        if record.status == "removed" and present and path not in live_paths:
            findings.append(
                AuditFinding(
                    kind="tombstoned_but_present",
                    path=path,
                    doc_id=doc_id,
                    detail="record is tombstoned but its file was never deleted",
                ),
            )
        elif record.status == "current" and not present:
            findings.append(
                AuditFinding(
                    kind="missing_document",
                    path=path,
                    doc_id=doc_id,
                    detail="manifest lists this act as current but the file is absent",
                ),
            )
        elif record.status == "current" and _is_stale_render(record, renderer_version):
            findings.append(
                AuditFinding(
                    kind="stale_render",
                    path=path,
                    doc_id=doc_id,
                    detail=(
                        f"rendered by renderer v{record.renderer_version}, "
                        f"current is v{renderer_version}"
                    ),
                ),
            )
    return findings


def _is_stale_render(record: ManifestRecord, renderer_version: int | None) -> bool:
    if renderer_version is None or record.renderer_version is None:
        return False
    return record.renderer_version < renderer_version


def _orphan_findings(
    corpus_root: Path, manifest: Manifest, on_disk: set[str]
) -> list[AuditFinding]:
    """Files on disk that no manifest record — of any status — accounts for."""
    claimed = {record.markdown_path for record in manifest.documents.values()}
    orphan_docs = [
        AuditFinding(
            kind="orphan_document",
            path=path,
            detail="file is in the corpus but no manifest record refers to it",
        )
        for path in sorted(on_disk - claimed)
    ]
    owned = {
        expected
        for record in manifest.documents.values()
        if record.status == "current"
        and (expected := _expected_embedding(corpus_root, record)) is not None
    }
    orphan_bins = [
        AuditFinding(
            kind="orphan_embedding",
            path=path,
            detail="embedding sidecar with no current manifest record",
        )
        for path in sorted(_embeddings_on_disk(corpus_root) - owned)
    ]
    return orphan_docs + orphan_bins


def _unparsed_heading_findings(corpus_root: Path) -> list[AuditFinding]:
    """Flag heading lines the section grammar cannot read.

    The renderer writes section headings and two parsers read them back —
    ``lovspor.mcp`` for get_section/list_sections and
    ``lovspor.embeddings.sections`` for the vector index. Nothing compared
    the two halves of that round trip, so a heading shape the grammar did
    not know simply vanished: no warning, no partial result, and an
    available-sections error message that omitted it while reading as
    authoritative. 2 347 headings were unreachable that way, including
    arbeidsmiljøloven's entire kapittel 2 A.

    A future Lovdata sync can introduce a new shape the same way. This is
    the check that turns that into a visible finding on the next audit
    rather than a silent hole discovered by a user who asked for a section
    that was there all along.
    """
    findings: list[AuditFinding] = []
    for subdir in _DATASET_SUBDIRS:
        for path in sorted((corpus_root / subdir).glob("*.md")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not _LOOKS_LIKE_SECTION_HEADING.match(line) or SECTION_HEADING.match(line):
                    continue
                findings.append(
                    AuditFinding(
                        kind="unparsed_section_heading",
                        path=_relative(corpus_root, path),
                        detail=f"line {number}: {line.strip()[:120]}",
                    ),
                )
    return findings


def audit_corpus(
    corpus_root: Path,
    manifest: Manifest,
    renderer_version: int | None = None,
) -> AuditReport:
    """Reconcile ``corpus_root`` against ``manifest``.

    ``renderer_version`` enables the stale-render check; omit it to skip that
    dimension (the other checks do not need to know about rendering).

    Findings are sorted by (kind, path) so a run is reproducible and diffable.
    Classification into blocking and non-blocking is the *caller's* concern,
    via :attr:`AuditReport.integrity_findings` / ``advisory_findings`` — this
    function reports everything it sees.
    """
    on_disk = _markdown_on_disk(corpus_root)
    findings = _document_findings(manifest, on_disk, renderer_version)
    findings += _duplicate_path_findings(corpus_root, manifest)
    findings += _identity_findings(corpus_root, manifest, on_disk)
    findings += _orphan_findings(corpus_root, manifest, on_disk)
    findings += _unparsed_heading_findings(corpus_root)
    findings.sort(key=lambda f: (f.kind, f.path))
    return AuditReport(
        corpus_root=str(corpus_root),
        documents_checked=sum(1 for r in manifest.documents.values() if r.status == "current"),
        findings=tuple(findings),
    )
