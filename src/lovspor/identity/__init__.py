"""Permanent document identity in the corpus (#871, ADR-0023 draft).

Identity is the ``doc_id`` (ADR-0003); a slug is an address. This package
keeps the three promises that make an address never change meaning: a
manifest record never disappears (``records``), a slug never changes owner
(``registry``), and a document's history is append-only and keyed by its
``doc_id`` (``history``). Whether the corpus is held to them is its declared
identity schema (``schema``, ``identity.json``), and a corpus whose git
history shows it was migrated never reads as legacy again
(``history_marker``). ``migration`` plans the one-off repair of a corpus
written before these guards existed.
"""

from lovspor.identity.records import replace_manifest
from lovspor.identity.registry import (
    corpus_identity,
    registered_ownership,
    verified_corpus_identity,
)

__all__ = [
    "corpus_identity",
    "registered_ownership",
    "replace_manifest",
    "verified_corpus_identity",
]
