"""Withdrawing a promoted local regulation, forward only (ADR-0016 4f, slice S9).

``lovverk`` history is never rewritten (ADR-0003), so a wrong promotion is
undone by a later commit that:

* marks the manifest record ``status: "removed"`` with a closed-set
  :class:`~lovspor.promotion.models.RemovedReason`;
* deletes the document's Markdown, so no reader serves it;
* keeps ``history/<slug>.json`` untouched and ``observations/<slug>.json``
  with every promoted version, adding the withdrawal — by the reviewer's
  role, never the name.

The withdrawal is first a record in the decision log, naming the document's
id and every archived artifact it was promoted from. Promotion reads that
record on every run (:func:`refuse_withdrawn`), so neither a later approval
nor a fresh checkout brings the document back. Nothing here commits.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import PromotionRefusedError
from lovspor.promotion.corpus import (
    LOCAL_DIR,
    MANIFEST_NAME,
    CorpusCheckout,
    LocalManifest,
    LocalRecord,
    manifest_text,
)
from lovspor.promotion.decisions import (
    ArtifactKey,
    DecisionLog,
    WithdrawalDocument,
    WithdrawalRecord,
    utc_text,
)
from lovspor.promotion.writer import ObservationsFile, WithdrawalNotice, observations_text

WITHDRAW_SUBJECT = "withdraw(lokal-forskrift): {authority_id}/{slug}"

LocatedDocument = tuple[str, LocalRecord]


class Withdrawn(BaseModel):
    """What one withdrawal changed in the checkout, by corpus-relative path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    written: tuple[str, ...]
    deleted: tuple[str, ...]
    commit_subject: str


def locate_document(manifest: LocalManifest, authority_id: str, slug: str) -> LocatedDocument:
    """The one record filed as ``<authority_id>/<slug>``, current or withdrawn."""
    for doc_id, record in sorted(manifest.documents.items()):
        if (record.authority_id, record.slug) == (authority_id, slug):
            return doc_id, record
    msg = f"no local regulation {authority_id}/{slug} in the corpus manifest"
    raise PromotionRefusedError(msg)


def withdrawal_record(
    corpus: CorpusCheckout, located: LocatedDocument, document: WithdrawalDocument, now: datetime
) -> WithdrawalRecord:
    """The decision-log record of withdrawing ``located``, naming every artifact it came from."""
    doc_id, record = located
    observations = _observations(corpus, record)
    artifacts = tuple(
        ArtifactKey(authority_id=record.authority_id, sha256=sha256, source_url=v.primary_url)
        for v in observations.versions
        for sha256 in v.source_sha256s
    )
    return WithdrawalRecord(
        doc_id=doc_id,
        authority_id=record.authority_id,
        slug=record.slug,
        removed_reason=document.removed_reason,
        artifacts=artifacts,
        decided_by=document.decided_by,
        reviewer_role=document.reviewer_role,
        decided_at=now,
        reason=document.reason,
    )


def apply_withdrawal(corpus: CorpusCheckout, withdrawal: WithdrawalRecord) -> Withdrawn:
    """Write the removed record and the kept observations; delete the Markdown."""
    manifest = corpus.local_manifest()
    record = manifest.documents[withdrawal.doc_id]
    files = {
        f"{LOCAL_DIR}/{MANIFEST_NAME}": _removed_manifest(manifest, withdrawal),
        _observations_path(record): _withdrawn_observations(corpus, record, withdrawal),
    }
    for relative, text in sorted(files.items()):
        atomic_write_text(corpus.inside(relative.removeprefix(f"{LOCAL_DIR}/")), text)
    markdown = corpus.inside(record.markdown_path.removeprefix(f"{LOCAL_DIR}/"))
    deleted = (record.markdown_path,) if markdown.is_file() else ()
    markdown.unlink(missing_ok=True)
    subject = WITHDRAW_SUBJECT.format(authority_id=record.authority_id, slug=record.slug)
    return Withdrawn(
        doc_id=withdrawal.doc_id,
        written=tuple(sorted(files)),
        deleted=deleted,
        commit_subject=subject,
    )


def refuse_withdrawn(decisions: DecisionLog, key: ArtifactKey | None, doc_id: str | None) -> None:
    """Refuse to go on with an artifact, or a document, the decision log withdrew."""
    withdrawal = decisions.withdrawal_of(key, doc_id)
    if withdrawal is None:
        return
    when = utc_text(withdrawal.decided_at)
    msg = (
        f"{withdrawal.doc_id} was withdrawn ({withdrawal.removed_reason.value}, {when}); "
        "a withdrawn document is never promoted again (ADR-0016 4f)"
    )
    raise PromotionRefusedError(msg)


def _observations_path(record: LocalRecord) -> str:
    return f"{LOCAL_DIR}/{record.authority_id}/observations/{record.slug}.json"


def _observations(corpus: CorpusCheckout, record: LocalRecord) -> ObservationsFile:
    path = corpus.inside(_observations_path(record).removeprefix(f"{LOCAL_DIR}/"))
    try:
        return ObservationsFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        msg = f"{path} does not read; the withdrawal must name the artifacts it covers"
        raise PromotionRefusedError(msg) from exc


def _withdrawn_observations(
    corpus: CorpusCheckout, record: LocalRecord, withdrawal: WithdrawalRecord
) -> str:
    notice = WithdrawalNotice(
        removed_reason=withdrawal.removed_reason,
        reviewed_by_role=withdrawal.reviewer_role,
        decided_at=utc_text(withdrawal.decided_at),
        reason=withdrawal.reason,
    )
    kept = _observations(corpus, record).model_copy(update={"withdrawal": notice})
    return observations_text(kept)


def _removed_manifest(manifest: LocalManifest, withdrawal: WithdrawalRecord) -> str:
    record = manifest.documents[withdrawal.doc_id].model_copy(
        update={"status": "removed", "removed_reason": withdrawal.removed_reason}
    )
    documents = {**manifest.documents, withdrawal.doc_id: record}
    updated = manifest.model_copy(
        update={"documents": documents, "generated_at": utc_text(withdrawal.decided_at)}
    )
    return manifest_text(updated)
