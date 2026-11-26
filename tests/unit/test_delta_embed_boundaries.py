"""Exact rounding and text identity boundaries of the #936 reuse contract."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.inputs import EmbeddingInput
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.sync.delta_embed import PriorVectors, RepayHeadroom, ReusingEmbedder, paid_tokens
from tests.unit.test_embedding_space_identity import _FakeEmbedder


class _TableEmbedder(_FakeEmbedder):
    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        super().__init__()
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.vstack([self.vectors[text] for text in texts])


def _prior(tmp_path: Path, provider: _TableEmbedder) -> PriorVectors:
    """Round-trip the prior so the tested scale is the LSPE float32 header value."""
    rows, scale = quantize_int8(provider.vectors["carried"][None, :])
    path = tmp_path / "prior.bin"
    write_embeddings(path, [("old-id", rows[0])], scale, 4, embedding_space_id=provider.space_id)
    stored = read_embeddings(path)
    return PriorVectors(
        rows={"carried": stored.sections[0][1]},
        scale=stored.scale,
        headroom=RepayHeadroom(None),
    )


@pytest.mark.parametrize("direction", [-1, 0, 1])
def test_adjacent_float32_maxima_keep_or_repay_at_the_stored_scale_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direction: int
) -> None:
    """A fresh maximum one float32 step above the old one must trigger repayment."""
    # Tokenization is independent of this rounding proof; avoid downloading BPE data.
    monkeypatch.setattr("lovspor.sync.delta_embed.count_input_tokens", len)
    maximum = np.float32(1.0)
    if direction:
        maximum = np.nextafter(maximum, np.float32(np.inf if direction > 0 else -np.inf))
    provider = _TableEmbedder(
        {
            "carried": np.array([1.0, -0.31, 0.17, 0.0], dtype=np.float32),
            "fresh": np.array([-maximum, 0.2, -0.4, 0.0], dtype=np.float32),
        }
    )
    prior = _prior(tmp_path, provider)
    inputs = [EmbeddingInput("a", "carried"), EmbeddingInput("b", "fresh")]

    rows, scale = ReusingEmbedder(provider, prior).quantized_rows(inputs)

    assert provider.calls == ([["fresh"], ["carried"]] if direction > 0 else [["fresh"]])
    expected, expected_scale = quantize_int8(np.vstack(list(provider.vectors.values())))
    write_embeddings(tmp_path / "delta.bin", list(zip(["a", "b"], rows, strict=True)), scale, 4)
    write_embeddings(
        tmp_path / "full.bin",
        list(zip(["a", "b"], expected, strict=True)),
        expected_scale,
        4,
    )
    assert (tmp_path / "delta.bin").read_bytes() == (tmp_path / "full.bin").read_bytes()


def test_repeated_carried_text_uses_current_ids_and_order_with_ties_to_even(
    tmp_path: Path,
) -> None:
    """Reuse follows input text; fresh half-step components use rebuild rounding."""
    provider = _TableEmbedder(
        {
            "carried": np.array([127, -63, 5, 0], dtype=np.float32) / 128,
            "fresh": np.array([0.5, 1.5, -0.5, -1.5], dtype=np.float32) / 128,
        }
    )
    prior = _prior(tmp_path, provider)
    inputs = [
        EmbeddingInput("new-first", "carried"),
        EmbeddingInput("new-middle", "fresh"),
        EmbeddingInput("new-last", "carried"),
    ]

    rows, scale = ReusingEmbedder(provider, prior).quantized_rows(inputs)

    assert provider.calls == [["fresh"]]
    assert paid_tokens([inputs[0], inputs[2]], prior) == 0
    np.testing.assert_array_equal(rows[1], [0, 2, 0, -2])
    expected, expected_scale = quantize_int8(
        np.vstack([provider.vectors[item.text] for item in inputs])
    )
    np.testing.assert_array_equal(rows, expected)
    assert scale == expected_scale
    path = tmp_path / "delta.bin"
    write_embeddings(path, [(item.section_id, rows[n]) for n, item in enumerate(inputs)], scale, 4)
    assert [sid for sid, _ in read_embeddings(path).sections] == [i.section_id for i in inputs]
