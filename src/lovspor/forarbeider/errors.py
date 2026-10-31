"""Typed refusals of the forarbeider reader.

The detail names a field, a URL or a path the operator supplied, never page
text: a refusal reaches logs and CI, and a proposition can quote hearing
submissions by name.
"""

from enum import StrEnum

from lovspor.errors import LovsporError


class RefusalReason(StrEnum):
    INVALID_SOURCE_URL = "invalid_source_url"
    HOST_NOT_ALLOWED = "host_not_allowed"
    UNEXPECTED_QUERY = "unexpected_query"
    CHAPTER_PAGE_REQUIRED = "chapter_page_required"
    UNSUPPORTED_DOCUMENT_TYPE = "unsupported_document_type"
    UNREADABLE_PAGE = "unreadable_page"
    DOM_CHANGED = "dom_changed"
    SOURCE_IDENTITY_MISSING = "source_identity_missing"
    CONFLICTING_IDENTITY = "conflicting_identity"
    REFERENCE_MISSING = "reference_missing"
    TITLE_MISSING = "title_missing"
    CHAPTER_NAVIGATION_MISSING = "chapter_navigation_missing"
    CHAPTER_DUPLICATE = "chapter_duplicate"
    CHAPTER_FOREIGN = "chapter_foreign"
    CHAPTER_SEQUENCE = "chapter_sequence"
    SCHEMA_INVALID = "schema_invalid"
    INVALID_TIMESTAMP = "invalid_timestamp"
    UNSAFE_OUTPUT_PATH = "unsafe_output_path"


class ForarbeiderRefusedError(LovsporError):
    """The input proves too little, or contradicts itself; no output is written."""

    def __init__(self, reason: RefusalReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail
