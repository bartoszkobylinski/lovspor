"""``history/<slug>.json`` for local regulations, derived from git (ADR-0016 3, S3).

The transaction axis of a local document is its ``lovverk`` git history, read
by the same derivation as the central corpus (``lovspor.history``), which
knows the local commit subjects. History can only be derived from a commit
that exists, so it is a separate step from the promotion: the operator
commits the promoted version, runs ``lovspor promote history``, and commits
the history files under :data:`HISTORY_SUBJECT`. That commit touches no
document, so it never becomes an event of one.

Only the JSON is written. The central ``history/<slug>.md`` view carries
Lovdata's licence in its front matter, and nothing in this dataset may.
"""

from __future__ import annotations

from lovspor.atomic_io import atomic_write_text
from lovspor.history import extract_history, history_json
from lovspor.promotion.corpus import LOCAL_DIR, CorpusCheckout

HISTORY_SUBJECT = "history(lokal-forskrift): derive history for {count} documents"


def derive_local_history(corpus: CorpusCheckout) -> tuple[str, ...]:
    """Write the history of every current local document that has one; paths written."""
    written: list[str] = []
    for doc_id, record in sorted(corpus.local_manifest().documents.items()):
        if record.status != "current":
            continue
        history = extract_history(corpus.path, record.markdown_path, doc_id, record.slug)
        if not history.events:
            continue
        relative = f"{record.authority_id}/history/{record.slug}.json"
        target = corpus.inside(relative)
        text = history_json(history)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        written.append(f"{LOCAL_DIR}/{relative}")
    return tuple(written)
