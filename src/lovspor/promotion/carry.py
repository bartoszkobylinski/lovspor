"""Carrying a standing approval across an extractor bump (owner decision, 2026-10-08).

When an extractor bump leaves a promoted version's published rendering
byte-identical, the owner's standing approval of that version carries to the
running extractor: ``lovspor promote migrate`` needs no new review. Any byte
difference still needs a fresh ``promote approve`` at the running extractor.

A carry is never presented as a review. The decision log gets a ``carried``
record (:class:`~.decisions.CarriedRecord`) naming the approval it rests on —
its time, its extractor, its reviewer's role, never the person — and the
published audit keeps the approval's own fields and says, in
``approval_carried``, at which extractor it was given and to which it was
carried. An approval given at the running extractor is not carried, and
wins: nothing is recorded as carried for it.

The bytes of the current version are the checkout's Markdown; those of an
earlier version are the Markdown its own commit published, read from the
checkout's git history (a shallow clone may not reach them, and then nothing
is carried for that version).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    ApprovalCarried,
    ApprovalReference,
    ArtifactKey,
    CarriedRecord,
    Decision,
    HumanDecision,
    PromotionAudit,
    utc_text,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.plan import Prepared, require_approval
from lovspor.timetravel import _iter_follow_log, _read_blob


class VersionApproval(BaseModel):
    """The approval one promoted version migrates on: given at the running extractor, or carried."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    content_hash: str
    decision: HumanDecision
    carried: bool

    @property
    def key(self) -> ArtifactKey:
        return self.decision.artifact

    def carried_record(self, doc_id: str, from_version: int, now: datetime) -> CarriedRecord:
        """The ``carried`` record of this approval, moved from ``from_version`` at ``now``."""
        return CarriedRecord(
            artifact=self.key,
            carried_at=now,
            doc_id=doc_id,
            version=self.version,
            content_hash=self.content_hash,
            from_extractor_version=from_version,
            to_extractor_version=EXTRACTOR_VERSION,
            approval=ApprovalReference.of(self.decision),
        )


def standing_approval(decision: HumanDecision | None, prepared: Prepared) -> VersionApproval:
    """The approval of ``prepared``'s text, carried when given at an earlier extractor.

    The caller has shown the version's rendering byte-identical; this refuses
    exactly what :func:`~.plan.require_approval` refuses, except an approval of
    the same text at an earlier extractor.
    """
    try:
        approval = require_approval(decision, prepared, {})
        return _approval(approval, prepared, carried=False)
    except PromotionRefusedError:
        if decision is None or not _carriable(decision, prepared):
            raise
        return _approval(decision, prepared, carried=True)


def _approval(decision: HumanDecision, prepared: Prepared, *, carried: bool) -> VersionApproval:
    return VersionApproval(
        version=prepared.version,
        content_hash=prepared.identity.content_hash,
        decision=decision,
        carried=carried,
    )


def _carriable(decision: HumanDecision, prepared: Prepared) -> bool:
    return (
        decision.decision is Decision.APPROVE
        and decision.content_hash == prepared.identity.content_hash
        and decision.extractor_version is not None
        and decision.extractor_version < EXTRACTOR_VERSION
    )


def historical_markdown(
    corpus: CorpusCheckout, markdown_path: str, version: int, content_hash: str
) -> str | None:
    """The Markdown an earlier version's commit published, or ``None`` when history lacks it."""
    try:
        revisions = _iter_follow_log(corpus.path, markdown_path)
    except subprocess.CalledProcessError:
        return None
    wanted = (f"version: {version}", f"content_hash: {json.dumps(content_hash)}")
    for revision in revisions:
        text = _read_blob(corpus.path, revision.sha, revision.path)
        lines = text.partition("\n---\n")[0].split("\n")
        if all(line in lines for line in wanted):
            return text
    return None


def audit_at_running_extractor(
    audit: PromotionAudit, approval: VersionApproval, now: datetime
) -> PromotionAudit:
    """``audit`` moved to the running extractor, saying whether its approval was carried."""
    carried = None
    if approval.carried:
        carried = ApprovalCarried(
            approved_at_extractor=ApprovalReference.of(approval.decision).extractor_version,
            carried_to_extractor=EXTRACTOR_VERSION,
            carried_at=utc_text(now),
        )
    update = {"extractor_version": EXTRACTOR_VERSION, "approval_carried": carried}
    return audit.model_copy(update=update)
