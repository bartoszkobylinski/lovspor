"""Canonical embedding-input construction and identity (ADR-0006).

The Embedding Input Identity answers one question: were the stored vectors
generated from the exact ordered inputs the current pipeline would generate
for this document? It is a freshness axis, deliberately distinct from the
three identities that already exist — source (``xml_hash``/``embedding_hash``),
embedding space (ESI, ADR-0005) and storage format (LSPE version). Four
historical incidents (2,336 / 797 / 26 / 1 documents) stemmed from pipeline
changes none of those axes could see; the digest here moves by itself for any
change that alters the real inputs, and only for those.

This module is the ONE place that turns a Published Rendering into the
ordered ``(section_id, chunk_text)`` records that get embedded. The writer,
the staleness check, the repair heuristic and the migration all consume it,
so the hash can never drift from the payload — reconstructing "equivalent"
inputs in a second implementation is exactly the bug class ADR-0006 §2
forbids.
"""

import hashlib
from dataclasses import dataclass

from lovspor.embeddings.model import split_to_token_chunks
from lovspor.embeddings.sections import iter_sections, strip_frontmatter

EMPTY_INPUT_HASH = hashlib.sha256(b"").hexdigest()
"""Identity of a document with no embeddable sections.

The digest of the empty stream is a defined, stampable value: a header-only
sidecar produced by a keyed writer records that its input identity is
"nothing", which is different from recording nothing at all.
"""


@dataclass(frozen=True)
class EmbeddingInput:
    """One unit of provider payload: a section id and the chunk text sent."""

    section_id: str
    text: str


def build_embedding_inputs(rendered_markdown: str) -> list[EmbeddingInput]:
    """Derive the ordered embedding inputs for one rendered document.

    Frontmatter is stripped, sections are extracted with the shared heading
    grammar, and each section's text is split into token-bounded chunks —
    every chunk keyed by its section id, in document order. This is byte-for-
    byte the payload construction the embedding writer uses; nothing here may
    normalize, reorder or filter beyond what the pipeline itself does.
    """
    body = strip_frontmatter(rendered_markdown)
    return [
        EmbeddingInput(section_id=section.section_id, text=chunk)
        for section in iter_sections(body)
        for chunk in split_to_token_chunks(section.text)
    ]


def hash_embedding_inputs(inputs: list[EmbeddingInput]) -> str:
    """Canonical ADR-0006 digest of an ordered input stream.

    Each record contributes ``uint32-BE length || bytes`` for the UTF-8
    section id, then the same framing for the chunk text. Length-prefixed
    framing makes the serialization injective — no delimiter can collide with
    content — so two different streams cannot share a digest by construction
    of the encoding. No extra normalization: the identity is of the exact
    payload, not of an idealised one. The empty stream hashes to
    :data:`EMPTY_INPUT_HASH`.
    """
    digest = hashlib.sha256()
    for item in inputs:
        for part in (item.section_id.encode("utf-8"), item.text.encode("utf-8")):
            digest.update(len(part).to_bytes(4, "big"))
            digest.update(part)
    return digest.hexdigest()
