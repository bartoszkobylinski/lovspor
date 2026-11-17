"""A thousand amendments to one document, each byte-identical to a full rebuild (#936).

The change path decides per amendment: carry the committed int8 rows (the
stored scale provably holds), pay the document in full (a row that may hold
the maximum is gone; known before spend), or re-pay its carried inputs (a paid
row is louder than the stored scale). Whichever way it goes, the sidecar it
publishes must be the bytes a full re-embed of the same inputs would publish,
and the next amendment reads that sidecar back, so any drift would compound.
"""

import random
from collections.abc import Callable
from pathlib import Path

import numpy as np

from lovspor.embeddings.inputs import EmbeddingInput
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.sync.delta_embed import PriorVectors, RepayHeadroom, ReusingEmbedder, exact_prior
from tests.unit.test_delta_embed_equivalence import _PinnedEmbedder, _unit_vector

_LOOP_DIM = 256
_ESI = _PinnedEmbedder().space_id
_Amendment = Callable[[random.Random, list[EmbeddingInput], int], None]


def _change(rng: random.Random, items: list[EmbeddingInput], step: int) -> None:
    at = rng.randrange(len(items))
    items[at] = EmbeddingInput(items[at].section_id, f"{items[at].section_id} endret {step}")


def _add(rng: random.Random, items: list[EmbeddingInput], step: int) -> None:
    items.insert(rng.randrange(len(items) + 1), EmbeddingInput(f"a{step}", f"ny tekst {step}"))


def _delete(rng: random.Random, items: list[EmbeddingInput], _step: int) -> None:
    del items[rng.randrange(len(items))]


def _move(rng: random.Random, items: list[EmbeddingInput], _step: int) -> None:
    moved = items.pop(rng.randrange(len(items)))
    items.insert(rng.randrange(len(items) + 1), moved)


_AMENDMENTS: dict[str, _Amendment] = {
    "change": _change,
    "add": _add,
    "delete": _delete,
    "move": _move,
}


def _amended(rng: random.Random, items: list[EmbeddingInput], step: int) -> list[EmbeddingInput]:
    amended = list(items)
    names = ["change", "add", "move"] if len(amended) < 4 else list(_AMENDMENTS)
    _AMENDMENTS[rng.choice(names if len(amended) < 40 else ["delete"])](rng, amended, step)
    return amended


def _loop_vectors(items: list[EmbeddingInput]) -> np.ndarray:
    return np.vstack([_unit_vector(item.text, _LOOP_DIM) for item in items])


def _publish(path: Path, items: list[EmbeddingInput], rows: tuple[np.ndarray, float]) -> bytes:
    quantized, scale = rows
    pairs = [(item.section_id, quantized[n]) for n, item in enumerate(items)]
    write_embeddings(path, pairs, scale, dim=_LOOP_DIM, embedding_space_id=_ESI)
    return path.read_bytes()


def _delta_rows(
    prior: PriorVectors, items: list[EmbeddingInput], embedder: _PinnedEmbedder
) -> tuple[tuple[np.ndarray, float], str]:
    """What the change path does, and which way it went: guard-paid, re-paid or carried."""
    kept = exact_prior(prior, items)
    if kept is None:
        return quantize_int8(embedder.encode([item.text for item in items])), "paid"
    rows = ReusingEmbedder(embedder, kept).quantized_rows(items)
    return rows, "re-paid" if len(embedder.paid) == len(items) else "carried"


def test_a_thousand_amendments_to_one_document_stay_byte_identical_to_a_rebuild(
    tmp_path: Path,
) -> None:
    rng = random.Random(936)  # noqa: S311 - a reproducible amendment sequence, not a secret
    items = [EmbeddingInput(f"s{n}", f"paragraf {n}") for n in range(12)]
    delta, full = tmp_path / "delta.bin", tmp_path / "full.bin"
    _publish(delta, items, quantize_int8(_loop_vectors(items)))
    ways: list[str] = []
    for step in range(1000):
        sidecar = read_embeddings(delta)
        rows = {i.text: row for i, (_, row) in zip(items, sidecar.sections, strict=True)}
        items = _amended(rng, items, step)
        prior = PriorVectors(rows=rows, scale=sidecar.scale, headroom=RepayHeadroom(None))
        output, way = _delta_rows(prior, items, _PinnedEmbedder(dim=_LOOP_DIM))
        published = _publish(delta, items, output)
        assert published == _publish(full, items, quantize_int8(_loop_vectors(items))), step
        ways.append(way)

    assert {way: ways.count(way) for way in set(ways)} == {
        "carried": 963,
        "paid": 23,
        "re-paid": 14,
    }
