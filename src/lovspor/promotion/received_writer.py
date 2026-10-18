"""The files one promoted received version writes (ADR-0016 A3-A5).

The same four files as :func:`~lovspor.promotion.writer.write_set`, written
the same way, with the received channel's provenance in place of the
observation axis:

* the rendering carries ``basis: "received"`` (:mod:`~lovspor.promotion.render`);
* ``observations/<slug>.json`` holds a :class:`~lovspor.promotion.writer.ReceivedVersion`
  (schema version 2, no ``source_status``: there is no URL to have one for);
* the audit names no observation (``observations`` is empty and
  ``observations_through`` is the approval's time) and carries the innsyn
  block, with the operator's ``answers`` as review evidence only (A1-1);
* the manifest's ``last_seen`` is ``received_at``.

A later received text with another ``content_hash`` is the next version,
ordered by ``received_at`` (A4). A document whose versions were observed is
refused, whatever the hash: switching the primary channel, or listing a
received copy as corroborating an observed version, is a recorded decision
no command makes yet (A4, 4c), so it is never written silently.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.corpus import LOCAL_DIR, MANIFEST_NAME, CorpusCheckout
from lovspor.promotion.decisions import InnsynAudit, PromotionAudit, StandingApproval, utc_text
from lovspor.promotion.evidence import evidence_path
from lovspor.promotion.innsyn_source import ReceivedArtifact
from lovspor.promotion.plan import Prepared
from lovspor.promotion.writer import (
    ObservationsFile,
    ReceivedProvenance,
    ReceivedVersion,
    WriteSet,
    audit_record,
    evidence_text,
    local_manifest_text,
    observations_text,
    refuse_nlod,
)

_SCHEMA: Final[Literal[2]] = 2


def received_write_set(
    prepared: Prepared,
    received: ReceivedArtifact,
    approval: StandingApproval,
    corpus: CorpusCheckout,
) -> WriteSet:
    """The four files of one promoted received version, refused if any would mention NLOD.

    Raises:
        PromotionRefusedError: the document's versions were observed, the
            corpus already holds this version, the text was received before
            the current version's, or a file would mention NLOD.
    """
    earlier = _following(received_history(corpus, prepared), prepared, received)
    audit = _audit(prepared, received, approval)
    authority_id = prepared.identity.authority.id
    files = {
        prepared.markdown_path: prepared.markdown,
        f"{LOCAL_DIR}/{authority_id}/observations/{prepared.slug}.json": _observations(
            prepared, received, audit, earlier
        ),
        evidence_path(authority_id, prepared.slug): evidence_text(corpus, prepared),
        f"{LOCAL_DIR}/{MANIFEST_NAME}": local_manifest_text(
            corpus, prepared, utc_text(received.received_at), approval.decision
        ),
    }
    refuse_nlod(files)
    return WriteSet(files=files, audit=audit)


def received_history(corpus: CorpusCheckout, prepared: Prepared) -> tuple[ReceivedVersion, ...]:
    """Every version the corpus holds of the prepared document, each one received.

    Raises:
        PromotionRefusedError: its observations file does not read, or a
            version of it was observed (ADR-0016 A4).
    """
    if prepared.identity.doc_id not in corpus.local_manifest().documents:
        return ()
    path = corpus.inside(f"{prepared.identity.authority.id}/observations/{prepared.slug}.json")
    versions = _read(path).versions
    received = tuple(v for v in versions if isinstance(v, ReceivedVersion))
    if len(received) != len(versions):
        msg = (
            f"{prepared.identity.doc_id} was observed; a received text is not written to it: "
            "switching the primary channel is a recorded decision (ADR-0016 A4)"
        )
        raise PromotionRefusedError(msg)
    return received


def _read(path: Path) -> ObservationsFile:
    try:
        return ObservationsFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        msg = f"{path} does not read; no received version is written beside it"
        raise PromotionRefusedError(msg) from exc


def _following(
    history: tuple[ReceivedVersion, ...], prepared: Prepared, received: ReceivedArtifact
) -> tuple[ReceivedVersion, ...]:
    """``history``, refused unless this version follows it by number and by ``received_at``."""
    doc_id = prepared.identity.doc_id
    if prepared.unchanged:
        msg = f"{doc_id} v{prepared.version} is already in the corpus; nothing to write"
        raise PromotionRefusedError(msg)
    if [v.version for v in history] != list(range(1, prepared.version)):
        msg = f"the observations file of {doc_id} does not list versions 1..{prepared.version - 1}"
        raise PromotionRefusedError(msg)
    if history and datetime.fromisoformat(history[-1].received[0].received_at) >= (
        received.received_at
    ):
        msg = f"{doc_id}: this text was received before the current version's (ADR-0016 A4)"
        raise PromotionRefusedError(msg)
    return history


def _audit(
    prepared: Prepared, received: ReceivedArtifact, approval: StandingApproval
) -> PromotionAudit:
    innsyn = InnsynAudit(
        innsyn_request=received.key.innsyn_request,
        received_at=utc_text(received.received_at),
        kind="regulation_text",
        answers=received.answers,
    )
    return audit_record(prepared, approval, ()).model_copy(update={"innsyn": innsyn})


def _observations(
    prepared: Prepared,
    received: ReceivedArtifact,
    audit: PromotionAudit,
    earlier: tuple[ReceivedVersion, ...],
) -> str:
    provenance = ReceivedProvenance(
        received_at=utc_text(received.received_at),
        innsyn_request=received.key.innsyn_request,
        source_sha256=received.key.sha256,
    )
    entry = ReceivedVersion(
        version=prepared.version,
        content_hash=prepared.identity.content_hash,
        received=(provenance,),
        promotion=audit,
    )
    document = ObservationsFile(
        schema_version=_SCHEMA,
        doc_id=prepared.identity.doc_id,
        slug=prepared.slug,
        authority_id=prepared.identity.authority.id,
        versions=(*earlier, entry),
    )
    return observations_text(document)
