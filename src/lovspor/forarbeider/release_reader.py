"""The receipt-verified reader of a real lovverk release (#738, contract Amendment 1, B2).

Read-only. It serves a forarbeider document from a real lovverk checkout only
when every check below holds, and refuses with a fixed code otherwise. It
never writes the checkout, Git or the workspace, never reaches the network,
and starts no process but the read-only Git of :mod:`lovspor.git_read`.

**The checkout and the publication.**

- The B1a guard, on ``main``: a canonical, owner-only, dedicated lovverk clone
  (never the engine, the sync clone or ``$LOVVERK_CORPUS_PATH``), trusted
  origin and root commit, safe config, no Git operation, **no forarbeider
  lock, journal or staging**, a clean index and worktree with nothing
  untracked, and the dataset exactly its manifest.
- HEAD is the commit ``refs/remotes/origin/main`` names: the release is
  committed and was on lovverk ``main`` when the clone last fetched. A
  working tree, a local commit or an unmerged branch is never publication.
- The guard runs again after the dataset is read; a change in between refuses.

**The dataset.** Manifest schema 2 only, in its canonical bytes (version 1
was written only by the synthetic sandbox; any other version refuses). Every
member path has the dataset grammar, every file its recorded SHA-256, the
index is the manifest's. Each released document's front matter restates its
member: id, labels, coverage, listed and released chapters and their text
digests. Withdrawn members are never served, whatever Git history holds.

**The owner's records (the trust mechanism).** Private receipts are read only
from the owner-only ``--workspace`` the release was made with; without it,
or without a record, the reader refuses, never guesses. For each released
document: exactly one stored batch confirmation wrote its file with these
bytes; its receipt is exactly the one ``batch apply`` writes; the stored plan
it names includes exactly these chapters with exactly these clearances; the
binding lists each chapter with its text digest; the owner's moment is the
document's; and every record a clearance cites (the standing policy, or the
review report and authorization) is stored intact. These are integrity checks
of the owner's own unauthenticated records, never authentication: a digest
proves bytes, not who made them.
"""

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider.batch_apply import receipt_of
from lovspor.forarbeider.batch_models import BatchPlan, PlannedChapter, PlannedDocument
from lovspor.forarbeider.batch_records import BatchConfirmation, BatchReleaseReceipt
from lovspor.forarbeider.corpus_models import (
    MANIFEST_PATH,
    ChapterReceiptV2,
    ForarbeiderManifestV2,
    ManifestDocumentV2,
    PolicyClearance,
    ReviewClearance,
)
from lovspor.forarbeider.corpus_render import NOT_LAW
from lovspor.forarbeider.corpus_tree import _read_public, read_any_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_models import StandingPolicy
from lovspor.forarbeider.release_checkout import CheckoutState, require_published, verify_checkout
from lovspor.forarbeider.release_commands import stored_record
from lovspor.forarbeider.release_models import (
    SHA256,
    PreflightReport,
    ReleaseAuthorization,
    digest,
    record_text,
)
from lovspor.forarbeider.release_store import read_named
from lovspor.forarbeider.staging import private_directory_exists

MAIN = "main"
_MANIFEST_V2 = 2
_DIGEST = re.compile(SHA256)
_FRONT = "---"


class VerifiedRelease(NamedTuple):
    """One released document whose bytes and owner records all agree."""

    member: ManifestDocumentV2
    markdown: str
    confirmation_sha256: str
    receipt_sha256: str
    plan_sha256: str


class ReleaseShelf(NamedTuple):
    """What a verified checkout serves: its state, manifest digest and released documents."""

    state: CheckoutState
    manifest_sha256: str | None
    releases: tuple[VerifiedRelease, ...]


def _mismatch(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RECEIPT_MISMATCH, detail)


def require_v2(checkout: Path, sha: str | None) -> None:
    """The manifest the dataset read verified is schema 2, re-read without following a link.

    Raises:
        ForarbeiderRefusedError: ``manifest_incompatible: manifest`` for another
            version, ``corpus_tree_mismatch: manifest`` when the bytes moved.
    """
    if sha is None:
        return
    raw = _read_public(checkout / MANIFEST_PATH)
    if digest(raw) != sha:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
    if json.loads(raw.decode()).get("schema_version") != _MANIFEST_V2:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")


