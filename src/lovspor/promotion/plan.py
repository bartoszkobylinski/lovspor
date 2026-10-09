"""From one archived artifact to one prepared version, or a hold (ADR-0016 S3).

``prepare`` runs the S2 extractor (whose last gate is the personal-data
screen), then S1 identity against the central ref-ids, then places the
document in the local dataset — its slug and version — and renders it. It
writes nothing and needs no approval, so the same call serves the preview a
reviewer reads and the promotion that follows the approval.

Placement, for the one version this slice writes:

* a new id is version 1, under a slug unique within its authority directory
  (``rendering/slug.py`` over the title; on collision ``-<vedtaksdato>``, then
  ``-<id>``);
* an id already current with the same ``content_hash`` is **unchanged** —
  nothing is rendered or written, which is what makes a rerun a no-op;
* an id already current with another ``content_hash`` is the next version,
  under the slug it already has (renames move nothing, ADR-0016 1b);
* a withdrawn id is refused: a withdrawal is forward-only and permanent
  (ADR-0016 4f, ``lovspor promote withdraw``);
* an id filed under another authority is refused: interkommunal attribution
  is a decision of a later slice.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.archive import ArchivedArtifact
from lovspor.promotion.corpus import LOCAL_DIR, CorpusCheckout, LocalManifest
from lovspor.promotion.decisions import (
    Carried,
    Carries,
    Decision,
    HumanDecision,
    StandingApproval,
    utc_text,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from lovspor.promotion.identity import mint_identity
from lovspor.promotion.models import (
    Authority,
    ExtractedDocument,
    HeldExtraction,
    HeldIdentity,
    LocalDocument,
    MintedIdentity,
    ObservedSource,
    PersonalDataHit,
)
from lovspor.promotion.render import render_local_regulation
from lovspor.rendering.slug import derive_slug

COMMIT_SUBJECT = "promote(lokal-forskrift): {authority_id}/{slug} v{version}"


class Held(BaseModel):
    """A source the pipeline refuses to publish as it stands (ADR-0016 4h)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["extraction", "identity"]
    reason: str
    detail: str
    personal_data: tuple[PersonalDataHit, ...] = ()


class Prepared(BaseModel):
    """One version, placed and rendered; ``unchanged`` when the corpus already has it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: MintedIdentity
    extracted: ExtractedDocument
    slug: str
    version: int
    unchanged: bool
    markdown: str = ""

    @property
    def markdown_path(self) -> str:
        return f"{LOCAL_DIR}/{self.identity.authority.id}/{self.slug}.md"

    @property
    def commit_subject(self) -> str:
        return COMMIT_SUBJECT.format(
            authority_id=self.identity.authority.id, slug=self.slug, version=self.version
        )


def prepare(
    artifact: ArchivedArtifact, authority: Authority, corpus: CorpusCheckout
) -> Held | Prepared:
    """Extract, identify, place and render one artifact; a hold stops it at its stage."""
    extracted = extract_regulation(artifact.payload, artifact.content_type)
    if isinstance(extracted, HeldExtraction):
        return Held(
            stage="extraction",
            reason=extracted.reason.value,
            detail=extracted.detail,
            personal_data=extracted.personal_data,
        )
    identity = mint_identity(extracted.regulation, authority, corpus.central_ref_ids())
    if isinstance(identity, HeldIdentity):
        return Held(stage="identity", reason=identity.reason.value, detail=identity.detail)
    slug, version, unchanged = _placement(corpus.local_manifest(), identity, extracted)
    prepared = Prepared(
        identity=identity, extracted=extracted, slug=slug, version=version, unchanged=unchanged
    )
    if unchanged:
        return prepared
    return prepared.model_copy(update={"markdown": _render(prepared, artifact)})


def require_approval(
    decision: HumanDecision | None, prepared: Prepared, carried: Carried
) -> HumanDecision:
    """The standing human approval of exactly this text, or a refusal saying what is missing.

    ``carried`` maps approvals a migration carried to the extractor it carried
    them to (:meth:`~.decisions.DecisionLog.carried`): one carried to the
    running extractor stands there exactly as one given there.
    """
    if decision is None:
        msg = "no human decision is recorded for this artifact; run `lovspor promote approve`"
        raise PromotionRefusedError(msg)
    if decision.decision is not Decision.APPROVE:
        when = utc_text(decision.decided_at)
        msg = f"the standing decision is {decision.decision.value} ({decision.decided_by}, {when})"
        raise PromotionRefusedError(msg)
    if decision.content_hash != prepared.identity.content_hash or not stands_at_running_extractor(
        decision, carried
    ):
        msg = "the approval was given for another text or extractor; preview and approve again"
        raise PromotionRefusedError(msg)
    return decision


def stands_at_running_extractor(decision: HumanDecision, carried: Carried) -> bool:
    """True when ``decision`` was given at the running extractor, or carried to it."""
    return EXTRACTOR_VERSION in (decision.extractor_version, carried.get(decision))


def standing_on(decision: HumanDecision, carries: Carries) -> StandingApproval:
    """``decision`` with the carry it stands by at the running extractor, if it is carried.

    Writers publish the result, so an approval given at an earlier extractor is
    stamped ``approval_carried`` and never reads as a review at this one.
    """
    carry = carries.get(decision)
    if decision.extractor_version == EXTRACTOR_VERSION or carry is None:
        return StandingApproval(decision=decision)
    return StandingApproval(decision=decision, carry=carry)


def _placement(
    manifest: LocalManifest, identity: MintedIdentity, extracted: ExtractedDocument
) -> tuple[str, int, bool]:
    existing = manifest.documents.get(identity.doc_id)
    if existing is None:
        return _free_slug(manifest, identity, extracted), 1, False
    if existing.status == "removed":
        reason = existing.removed_reason.value if existing.removed_reason else "no reason recorded"
        msg = f"{identity.doc_id} was withdrawn ({reason}); it is never promoted again (4f)"
        raise PromotionRefusedError(msg)
    if existing.authority_id != identity.authority.id:
        msg = f"{identity.doc_id} is filed under authority {existing.authority_id}, not this one"
        raise PromotionRefusedError(msg)
    if existing.content_hash == identity.content_hash:
        return existing.slug, existing.version, True
    return existing.slug, existing.version + 1, False


def _free_slug(
    manifest: LocalManifest, identity: MintedIdentity, extracted: ExtractedDocument
) -> str:
    """The first slug no record of this authority holds — current or withdrawn alike."""
    taken = {
        record.slug
        for record in manifest.documents.values()
        if record.authority_id == identity.authority.id
    }
    base = derive_slug(None, extracted.fields.title, identity.doc_id)
    stated = extracted.regulation.vedtaksdato
    dated = f"{base}-{stated.isoformat()}" if stated is not None else None
    for candidate in (base, dated, f"{base}-{identity.doc_id}"):
        if candidate is not None and candidate not in taken:
            return candidate
    msg = f"every slug for {identity.doc_id} is taken under {identity.authority.id}"
    raise PromotionRefusedError(msg)


def _render(prepared: Prepared, artifact: ArchivedArtifact) -> str:
    source = ObservedSource(
        observed_at_first=artifact.observed_at_first,
        source_url=artifact.key.source_url,
        source_sha256=artifact.key.sha256,
    )
    document = LocalDocument(
        identity=prepared.identity,
        extracted=prepared.extracted,
        slug=prepared.slug,
        version=prepared.version,
        source=source,
    )
    return render_local_regulation(document)
