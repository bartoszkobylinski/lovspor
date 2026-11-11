"""The private records of a batch release and a withdrawal (#738, contract Amendment 1, B1b).

Stored in the owner-only workspace, never in a checkout, CI or a log:

- :class:`BatchConfirmation`, the owner's dated, written sign-off for exactly
  one batch: the plan and binding digests, the documents, the chapter count,
  the target branch, the files the release writes and the manifest they make.
  It is stored **before** the clone changes, under that manifest's digest, so
  a run that stopped after writing finds the decision that put the tree there.
- :class:`BatchReleaseReceipt`, written after the tree is verified, under the
  confirmation's digest.
- :class:`ReleaseWithdrawalIntent` and :class:`ReleaseWithdrawal`, the same
  pair for a forward-only withdrawal.

A typed phrase at a terminal is the owner's act, not authenticated identity:
``identity_authenticated`` is ``False`` by type. Nothing here is committed,
pushed or merged: the owner does that, and merging the lovverk PR is the
publication.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from lovspor.forarbeider.apply_preflight import ApplyBinding
from lovspor.forarbeider.corpus_plan import PlannedFile
from lovspor.forarbeider.release_models import SHA256

_Digest = Annotated[str, Field(pattern=SHA256)]
_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class BatchConfirmation(BaseModel):
    """The owner's typed sign-off for one batch, bound to exact inputs and exact output."""

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_batch_confirmation"]
    plan_sha256: _Digest
    binding_sha256: _Digest
    binding: ApplyBinding
    target_branch: str
    documents: tuple[str, ...] = Field(min_length=1)
    chapters: int = Field(ge=1)
    phrase: str
    owner: str = Field(min_length=1)
    decided_at: AwareDatetime
    result_manifest_sha256: _Digest
    writes: tuple[PlannedFile, ...] = Field(min_length=3)
    identity_authenticated: Literal[False]
    git_add: Literal[False]
    commits: Literal[False]
    pushes: Literal[False]

    @field_validator("decided_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return _utc(value)


class BatchReleaseReceipt(BaseModel):
    """A confirmed batch written to the release clone's working tree and read back."""

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_batch_release"]
    confirmation_sha256: _Digest
    plan_sha256: _Digest
    binding_sha256: _Digest
    target_branch: str
    head_commit: str
    base_manifest_sha256: _Digest | None
    result_manifest_sha256: _Digest
    writes: tuple[PlannedFile, ...]
    documents: tuple[str, ...]
    written_to: Literal["working_tree_only"]
    publication: Literal["owner_merge_of_lovverk_pr"]


class WithdrawalReason(StrEnum):
    """Why the owner takes a document down: a code, never free text that could hold a name."""

    PRIVACY = "privacy"
    RIGHTS = "rights"
    CORRECTION = "correction"
    SOURCE_WITHDRAWN = "source_withdrawn"
    OWNER_DECISION = "owner_decision"


class ReleaseWithdrawalIntent(BaseModel):
    """The owner's decision to withdraw one released document, stored before the clone changes.

    ``chapters_named`` are the chapters the owner identified; the document's
    file holds every released chapter, so ``chapters_withdrawn`` is all of them.
    """

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_withdrawal_intent"]
    document_id: str
    chapters_named: tuple[int, ...] = Field(min_length=1)
    chapters_withdrawn: tuple[int, ...] = Field(min_length=1)
    reason: WithdrawalReason
    owner: str = Field(min_length=1)
    withdrawn_at: AwareDatetime
    target_branch: str
    head_commit: str
    base_manifest_sha256: _Digest
    result_manifest_sha256: _Digest
    removed: tuple[str, ...] = Field(min_length=1)
    phrase: str
    identity_authenticated: Literal[False]
    history_erased: Literal[False]

    @field_validator("withdrawn_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return _utc(value)


class ReleaseWithdrawal(BaseModel):
    """A withdrawal written to the release clone's working tree and read back."""

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_withdrawal"]
    intent_sha256: _Digest
    document_id: str
    reason: WithdrawalReason
    withdrawn_at: AwareDatetime
    removed: tuple[str, ...]
    result_manifest_sha256: _Digest
    written_to: Literal["working_tree_only"]
    history_erased: Literal[False]

    @field_validator("withdrawn_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return _utc(value)
