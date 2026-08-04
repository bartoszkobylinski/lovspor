"""ADR-0006 canonical input identity: framing, determinism, sensitivity.

The digest is the contract: any change that alters the real ordered inputs
must move it, anything else must not. These tests pin the encoding golden so
the canonical serialization can never drift silently — a drifted digest
would re-embed the corpus for nothing, and a lax one would readmit the
silent-stale-vector class the ADR exists to kill.
"""

import hashlib

from lovspor.embeddings.inputs import (
    EMPTY_INPUT_HASH,
    EmbeddingInput,
    build_embedding_inputs,
    hash_embedding_inputs,
)

_DOC = (
    "---\n"
    "title: X\n"
    "---\n"
    "# X\n"
    "\n"
    "## Kapittel 1. Alminnelige bestemmelser\n"
    "\n"
    "### § 1-1. Virkeområde\n"
    "\n"
    "Første tekst.\n"
    "\n"
    "### § 1-2. Definisjoner\n"
    "\n"
    "Andre tekst.\n"
)


def _manual_digest(records: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for section_id, text in records:
        for part in (section_id.encode(), text.encode()):
            digest.update(len(part).to_bytes(4, "big"))
            digest.update(part)
    return digest.hexdigest()


def test_the_encoding_is_the_documented_length_prefixed_stream() -> None:
    """Golden: the implementation matches the ADR-0006 §1 encoding exactly,
    reproduced here independently so a framing change cannot hide inside a
    matching pair of implementation and test helper."""
    inputs = [EmbeddingInput("1-1", "a"), EmbeddingInput("1-2", "b")]
    assert hash_embedding_inputs(inputs) == _manual_digest([("1-1", "a"), ("1-2", "b")])


def test_the_empty_stream_has_the_standard_empty_sha256() -> None:
    assert hash_embedding_inputs([]) == EMPTY_INPUT_HASH
    assert hashlib.sha256(b"").hexdigest() == EMPTY_INPUT_HASH


def test_the_digest_is_deterministic_across_runs() -> None:
    first = hash_embedding_inputs(build_embedding_inputs(_DOC))
    second = hash_embedding_inputs(build_embedding_inputs(_DOC))
    assert first == second


def test_ordering_changes_the_identity() -> None:
    forward = [EmbeddingInput("1", "a"), EmbeddingInput("2", "b")]
    swapped = [EmbeddingInput("2", "b"), EmbeddingInput("1", "a")]
    assert hash_embedding_inputs(forward) != hash_embedding_inputs(swapped)


def test_the_section_id_participates_in_the_identity() -> None:
    assert hash_embedding_inputs([EmbeddingInput("1", "a")]) != hash_embedding_inputs(
        [EmbeddingInput("2", "a")],
    )


def test_the_chunk_text_participates_in_the_identity() -> None:
    assert hash_embedding_inputs([EmbeddingInput("1", "a")]) != hash_embedding_inputs(
        [EmbeddingInput("1", "b")],
    )


def test_length_prefix_framing_resists_boundary_collisions() -> None:
    """The classic delimiter attack: moving a byte across a record boundary
    must change the digest. With naive concatenation ('1'+'ab' vs '1a'+'b')
    both streams would serialize identically."""
    one = [EmbeddingInput("1", "ab")]
    other = [EmbeddingInput("1a", "b")]
    assert hash_embedding_inputs(one) != hash_embedding_inputs(other)


def test_splitting_one_record_into_two_changes_the_identity() -> None:
    """Same concatenated text, different chunk boundaries — the same-count
    blindness of the old heuristics is exactly what the digest must see."""
    joined = [EmbeddingInput("1", "alpha beta")]
    split = [EmbeddingInput("1", "alpha "), EmbeddingInput("1", "beta")]
    assert hash_embedding_inputs(joined) != hash_embedding_inputs(split)


def test_build_derives_ordered_ids_and_text_from_the_rendering() -> None:
    inputs = build_embedding_inputs(_DOC)
    assert [item.section_id for item in inputs] == ["1-1", "1-2"]
    assert "Første tekst." in inputs[0].text
    assert "Andre tekst." in inputs[1].text


def test_a_sectionless_document_yields_the_empty_identity() -> None:
    doc = "---\ntitle: T\n---\n# T\n\nBare brødtekst uten paragrafer.\n"
    assert build_embedding_inputs(doc) == []
    assert hash_embedding_inputs(build_embedding_inputs(doc)) == EMPTY_INPUT_HASH
