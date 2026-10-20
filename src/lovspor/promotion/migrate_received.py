"""``promote migrate`` for a document promoted from a received text (#643, ADR-0016 A5).

A received version names its source by ``innsyn_request`` and
``source_sha256``, never by a URL, so it is re-read from the innsyn workspace
(:func:`~.received_refresh.named_source`, the read ``promote received
preview`` makes), not from the observatory archive. Otherwise it migrates as
an observed document does (:mod:`.migrate`): the running extractor must read
the same id and text and render the published Markdown byte-identically,
under the KLASS vintage it was published with; then each version's standing
approval — given at the running extractor, or carried to it (:mod:`.carry`,
owner decision 2026-10-08) — moves the version metadata. An earlier version's
approval is carried only when the Markdown its own commit published re-renders
byte-identically from the received source that rendering names. Any byte
difference needs a fresh ``promote received approve``.
"""

from __future__ import annotations

from dataclasses import dataclass

from lovspor.errors import PromotionRefusedError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR, InnsynWorkspace
from lovspor.promotion.carry import VersionApproval, standing_approval
from lovspor.promotion.decisions import HumanDecision, ReceivedKey
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_received
from lovspor.promotion.innsyn_authority import received_authority
from lovspor.promotion.innsyn_source import received_source
from lovspor.promotion.migrate import (
    Migration,
    MigrationInputs,
    Published,
    assemble,
    published_klass_version,
    published_then,
    refuse_new_rendering,
    same_text,
)
from lovspor.promotion.plan import Prepared, received_form
from lovspor.promotion.received_refresh import named_source
from lovspor.promotion.withdraw import refuse_withdrawn
from lovspor.promotion.writer import ReceivedVersion


@dataclass(frozen=True)
class ReceivedSubject:
    """One received document being migrated: the run's inputs, the workspace, the document."""

    inputs: MigrationInputs
    workspace: InnsynWorkspace | None
    document: Published

    def require_workspace(self) -> InnsynWorkspace:
        """The innsyn workspace, or a refusal naming the variable that sets it."""
        if self.workspace is None:
            msg = f"a received document is re-read from the innsyn workspace; set {ENV_INNSYN_DIR}"
            raise PromotionRefusedError(msg)
        return self.workspace


def plan_received_migration(subject: ReceivedSubject) -> Migration:
    """The migration of a received document, or a refusal saying why it is not one."""
    document = subject.document
    entries = _received_versions(document)
    key, prepared = _reread(subject, document)
    current = standing_approval(subject.inputs.decisions.latest_decision(key), prepared)
    earlier = tuple(
        _earlier_approval(subject, entry, prepared)
        for entry in entries
        if entry.version != document.record.version
    )
    return assemble(subject.inputs, document, prepared, (*earlier, current))


def _received_versions(document: Published) -> tuple[ReceivedVersion, ...]:
    """Every promoted version, each one received, numbered 1..current."""
    versions = document.observations.versions
    received = tuple(entry for entry in versions if isinstance(entry, ReceivedVersion))
    current = document.record.version
    numbers = [entry.version for entry in received]
    if len(received) != len(versions) or numbers != list(range(1, current + 1)):
        msg = f"{document.observations_path} does not list received versions 1..{current}"
        raise PromotionRefusedError(msg)
    return received


def _reread(subject: ReceivedSubject, document: Published) -> tuple[ReceivedKey, Prepared]:
    """``document`` re-read from the received source it names, refused unless byte-identical."""
    workspace = subject.require_workspace()
    artifact = named_source(workspace, document)
    refuse_withdrawn(subject.inputs.decisions, artifact.key, document.doc_id)
    klass_version = published_klass_version(document)
    authority = received_authority(workspace, document.record.authority_id, klass_version)
    extracted = extract_received(artifact.payload, artifact.content_type)
    prepared = same_text(subject.inputs.corpus, document, extracted, authority)
    refuse_new_rendering(document, prepared, received_source(artifact, received_form(prepared)))
    return artifact.key, prepared


def _earlier_approval(
    subject: ReceivedSubject, entry: ReceivedVersion, prepared: Prepared
) -> VersionApproval:
    """The standing approval of an earlier version's text, carried only on identical bytes."""
    keys = _keys(subject.document.record.authority_id, entry)
    standing = [
        record
        for record in subject.inputs.decisions.records()
        if isinstance(record, HumanDecision) and record.artifact in keys
    ]
    identity = prepared.identity.model_copy(update={"content_hash": entry.content_hash})
    earlier = prepared.model_copy(update={"identity": identity, "version": entry.version})
    try:
        approval = standing_approval(standing[-1] if standing else None, earlier)
        if approval.carried:
            _require_identical_bytes(subject, entry)
    except PromotionRefusedError as exc:
        names = ", ".join(sorted(key.sha256 for key in keys))
        raise PromotionRefusedError(f"v{entry.version} (approve one of {names}): {exc}") from exc
    return approval


def _keys(authority_id: str, entry: ReceivedVersion) -> list[ReceivedKey]:
    """Every received copy of ``entry``'s text, as the decision log names it."""
    return [
        ReceivedKey(
            authority_id=authority_id, innsyn_request=p.innsyn_request, sha256=p.source_sha256
        )
        for p in entry.received
    ]


def _require_identical_bytes(subject: ReceivedSubject, entry: ReceivedVersion) -> None:
    """An earlier version's published Markdown re-rendered byte-identically, or a refusal."""
    corpus, document = subject.inputs.corpus, subject.document
    then = published_then(corpus, document, entry.version, entry.content_hash)
    try:
        _reread(subject, then)
    except PromotionRefusedError as exc:
        msg = (
            f"it is not byte-identical at extractor v{EXTRACTOR_VERSION} ({exc}); approve it again"
        )
        raise PromotionRefusedError(msg) from exc