def _stored_confirmations(workspace: Path) -> list[tuple[str, BatchConfirmation]]:
    """Every stored confirmation, by the name it is stored under; links are refused on read."""
    directory = workspace / "batch_confirmations"
    if not private_directory_exists(directory):
        return []
    names = sorted(entry.name.removesuffix(".json") for entry in os.scandir(directory))
    found = []
    for name in names:
        if _DIGEST.fullmatch(name):
            record = read_named(workspace, "batch_confirmations", name, BatchConfirmation)
            if record is not None:
                found.append((name, record))
    return found


def confirmation_of(workspace: Path, member: ManifestDocumentV2) -> BatchConfirmation:
    """The one stored confirmation that wrote this member's file with these bytes.

    Raises:
        ForarbeiderRefusedError: ``release_record_missing: receipt`` for none,
            ``receipt_mismatch: ambiguous`` for more than one, or ``documents``.
    """
    written = (member.path, member.markdown_sha256)
    matches = [
        record
        for _, record in _stored_confirmations(workspace)
        if any((item.path, item.sha256) == written for item in record.writes)
    ]
    if not matches:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, "receipt")
    if len(matches) > 1:
        raise _mismatch("ambiguous")
    if member.document.id not in matches[0].documents:
        raise _mismatch("documents")
    return matches[0]


def require_receipt(workspace: Path, confirmation: BatchConfirmation) -> tuple[str, str]:
    """The confirmation's digest and its receipt's, which must be exactly the one apply writes.

    Raises:
        ForarbeiderRefusedError: ``release_record_missing: receipt``, ``receipt_mismatch: receipt``.
    """
    sha = digest(record_text(confirmation).encode())
    stored = read_named(workspace, "batch_releases", sha, BatchReleaseReceipt)
    if stored is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, "receipt")
    if stored != receipt_of(confirmation, sha):
        raise _mismatch("receipt")
    return sha, digest(record_text(stored).encode())


def _planned(plan: BatchPlan, member: ManifestDocumentV2) -> PlannedDocument:
    found = next((item for item in plan.documents if item.document == member.document), None)
    if found is None or found.destination != member.path:
        raise _mismatch("plan")
    return found


def _same_chapter(receipt: ChapterReceiptV2, planned: PlannedChapter) -> bool:
    return (
        receipt.chapter_number,
        receipt.chapter_id,
        receipt.source_page_url,
        receipt.source_sha256,
        receipt.text_sha256,
        receipt.clearance,
    ) == (
        planned.chapter_number,
        planned.chapter_id,
        planned.source_page_url,
        planned.source_sha256,
        planned.text_sha256,
        planned.clearance,
    )


def require_planned(
    member: ManifestDocumentV2, plan: BatchPlan, confirmation: BatchConfirmation
) -> None:
    """The member's chapters are exactly the plan's included ones, clearance for clearance.

    Raises:
        ForarbeiderRefusedError: ``receipt_mismatch: plan|chapters|binding|times``.
    """
    included = [item for item in _planned(plan, member).chapters if item.included]
    pairs = list(zip(member.chapters, included, strict=False))
    if len(included) != len(member.chapters) or not all(_same_chapter(*p) for p in pairs):
        raise _mismatch("chapters")
    bound = set(confirmation.binding.included)
    if any(f"{item.chapter_id}@{item.text_sha256}" not in bound for item in member.chapters):
        raise _mismatch("binding")
    times = member.times
    if not times.owner_decided_at == confirmation.decided_at == times.released_at:
        raise _mismatch("times")


def require_cited(workspace: Path, member: ManifestDocumentV2) -> None:
    """Every record a clearance cites is stored and intact: nothing is taken on its word.

    Raises:
        ForarbeiderRefusedError: ``release_record_missing`` or ``release_record_corrupt``.
    """
    for clearance in {item.clearance for item in member.chapters}:
        if isinstance(clearance, PolicyClearance):
            stored_record(workspace, "policies", clearance.policy_sha256, StandingPolicy)
        elif isinstance(clearance, ReviewClearance):
            if clearance.report_sha256 is None or clearance.authorization_sha256 is None:
                raise _mismatch("clearance")
            stored_record(workspace, "reports", clearance.report_sha256, PreflightReport)
            authorization = clearance.authorization_sha256
            stored_record(workspace, "authorizations", authorization, ReleaseAuthorization)


