"""The files one promoted version writes into ``lokale-forskrifter/`` (ADR-0016 3, 4d).

Three files, all under ``lokale-forskrifter/`` and nowhere else:

* ``<authority_id>/<slug>.md`` — the rendering of the version;
* ``<authority_id>/observations/<slug>.json`` — the observation axis of every
  promoted version, each with its audit record (``promotion``): the role of
  who approved it — never the name — and when, the classifier evidence if
  supplied, the extractor, renderer and identity that produced it, and the
  archive records it was read from;
* ``manifest.json`` — the local dataset's membership.

Every byte is a function of the inputs: the artifact's observations up to the
approval, the approval itself, and the corpus as it stands. The manifest's
``generated_at`` is the approval's time, not the clock's, so an unchanged
input writes nothing new. A file whose bytes are already on disk is not
rewritten, and nothing here commits: the operator commits under the subject
the command prints, at promotion time — never backdated to ``ObservedAt``,
which lives in the front matter and the observations file (ADR-0016 2).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
)

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import PromotionRefusedError
from lovspor.promotion.archive import ArchivedArtifact, SourceStatus
from lovspor.promotion.corpus import LOCAL_DIR, MANIFEST_NAME, CorpusCheckout, LocalRecord
from lovspor.promotion.corpus import manifest_text as render_manifest
from lovspor.promotion.decisions import HumanDecision, IdentityAudit, PromotionAudit, utc_text
from lovspor.promotion.plan import Prepared
from lovspor.promotion.render import LOCAL_RENDERER_VERSION
from lovspor.promotion.versions import ExcludedObservation

_NLOD = re.compile(r"nlod", re.IGNORECASE)


class VersionObservations(BaseModel):
    """One promoted version on the observation axis, with its audit record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    content_hash: str
    observed_at_first: str
    observed_at_last: str
    observation_count: int
    primary_url: str
    corroborating_urls: tuple[str, ...]
    source_sha256s: tuple[str, ...]
    promotion: PromotionAudit


class ObservationsFile(BaseModel):
    """``observations/<slug>.json``: every promoted version of one document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    doc_id: str
    slug: str
    authority_id: str
    versions: tuple[VersionObservations, ...]
    source_status: SourceStatus
    excluded: tuple[ExcludedObservation, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_nothing_excluded(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """``excluded`` is written only when something was, so a file without it keeps its bytes."""
        data: dict[str, object] = handler(self)
        if not self.excluded:
            data.pop("excluded", None)
        return data


class WriteSet(BaseModel):
    """What one promotion writes: file texts by corpus-relative path, and the audit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: dict[str, str]
    audit: PromotionAudit


def write_set(
    prepared: Prepared, artifact: ArchivedArtifact, decision: HumanDecision, corpus: CorpusCheckout
) -> WriteSet:
    """The three files of one promoted version, refused if any would mention NLOD."""
    audit = _audit(prepared, artifact, decision)
    authority_dir = f"{LOCAL_DIR}/{prepared.identity.authority.id}"
    files = {
        prepared.markdown_path: prepared.markdown,
        f"{authority_dir}/observations/{prepared.slug}.json": _observations(
            corpus, prepared, artifact, audit
        ),
        f"{LOCAL_DIR}/{MANIFEST_NAME}": _manifest(corpus, prepared, artifact, decision),
    }
    mentioning = sorted(path for path, text in files.items() if _NLOD.search(text))
    if mentioning:
        msg = f"{mentioning} would mention NLOD; local regulations are not NLOD data"
        raise PromotionRefusedError(msg)
    return WriteSet(files=files, audit=audit)


def apply(corpus: CorpusCheckout, writes: WriteSet) -> tuple[str, ...]:
    """Write every file whose bytes differ from disk; the paths written, sorted."""
    written: list[str] = []
    for relative, text in sorted(writes.files.items()):
        target = corpus.inside(relative.removeprefix(f"{LOCAL_DIR}/"))
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        written.append(relative)
    return tuple(written)


def _audit(
    prepared: Prepared, artifact: ArchivedArtifact, decision: HumanDecision
) -> PromotionAudit:
    identity = prepared.identity
    return PromotionAudit(
        decision=decision.decision,
        reviewed_by_role=decision.reviewer_role,
        decided_at=utc_text(decision.decided_at),
        reason=decision.reason,
        reviewed_in_sample=True,
        classifier=decision.classifier,
        extractor_version=prepared.extracted.extractor_version,
        renderer_version=LOCAL_RENDERER_VERSION,
        source_form=prepared.extracted.source_form.value,
        identity=IdentityAudit(
            scheme=identity.scheme,
            doc_id=identity.doc_id,
            ref_id=identity.ref_id,
            candidates=identity.candidates,
        ),
        observations_through=utc_text(decision.decided_at),
        observations=artifact.observations,
    )


def _observations(
    corpus: CorpusCheckout, prepared: Prepared, artifact: ArchivedArtifact, audit: PromotionAudit
) -> str:
    earlier = _earlier_versions(corpus, prepared, artifact)
    entry = VersionObservations(
        version=prepared.version,
        content_hash=prepared.identity.content_hash,
        observed_at_first=utc_text(artifact.observed_at_first),
        observed_at_last=utc_text(artifact.observed_at_last),
        observation_count=len(artifact.observations),
        primary_url=artifact.key.source_url,
        corroborating_urls=artifact.corroborating_urls,
        source_sha256s=(artifact.key.sha256,),
        promotion=audit,
    )
    document = ObservationsFile(
        doc_id=prepared.identity.doc_id,
        slug=prepared.slug,
        authority_id=prepared.identity.authority.id,
        versions=(*earlier, entry),
        source_status=artifact.source_status,
    )
    return _json(document)


def observations_text(document: ObservationsFile) -> str:
    """``observations/<slug>.json`` as it is written: sorted keys, indented, final newline."""
    return _json(document)


def _json(document: BaseModel) -> str:
    payload = document.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _earlier_versions(
    corpus: CorpusCheckout, prepared: Prepared, artifact: ArchivedArtifact
) -> tuple[VersionObservations, ...]:
    """The versions already promoted, refusing one this version would not follow."""
    if prepared.version == 1:
        return ()
    path = corpus.inside(f"{prepared.identity.authority.id}/observations/{prepared.slug}.json")
    try:
        existing = ObservationsFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        msg = f"{path} does not read; version {prepared.version} cannot be appended to it"
        raise PromotionRefusedError(msg) from exc
    if [v.version for v in existing.versions] != list(range(1, prepared.version)):
        msg = f"{path} does not list versions 1..{prepared.version - 1}"
        raise PromotionRefusedError(msg)
    if _instant(existing.versions[-1].observed_at_first) >= artifact.observed_at_first:
        msg = "this content was first observed before the current version; backfill orders that"
        raise PromotionRefusedError(msg)
    return existing.versions


def _manifest(
    corpus: CorpusCheckout, prepared: Prepared, artifact: ArchivedArtifact, decision: HumanDecision
) -> str:
    manifest = corpus.local_manifest()
    authority = prepared.identity.authority
    record = LocalRecord(
        status="current",
        slug=prepared.slug,
        title=prepared.extracted.fields.title,
        markdown_path=prepared.markdown_path,
        renderer_version=LOCAL_RENDERER_VERSION,
        last_seen=utc_text(artifact.observed_at_last),
        authority_id=authority.id,
        authority_type=authority.type.value,
        content_hash=prepared.identity.content_hash,
        version=prepared.version,
        extractor_version=prepared.extracted.extractor_version,
    )
    documents = {**manifest.documents, prepared.identity.doc_id: record}
    updated = manifest.model_copy(
        update={"documents": documents, "generated_at": utc_text(decision.decided_at)}
    )
    return render_manifest(updated)
