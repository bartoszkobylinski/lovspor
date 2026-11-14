"""The reviewed owner-exception list, schema 2: evidence-bound, approved by digest (#921).

``owner_reassignments.json`` lists every historical owner the #871 migration of
lovverk changes (ADR-0026 draft). Owner decision on #921, option (a): the
served owner stays as the public URLs serve it, and the first owner is kept
beside it, never overwritten:

- ``reassignments``: an address whose owner at ``corpus.reviewed_base`` (the
  migration's base) changes: the U1 renames and the slug-history repairs;
- ``threshold_exceptions``: a ``threshold-exception`` entry, where
  ``served_owner_doc_id`` keeps an address ``first_owner_doc_id`` held first. Each
  carries the Git evidence the gate re-reads from the corpus
  (``owner_evidence``): the commit where the first owner first held the
  address, and where the served owner first did. ``advisory`` holds downstream
  HTTP readings lovspor cannot check;
- ``approval``: the owner's sign-off on ``list_digest``, the sha256 of the list
  without its ``approval``. Pending until the owner runs
  ``lovspor ops identity-exceptions-approve``; any later edit voids it, and a
  void or pending approval lists nothing (fail closed).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import IdentitySchemaError

REASSIGNMENTS_PATH = Path(__file__).with_name("owner_reassignments.json")
LIST_SCHEMA_VERSION = 2
PENDING: Literal["pending-owner-approval"] = "pending-owner-approval"
APPROVED: Literal["approved"] = "approved"
Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, StringConstraints(min_length=1)]
Address = tuple[str, str]
#: ``address -> (owner at the base, owner in the registry)``.
Allowed = dict[Address, tuple[str, str]]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReviewedCorpus(_Frozen):
    """The corpus revisions the list was reviewed against."""

    reviewed_base: Sha
    u1_threshold: Sha
    plan_sums_sha256: Digest


class OwnerReassignment(_Frozen):
    """One reviewed owner change of an address the reviewed base published."""

    directory: Text
    slug: Text
    from_doc_id: Text
    to_doc_id: Text
    kind: Literal["u1-rename", "history-repair"]
    reason: Text


class ThresholdEvidence(_Frozen):
    """The commits establishing both owners; re-read from the corpus by the gate."""

    first_owner_first_commit: Sha
    served_owner_first_commit: Sha


class DownstreamReading(_Frozen):
    """An HTTP reading of the address downstream: advisory, not checkable from lovspor."""

    lovverk_commit: Sha
    http_status: Text
    in_sitemap: bool | None
    source: Text


class ThresholdException(_Frozen):
    """A ``threshold-exception`` entry: the served owner keeps an address the first
    owner held first."""

    directory: Text
    slug: Text
    first_owner_doc_id: Text
    served_owner_doc_id: Text
    evidence: ThresholdEvidence
    advisory: tuple[DownstreamReading, ...] = ()
    reason: Text

    @model_validator(mode="after")
    def _two_owners(self) -> "ThresholdException":
        if self.first_owner_doc_id == self.served_owner_doc_id:
            raise ValueError(f"{self.directory}/{self.slug}: first and served owner are one")
        return self


class Approval(_Frozen):
    """The owner's digest-bound sign-off; every field empty while pending."""

    status: Literal["pending-owner-approval", "approved"]
    digest: Digest | None = None
    approved_by: Text | None = None
    approved_on: date | None = None

    @model_validator(mode="after")
    def _complete_exactly_when_approved(self) -> "Approval":
        fields = (self.digest, self.approved_by, self.approved_on)
        if self.status == APPROVED and None in fields:
            raise ValueError("an approval names its digest, approver and date")
        if self.status == PENDING and fields != (None, None, None):
            raise ValueError("a pending approval carries no digest, approver or date")
        return self


class ReassignmentList(_Frozen):
    schema_version: Literal[2]
    corpus: ReviewedCorpus
    reassignments: tuple[OwnerReassignment, ...]
    threshold_exceptions: tuple[ThresholdException, ...]
    approval: Approval


@dataclass(frozen=True)
class Reviewed:
    """The list as the gate uses it: entries count only when ``approved``."""

    path: Path
    changes: Allowed
    threshold: dict[Address, ThresholdException]
    corpus: ReviewedCorpus
    digest: str
    approved: bool
    state: str


def list_digest(listed: ReassignmentList) -> str:
    """sha256 of the list without ``approval``: canonical JSON (sorted keys, no
    whitespace, UTF-8), so a formatting change keeps it and any content edit voids it."""
    data = listed.model_dump(mode="json", exclude={"approval"})
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def list_json(listed: ReassignmentList) -> str:
    """The list as written: sorted keys, two-space indent, final newline."""
    data = listed.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def read_list(path: Path) -> ReassignmentList:
    try:
        return ReassignmentList.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise IdentitySchemaError(f"{path}: unreadable owner reassignment list: {exc}") from exc


def _unique[V](pairs: list[tuple[Address, V]], path: Path) -> dict[Address, V]:
    allowed = dict(pairs)
    if len(allowed) != len(pairs):
        raise IdentitySchemaError(f"{path}: an address is listed twice")
    return allowed


def approval_state(listed: ReassignmentList, digest: str) -> tuple[bool, str]:
    """``(approved, why)``: approved only with a matching digest."""
    approval = listed.approval
    if approval.status == PENDING:
        return False, f"BLOCKED: pending owner approval of digest {digest}"
    if approval.digest != digest:
        return False, f"BLOCKED: approval digest {approval.digest} is not the list's {digest}"
    return True, f"approved by {approval.approved_by} on {approval.approved_on} ({digest})"


def load_reassignments(path: Path | None = None) -> Reviewed:
    """The reviewed list (``REASSIGNMENTS_PATH`` by default); refused when unreadable or
    when an address is listed twice. Unapproved, it lists nothing."""
    path = REASSIGNMENTS_PATH if path is None else path
    listed = read_list(path)
    changes = [((r.directory, r.slug), (r.from_doc_id, r.to_doc_id)) for r in listed.reassignments]
    threshold = [((t.directory, t.slug), t) for t in listed.threshold_exceptions]
    unique_changes, unique_threshold = _unique(changes, path), _unique(threshold, path)
    digest = list_digest(listed)
    approved, state = approval_state(listed, digest)
    return Reviewed(
        path=path,
        changes=unique_changes if approved else {},
        threshold=unique_threshold if approved else {},
        corpus=listed.corpus,
        digest=digest,
        approved=approved,
        state=state,
    )


def approve_list(path: Path, digest: str, approved_by: str, approved_on: date) -> str:
    """Sign a pending list: write the approval when ``digest`` is the list's own.

    Refuses, writing nothing, a digest other than the list's, an approved list, or
    an empty approver. Returns the digest signed.
    """
    listed = read_list(path)
    actual = list_digest(listed)
    if digest != actual:
        raise IdentitySchemaError(f"{path}: --digest {digest} is not the list's digest {actual}")
    if listed.approval.status != PENDING:
        raise IdentitySchemaError(f"{path}: already {listed.approval.status}; nothing written")
    try:
        approval = Approval(
            status=APPROVED, digest=actual, approved_by=approved_by, approved_on=approved_on
        )
    except ValidationError as exc:
        raise IdentitySchemaError(f"{path}: refusing the approval: {exc}") from exc
    atomic_write_text(path, list_json(listed.model_copy(update={"approval": approval})))
    return actual