def front_matter(markdown: str) -> dict[str, object]:
    """The rendering's front matter: one ``key: JSON value`` per line between the fences.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: front_matter``.
    """
    lines = markdown.split("\n")
    try:
        end = lines.index(_FRONT, 1)
        if lines[0] != _FRONT:
            raise ValueError("no front matter")
        pairs = [line.split(": ", 1) for line in lines[1:end]]
        return {key: json.loads(value) for key, value in pairs}
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error


def require_restated(member: ManifestDocumentV2, markdown: str) -> None:
    """The file says what its member says: identity, labels, coverage, chapters, digests.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: front_matter``.
    """
    expected = {
        "id": member.document.id,
        "source_document_type": "forarbeid",
        "normative": False,
        "source_license": None,
        "rights_basis": "åndsverkloven § 14",
        "release_status": "released",
        "coverage": member.coverage,
        "asserts_complete_proposition": False,
        "listed_chapters": list(member.listed_chapters),
        "released_chapters": [item.chapter_number for item in member.chapters],
        "chapter_text_sha256": [item.text_sha256 for item in member.chapters],
    }
    found = front_matter(markdown)
    if (
        any(found.get(key, ...) != value for key, value in expected.items())
        or NOT_LAW not in markdown
    ):
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter")


def verify_release(workspace: Path, member: ManifestDocumentV2, markdown: bytes) -> VerifiedRelease:
    """One released document checked against its file and every owner record behind it.

    Raises:
        ForarbeiderRefusedError: the first record or byte that does not agree.
    """
    text = markdown.decode("utf-8")
    require_restated(member, text)
    confirmation = confirmation_of(workspace, member)
    confirmed, receipt = require_receipt(workspace, confirmation)
    plan = stored_record(workspace, "batch_plans", confirmation.plan_sha256, BatchPlan)
    require_planned(member, plan, confirmation)
    require_cited(workspace, member)
    return VerifiedRelease(member, text, confirmed, receipt, confirmation.plan_sha256)


def _released(manifest: ForarbeiderManifestV2) -> list[ManifestDocumentV2]:
    return [member for member in manifest.documents if member.release_status == "released"]


def open_release_shelf(workspace: Path, checkout: Path) -> ReleaseShelf:
    """The verified released documents of a published lovverk ``main``; nothing is written.

    Raises:
        ForarbeiderRefusedError: the guard's, the publication's, the dataset's or
            a record's refusal; ``corpus_tree_mismatch: concurrent`` for a change
            while reading.
    """
    state = verify_checkout(checkout, MAIN)
    require_published(checkout, state.head_commit)
    dataset, files = read_any_dataset_files(checkout)
    if dataset.manifest_sha256 != state.base_manifest_sha256:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
    require_v2(checkout, dataset.manifest_sha256)
    releases = tuple(
        verify_release(workspace, member, files[member.path])
        for member in _released(dataset.manifest)
    )
    if verify_checkout(checkout, MAIN) != state:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
    return ReleaseShelf(state, dataset.manifest_sha256, releases)


def find_release(shelf: ReleaseShelf, document: str) -> VerifiedRelease:
    """Raises: ForarbeiderRefusedError: ``document_not_released: document``."""
    found = next((item for item in shelf.releases if item.member.document.id == document), None)
    if found is None:
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    return found


def chapter_markdown(release: VerifiedRelease, number: int) -> tuple[ChapterReceiptV2, str]:
    """One released chapter's Markdown, cut from the verified file at its source line.

    Each chapter of a rendering opens with ``Source: <its page URL>``; chapter
    text escapes ``<``, so no text line can open one.

    Raises:
        ForarbeiderRefusedError: ``chapter_not_released: chapter``.
    """
    receipt = next((i for i in release.member.chapters if i.chapter_number == number), None)
    if receipt is None:
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NOT_RELEASED, "chapter")
    lines = release.markdown.split("\n")
    opening = f"Source: <{receipt.source_page_url}>"
    if lines.count(opening) != 1:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
    start = lines.index(opening)
    rest = [
        index for index, line in enumerate(lines) if index > start and line.startswith("Source: <")
    ]
    end = rest[0] if rest else len(lines)
    return receipt, "\n".join(lines[start:end]).rstrip("\n") + "\n"
