"""Quote-reference materialization and verification (quote-free dataset).

LLHB stores no statutory text: a true quote lives in the dataset as
coordinates — ``(slug, section_id, occurrence?, char_span?)`` plus a
SHA-256 of the normalized span — and is materialized here from a pinned
corpus checkout at evaluation time. Everything fails closed:

* unknown slug/section, ambiguous occurrence → typed failure, never a
  guess across candidates;
* span outside the section, or an empty span → ``span-invalid``;
* hash mismatch → ``hash-mismatch``; coordinates are NEVER silently
  adjusted to make the hash fit.

Normalization is the production ``verify_quote`` normalization,
imported from ``lovspor.mcp`` — the benchmark must hash and compare
text exactly the way the MCP tool does, so a materialized quote is by
construction verifiable by ``verify_quote`` against the same corpus.

Whether a failure means *source drift* or an *invalid case definition*
is not decidable here: it depends on whether the corpus checkout is at
the case's pinned commit. Callers check the pin first
(``lovspor.llhb.corpus_pin``) and use ``drift_or_invalid`` to label the
failure.
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel

from lovspor.mcp import (
    CorpusAmbiguousSectionError,
    CorpusNotFoundError,
    CorpusReader,
    _normalize_for_quote_match,
)

_SHA256_HEX_LENGTH = 64


class QuoteStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not-found"
    AMBIGUOUS = "ambiguous"
    SPAN_INVALID = "span-invalid"
    HASH_MISMATCH = "hash-mismatch"


class QuoteRef(BaseModel, frozen=True):
    """Stable coordinates of a statutory quote in a pinned corpus state."""

    slug: str
    section_id: str
    occurrence: int | None = None
    char_span: tuple[int, int] | None = None  # [start, end) in normalized section text
    sha256_normalized: str


class MaterializedQuote(BaseModel):
    """Outcome of materializing one quote reference."""

    status: QuoteStatus
    text: str | None = None  # normalized quote text, only when status is OK
    reason: str | None = None


def normalize_quote_text(text: str) -> str:
    """The production verify_quote normalization (canonical, not a copy)."""
    return _normalize_for_quote_match(text)


def quote_sha256(normalized_text: str) -> str:
    """SHA-256 hex of an already-normalized quote span."""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def materialize_quote(reader: CorpusReader, ref: QuoteRef) -> MaterializedQuote:
    """Materialize ``ref`` from ``reader``'s corpus, failing closed."""
    try:
        section = reader.get_section(ref.slug, ref.section_id, ref.occurrence)
    except CorpusAmbiguousSectionError as exc:
        return MaterializedQuote(status=QuoteStatus.AMBIGUOUS, reason=str(exc))
    except CorpusNotFoundError as exc:
        return MaterializedQuote(status=QuoteStatus.NOT_FOUND, reason=str(exc))
    normalized = normalize_quote_text(str(section["body"]))
    return _check_span_and_hash(ref, normalized)


def _check_span_and_hash(ref: QuoteRef, normalized: str) -> MaterializedQuote:
    if ref.char_span is None:
        text = normalized
    else:
        start, end = ref.char_span
        if not (0 <= start < end <= len(normalized)):
            return MaterializedQuote(
                status=QuoteStatus.SPAN_INVALID,
                reason=(
                    f"char_span [{start}, {end}) outside normalized section "
                    f"text of length {len(normalized)}"
                ),
            )
        text = normalized[start:end]
    if not text:
        return MaterializedQuote(status=QuoteStatus.SPAN_INVALID, reason="empty quote span")
    digest = quote_sha256(text)
    if digest != ref.sha256_normalized:
        return MaterializedQuote(
            status=QuoteStatus.HASH_MISMATCH,
            reason=(
                f"normalized span hashes to {digest}, reference says "
                f"{ref.sha256_normalized}; coordinates are never auto-adjusted"
            ),
        )
    return MaterializedQuote(status=QuoteStatus.OK, text=text)


def drift_or_invalid(pin_matches: bool) -> str:
    """Label a quote failure once the corpus pin has been checked."""
    return "invalid-case-definition" if pin_matches else "source-drift"
