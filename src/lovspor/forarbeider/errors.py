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
    INPUT_UNREADABLE = "input_unreadable"
    INPUT_TOO_LARGE = "input_too_large"
    OUTPUT_WRITE_FAILED = "output_write_failed"
    OBSERVATION_EXISTS = "observation_exists"
    OBSERVATION_UNSAFE = "observation_unsafe"
    UNREADABLE_PAGE = "unreadable_page"
    INCOMPLETE_PAGE = "incomplete_page"
    DOM_CHANGED = "dom_changed"
    SOURCE_IDENTITY_MISSING = "source_identity_missing"
    CONFLICTING_IDENTITY = "conflicting_identity"
    REFERENCE_MISSING = "reference_missing"
    TITLE_MISSING = "title_missing"
    CHAPTER_NAVIGATION_MISSING = "chapter_navigation_missing"
    CHAPTER_DUPLICATE = "chapter_duplicate"
    CHAPTER_FOREIGN = "chapter_foreign"
    CHAPTER_SEQUENCE = "chapter_sequence"
    PAGE_CHAPTER_UNPROVED = "page_chapter_unproved"
    SCHEMA_INVALID = "schema_invalid"
    INVALID_TIMESTAMP = "invalid_timestamp"
    UNSAFE_OUTPUT_PATH = "unsafe_output_path"
    REVIEW_INVALID = "review_invalid"
    REVIEW_SUBJECT_MISMATCH = "review_subject_mismatch"
    REVIEW_CONFLICT = "review_conflict"
    REVIEW_TRANSITION = "review_transition"
    REVIEW_ATTESTATION_MISSING = "review_attestation_missing"
    REVIEW_NOT_ELIGIBLE = "review_not_eligible"
    REVIEW_NOT_INTERACTIVE = "review_not_interactive"
    REVIEW_LEDGER_CORRUPT = "review_ledger_corrupt"
    REVIEW_LEDGER_UNSAFE = "review_ledger_unsafe"
    UNSAFE_INPUT_PATH = "unsafe_input_path"
    RELEASE_INVALID = "release_invalid"
    RELEASE_NOT_ELIGIBLE = "release_not_eligible"
    RELEASE_STALE = "release_stale"
    RELEASE_RECORD_MISSING = "release_record_missing"
    RELEASE_RECORD_CORRUPT = "release_record_corrupt"
    RELEASE_ALREADY_REVOKED = "release_already_revoked"
    RELEASE_OUT_OF_ORDER = "release_out_of_order"
    MANIFEST_INVALID = "manifest_invalid"
    MANIFEST_INCOMPATIBLE = "manifest_incompatible"
    CORPUS_TREE_MISMATCH = "corpus_tree_mismatch"
    CORPUS_NOT_SANDBOX = "corpus_not_sandbox"
    CORPUS_LOCKED = "corpus_locked"
    INPUT_NOT_SYNTHETIC = "input_not_synthetic"
    PROMOTION_DENIED = "promotion_denied"
    PLAN_STALE = "plan_stale"
    RENDER_UNSAFE = "render_unsafe"
    DOCUMENT_NOT_RELEASED = "document_not_released"
    DOCUMENT_UNKNOWN = "document_unknown"
    CHAPTER_NOT_RELEASED = "chapter_not_released"


class ForarbeiderRefusedError(LovsporError):
    """The input proves too little, or contradicts itself; no output is written."""

    def __init__(self, reason: RefusalReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail
