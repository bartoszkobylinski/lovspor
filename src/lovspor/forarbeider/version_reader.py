"""One reader for version manifests v3 and the corpus manifests of versions 1 and 2 (#738, FC6).

A v3 manifest is read by :func:`~lovspor.forarbeider.version_manifest.parse_version_manifest`,
its id and both axes recomputed. A v1 or v2 corpus manifest is read exactly as
before, by :func:`~lovspor.forarbeider.corpus_models.parse_any_manifest`, and
each member is lifted to a summary (architecture §5.2): no version id, because
v2 never recorded one, and both axes incomplete, because v2 never recorded the
whole extraction. A lifted member is never complete, whatever its coverage
label says.
"""

from typing import Literal, Self

from pydantic import model_validator

from lovspor.forarbeider.corpus_models import (
    ManifestDocumentV2,
    _loaded,
    parse_any_manifest,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.version_manifest import parse_version_manifest
from lovspor.forarbeider.version_models import (
    MANIFEST_SCHEMA_VERSION,
    Digest,
    Effect,
    Frozen,
    NonBlank,
    PublicationState,
    Reason,
    SourceState,
    VersionManifest,
)

_LEGACY = (1, 2)


class VersionSummary(Frozen):
    """What any manifest version states about one document's completeness."""

    schema_version: Literal[1, 2, 3]
    document_id: NonBlank
    version_id: Digest | None
    source_state: SourceState
    publication_state: PublicationState
    reasons: tuple[Reason, ...]

    @model_validator(mode="after")
    def _publication_needs_source(self) -> Self:
        published = self.publication_state is PublicationState.PUBLICATION_COMPLETE
        if published and self.source_state is not SourceState.SOURCE_COMPLETE:
            raise ValueError("publication_complete requires source_complete")
        return self


def read_version_summaries(raw: bytes) -> tuple[VersionSummary, ...]:
    """The documents of any manifest version in ``raw``, each with both axes.

    Raises:
        ForarbeiderRefusedError: ``manifest_incompatible`` for an unknown
            version; ``manifest_invalid`` for bytes its version refuses.
    """
    payload = _loaded(raw)
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if version == MANIFEST_SCHEMA_VERSION:
        return (_summary(parse_version_manifest(raw)),)
    if version not in _LEGACY:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
    legacy = parse_any_manifest(raw)
    return tuple(_lifted(member, version) for member in legacy.documents)


def _summary(manifest: VersionManifest) -> VersionSummary:
    return VersionSummary(
        schema_version=3,
        document_id=manifest.content.document.document_id,
        version_id=manifest.version_id,
        source_state=manifest.source.state,
        publication_state=manifest.publication.state,
        reasons=manifest.source.reasons + manifest.publication.reasons,
    )


def _lifted(member: ManifestDocumentV2, version: Literal[1, 2]) -> VersionSummary:
    """A legacy member: released chapters only, never the whole extraction, never complete."""
    released = {item.chapter_number for item in member.chapters}
    unreleased = [number for number in member.listed_chapters if number not in released]
    reasons = (
        Reason(code="legacy_manifest", effect=Effect.INCOMPLETE, detail=f"v{version}"),
        *(
            Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=f"ch{number}")
            for number in unreleased
        ),
    )
    return VersionSummary(
        schema_version=version,
        document_id=member.document.id,
        version_id=None,
        source_state=SourceState.SOURCE_INCOMPLETE,
        publication_state=PublicationState.PUBLICATION_INCOMPLETE,
        reasons=reasons,
    )
