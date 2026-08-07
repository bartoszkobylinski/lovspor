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
    display_text: str | None = None  # original-cased counterpart (F3), presentation only
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
    body = str(section["body"])
    result = _check_span_and_hash(ref, normalize_quote_text(body))
    if result.status is QuoteStatus.OK:
        result.display_text = display_span_text(body, ref.char_span)
    return result


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


def _aligned_tokens(original: str) -> list[tuple[str, int, int]] | None:
    """``(original_token, norm_start, norm_end)`` triples; None when the
    normalization does not map token-by-token (fail closed, never a guess)."""
    normalized = normalize_quote_text(original)
    original_tokens = original.split()
    normalized_tokens = normalized.split(" ") if normalized else []
    if len(original_tokens) != len(normalized_tokens):
        return None
    triples: list[tuple[str, int, int]] = []
    position = 0
    for source, target in zip(original_tokens, normalized_tokens, strict=True):
        if normalize_quote_text(source) != target:
            return None
        triples.append((source, position, position + len(target)))
        position += len(target) + 1
    return triples


def display_span_text(original: str, char_span: tuple[int, int] | None) -> str | None:
    """Original-cased counterpart of a normalized-domain span (F3, C7).

    ``quote_ref`` coordinates and hashes stay in the normalized domain
    (the ``verify_quote`` contract); this recovers the source spelling —
    casing, typographic quotes — purely for presentation. None whenever
    the span does not cover whole tokens of a cleanly-aligned body: a
    partial or guessed restoration is worse than the normalized text.
    """
    triples = _aligned_tokens(original)
    if triples is None:
        return None
    if char_span is None:
        return " ".join(token for token, _, _ in triples) or None
    start, end = char_span
    covered = [token for token, s, e in triples if start <= s and e <= end]
    aligned = any(s == start for _, s, _ in triples) and any(e == end for _, _, e in triples)
    return " ".join(covered) if covered and aligned else None


def drift_or_invalid(pin_matches: bool) -> str:
    """Label a quote failure once the corpus pin has been checked."""
    return "invalid-case-definition" if pin_matches else "source-drift"
