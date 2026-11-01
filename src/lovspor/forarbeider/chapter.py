"""One chapter of one saved page, as a held staging result (#738, ADR-0018 PR2).

The selected chapter is the ``?ch=N`` of the source URL, proved by the checks
of :mod:`lovspor.forarbeider.page`. A page that fails them, such as one cut off
or claimed as another chapter, is refused and yields nothing. A proved page
whose body cannot be reproduced yields a result without a body.
"""

import hashlib
from datetime import datetime

from pydantic import ValidationError

from lovspor.forarbeider.chapter_body import ChapterBody, read_body
from lovspor.forarbeider.chapter_models import (
    ChapterResult,
    HoldReason,
    ordered_reasons,
    text_hash,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import identity_fields, page_document, read_proposition

_FIXED_FIELDS: dict[str, object] = {
    "schema_version": 1,
    "source_document_type": "forarbeid",
    "normative": False,
    "extractor_version": 1,
    "normalizer_version": 1,
    "source_provider": "regjeringen.no",
    "rights_basis": "åndsverkloven § 14",
    "source_license": None,
    "release_state": "held",
}


def build_chapter_result(payload: bytes, source_url: str, retrieved_at: datetime) -> ChapterResult:
    """The held result of the chapter ``source_url`` selects, depending on these inputs alone.

    Raises:
        ForarbeiderRefusedError: the page fails a check of :func:`read_proposition`.
    """
    facts = read_proposition(payload, source_url)
    chapter = facts.chapters[(facts.locator.chapter or 0) - 1]
    body = read_body(page_document(payload), chapter.title)
    fields = {
        **_FIXED_FIELDS,
        **identity_fields(facts.locator),
        **_body_fields(body),
        "id": f"regjeringen-{facts.locator.document_id}-ch{chapter.chapter_number}",
        "title": facts.title,
        "ministry": facts.ministry,
        "chapter_number": chapter.chapter_number,
        "chapter_title": chapter.title,
        "retrieved_at": retrieved_at,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
    }
    try:
        return ChapterResult.model_validate(fields)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, source_url) from error


def _body_fields(body: ChapterBody) -> dict[str, object]:
    return {
        "text_sha256": None if body.blocks is None else text_hash(body.blocks),
        "hold_reasons": ordered_reasons((HoldReason.REVIEW_REQUIRED, *body.reasons)),
        "blocks": body.blocks,
    }
