"""AR4a contracts: one publication transaction, prepared offline and never published (#738; §9.3).

A **publication transaction** is the exact proposal of one AR2 batch for one
publication path, against one base commit of ``lovverk-forarbeider``:

- it is bound to the sealed plan (``plan_id``), the batch (``batch_id``) and
  the AR3 quarantine set the plan honoured (``quarantine_sha256``), and names,
  document by document, the selection and binding digests the plan planned;
- it names the **publication path**, kept apart by type: path A
  (:class:`PathA`, the GitHub App's ``createCommitOnBranch`` with
  ``expectedHeadOid``, signature class ``github_app``) or path B
  (:class:`PathB`, a bot-key-signed commit and fast-forward push, signature
  class ``bot_key``). A transaction prepared for one path is never the other's:
  the path is part of the content, so of the id and the release manifest;
- it lists every proposed file with its digest, size and the digest it
  replaces, and the counts **recounted from the rendered bytes** (AR2 counted
  the bound private artifacts, ``size_basis: bound_artifacts``);
- ``transaction_id`` is the digest of the content, so the same inputs give the
  same id and any change gives another.

**The publication tree** of ``lovverk-forarbeider`` (§9.7) admits exactly
four kinds of file, by grammar (:func:`publication_path`): a document
``documents/regjeringen-<id>.md``, ``INDEX.md``, the membership list
``catalogue.json`` and one release manifest ``releases/<sha256>.json`` per
batch, named by the digest of its own bytes. A preparation that would touch
any other path is refused.

**The manifest's signature** (AR6b) is published beside it as
``releases/<sha256>.sig`` (:data:`RELEASE_SIGNATURE_FILE`), the exact AR6a
SSHSIG over the manifest's bytes. It is never a proposed file: a transaction
does not list it, so neither its digest nor the manifest's depends on it (the
signature is made after preparation, over the manifest the transaction names).
The commit publishes exactly the transaction's files and this one companion;
a base tree may hold the companions of earlier batches.

**Nothing here is an authorization.** ``authorized`` and ``published`` are
``False`` by type. What a preparation cannot check, because AR4b to AR7 do not
exist yet, is listed in ``unavailable_checks`` and never reported as passed;
the release-wide blockers the plan carried (the unanchored head, D10, D11)
are carried on unchanged.

**The journal** (:class:`PreparationJournal`) records one preparation's
state, the transaction and the operations derived from it; it can describe
no other change, so a journal edited on disk is refused rather than followed.
"""

import re
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.release_plan_models import BatchCaps
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of

TRANSACTION_VERSION: Final = "publication-transaction/1"
REPOSITORY: Final = "bartoszkobylinski/lovverk-forarbeider"
INDEX_FILE: Final = "INDEX.md"
CATALOGUE_FILE: Final = "catalogue.json"
DOCUMENT_FILE: Final = re.compile(r"documents/regjeringen-[1-9]\d{0,11}\.md")
RELEASE_FILE: Final = re.compile(r"releases/[0-9a-f]{64}\.json")
RELEASE_SIGNATURE_FILE: Final = re.compile(r"releases/[0-9a-f]{64}\.sig")
#: Checks a preparation cannot make, and the slice that will; never reported as passed.
UNAVAILABLE_CHECKS: Final = (
    "ar4b_publication_authorization_not_implemented",
    "ar4b_remote_main_not_compared",
    "ar5_budget_reservation_not_implemented",
    "ar5_kill_switch_not_implemented",
    "ar6_release_manifest_signature_not_implemented",
    "ar7_repository_settings_not_verified",
)

CommitId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
FileRole = Literal["document", "index", "catalogue", "release_manifest"]
PreparationState = Literal[
    "staging",
    "staged",
    "documents_written",
    "index_written",
    "manifest_written",
    "reading_back",
    "prepared",
    "rolling_back",
]
#: The order in which a preparation advances; ``rolling_back`` only ever goes back.
FORWARD: Final[tuple[PreparationState, ...]] = (
    "staging",
    "staged",
    "documents_written",
    "index_written",
    "manifest_written",
    "reading_back",
    "prepared",
)


def role_of(path: str) -> FileRole | None:
    """The role ``path`` has in the publication tree, ``None`` outside it."""
    if path == INDEX_FILE:
        return "index"
    if path == CATALOGUE_FILE:
        return "catalogue"
    if DOCUMENT_FILE.fullmatch(path):
        return "document"
    if RELEASE_FILE.fullmatch(path):
        return "release_manifest"
    return None


def release_signature_path(manifest_sha256: str) -> str:
    """The published companion holding the bot's signature on the manifest with this digest."""
    return f"releases/{manifest_sha256}.sig"


def publication_path(path: str) -> bool:
    return role_of(path) is not None


def document_file(document_id: str) -> str:
    return f"documents/{document_id}.md"


# --- the two publication paths, never mixed --------------------------------------------------


class PathA(Frozen):
    """Path A: GitHub creates the commit and moves ``main`` in one mutation (§9.3 A6)."""

    path: Literal["A"] = "A"
    mechanism: Literal["github_app_create_commit_on_branch"] = "github_app_create_commit_on_branch"
    expected_head_oid: CommitId
    signature_class: Literal["github_app"] = "github_app"

    @property
    def base_commit(self) -> str:
        return self.expected_head_oid


class PathB(Frozen):
    """Path B: a bot-key-signed local commit and a fast-forward push (§9.3 B6-B9)."""

    path: Literal["B"] = "B"
    mechanism: Literal["bot_key_signed_fast_forward_push"] = "bot_key_signed_fast_forward_push"
    base_commit: CommitId
    signature_class: Literal["bot_key"] = "bot_key"


Publication = Annotated[PathA | PathB, Field(discriminator="path")]


def publication_for(path: Literal["A", "B"], base_commit: str) -> PathA | PathB:
    return PathA(expected_head_oid=base_commit) if path == "A" else PathB(base_commit=base_commit)


# --- proposed bytes --------------------------------------------------------------------------


class ProposedFile(Frozen):
    """One file the proposal writes, its digest, size and the digest it replaces."""

    path: NonBlank
    role: FileRole
    sha256: Digest
    size_bytes: int = Field(ge=1)
    previous_sha256: Digest | None

    @model_validator(mode="after")
    def _in_the_tree(self) -> Self:
        if role_of(self.path) != self.role:
            raise ValueError("a proposed file is a publication file of its role")
        return self


class DocumentPublication(Frozen):
    """One document version of the batch: what the plan bound and what was rendered."""

    document_id: NonBlank
    version_id: Digest
    selection_sha256: Digest
    binding_sha256: Digest
    manifest_sha256: Digest
    policy_decision_sha256: Digest
    path: NonBlank
    sha256: Digest
    size_bytes: int = Field(ge=1)
    previous_sha256: Digest | None

    @model_validator(mode="after")
    def _its_file(self) -> Self:
        if self.path != document_file(self.document_id):
            raise ValueError("a document is rendered to its own file")
        return self


class ProposalCounts(Frozen):
    """The proposal as rendered: documents, files and bytes, the manifest included."""

    documents: int = Field(ge=1)
    files: int = Field(ge=1)
    bytes: int = Field(ge=1)


def within_caps(counts: ProposalCounts, caps: BatchCaps) -> bool:
    return (
        counts.documents <= caps.documents
        and counts.files <= caps.files
        and counts.bytes <= caps.bytes
    )


def counts_of(files: tuple[ProposedFile, ...], documents: int) -> ProposalCounts:
    return ProposalCounts(
        documents=documents, files=len(files), bytes=sum(item.size_bytes for item in files)
    )


class CatalogueEntry(Frozen):
    """One published document version, as the catalogue lists it; no text but the title."""

    document_id: NonBlank
    version_id: Digest
    document_type: NonBlank
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    title: NonBlank
    source_url: NonBlank
    path: NonBlank
    sha256: Digest
    size_bytes: int = Field(ge=1)
    batch_id: str = Field(pattern=r"^batch-[0-9a-f]{56}$")


class Catalogue(Frozen):
    """``catalogue.json``: every published document, the only thing that makes a file a member."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_catalogue"] = "forarbeider_publication_catalogue"
    normative: Literal[False] = False
    basis: Literal["åndsverkloven § 14"] = "åndsverkloven § 14"
    source_license: None = None
    documents: tuple[CatalogueEntry, ...] = ()

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        ids = [item.document_id for item in self.documents]
        if ids != sorted(set(ids)):
            raise ValueError("catalogue documents are sorted by id, once each")
        if any(item.path != document_file(item.document_id) for item in self.documents):
            raise ValueError("a catalogued document lives in its own file")
        return self


class ReleaseManifestFile(Frozen):
    """``releases/<sha256>.json``: what one batch adds, named by its own digest (§9.3 step 2)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_release"] = "forarbeider_publication_release"
    transaction_version: Literal["publication-transaction/1"] = TRANSACTION_VERSION
    repository: Literal["bartoszkobylinski/lovverk-forarbeider"] = REPOSITORY
    publication: Publication
    plan_id: Digest
    batch_id: str = Field(pattern=r"^batch-[0-9a-f]{56}$")
    batch_sequence: int = Field(ge=1)
    policy: PolicyReference
    inventory_sha256: Digest
    limits_sha256: Digest
    quarantine_sha256: Digest
    documents: tuple[DocumentPublication, ...] = Field(min_length=1)
    files: tuple[ProposedFile, ...]
    source_sha256s: tuple[Digest, ...]
    signature: Literal["not_signed_ar6_pending"] = "not_signed_ar6_pending"
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require_documents(self.documents)
        if any(item.role == "release_manifest" for item in self.files):
            raise ValueError("a release manifest never lists itself")
        return self


def _require_documents(documents: tuple[DocumentPublication, ...]) -> None:
    ids = [item.document_id for item in documents]
    if ids != sorted(set(ids)):
        raise ValueError("documents are sorted by id, once each")


# --- the transaction -----------------------------------------------------------------------


class TransactionContent(Frozen):
    """Everything a transaction binds; ``transaction_id`` is its digest."""

    transaction_version: Literal["publication-transaction/1"] = TRANSACTION_VERSION
    repository: Literal["bartoszkobylinski/lovverk-forarbeider"] = REPOSITORY
    branch: Literal["main"] = "main"
    publication: Publication
    plan_id: Digest
    batch_id: str = Field(pattern=r"^batch-[0-9a-f]{56}$")
    batch_sequence: int = Field(ge=1)
    quarantine_sha256: Digest
    base_catalogue_sha256: Digest | None
    documents: tuple[DocumentPublication, ...] = Field(min_length=1)
    release_manifest_path: NonBlank
    release_manifest_sha256: Digest
    files: tuple[ProposedFile, ...] = Field(min_length=4)
    counts: ProposalCounts
    pending_release_blockers: tuple[NonBlank, ...]
    unavailable_checks: tuple[NonBlank, ...] = UNAVAILABLE_CHECKS
    authorized: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require_documents(self.documents)
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("files are sorted by path, once each")
        if self.release_manifest_path != f"releases/{self.release_manifest_sha256}.json":
            raise ValueError("the release manifest is named by its own digest")
        if self.counts != counts_of(self.files, len(self.documents)):
            raise ValueError("counts follow from the proposed files")
        _require_files_cover(self)
        return self


def _require_files_cover(content: TransactionContent) -> None:
    by_path = {item.path: item for item in content.files}
    wanted = {INDEX_FILE, CATALOGUE_FILE, content.release_manifest_path}
    wanted |= {item.path for item in content.documents}
    if set(by_path) != wanted:
        raise ValueError(
            "files are exactly the documents, the index, the catalogue and the manifest"
        )
    for item in content.documents:
        found = by_path[item.path]
        if (found.sha256, found.size_bytes, found.previous_sha256) != (
            item.sha256,
            item.size_bytes,
            item.previous_sha256,
        ):
            raise ValueError("a document's file is the one rendered")


class PublicationTransaction(Frozen):
    """One prepared, unauthorized, unpublished proposal; its id is its content's digest."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_transaction"] = (
        "forarbeider_publication_transaction"
    )
    transaction_id: Digest
    content: TransactionContent

    @model_validator(mode="after")
    def _identified(self) -> Self:
        if self.transaction_id != digest_of(self.content):
            raise ValueError("transaction_id is the digest of the content")
        return self


def transaction_of(content: TransactionContent) -> PublicationTransaction:
    return PublicationTransaction(transaction_id=digest_of(content), content=content)


# --- the journal ---------------------------------------------------------------------------


class JournalOperation(Frozen):
    """Make ``path`` hold ``sha256``; ``previous_sha256`` is what it held (``None``: absent)."""

    path: NonBlank
    sha256: Digest
    previous_sha256: Digest | None

    @model_validator(mode="after")
    def _in_the_tree(self) -> Self:
        if not publication_path(self.path):
            raise ValueError("an operation names a publication file only")
        return self


_PHASE: Final[dict[FileRole, int]] = {
    "document": 0,
    "index": 1,
    "catalogue": 1,
    "release_manifest": 2,
}


def operations_of(transaction: PublicationTransaction) -> tuple[JournalOperation, ...]:
    """Documents, then the index and catalogue, then the manifest: the only order there is."""
    ordered = sorted(transaction.content.files, key=lambda item: (_PHASE[item.role], item.path))
    return tuple(
        JournalOperation(path=item.path, sha256=item.sha256, previous_sha256=item.previous_sha256)
        for item in ordered
    )


def phase_of(path: str) -> int:
    role = role_of(path)
    if role is None:
        raise ValueError("not a publication file")
    return _PHASE[role]


class PreparationJournal(Frozen):
    """One preparation's state and operations; it describes its own transaction only."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_journal"] = "forarbeider_publication_journal"
    state: PreparationState
    transaction: PublicationTransaction
    operations: tuple[JournalOperation, ...]

    @model_validator(mode="after")
    def _its_own(self) -> Self:
        if self.operations != operations_of(self.transaction):
            raise ValueError("a journal's operations are its transaction's")
        return self


class PreparationHalt(Frozen):
    """What a halted preparation leaves for the AR5 stop handling: the plan, batch and failures."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_preparation_halt"] = "forarbeider_preparation_halt"
    plan_id: Digest | None
    batch_id: NonBlank
    publication_path: Literal["A", "B"]
    base_commit: NonBlank
    halts: tuple[NonBlank, ...] = Field(min_length=1)
    documents: tuple[NonBlank, ...]
    stop_record_written: Literal[False] = False
