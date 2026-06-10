from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from lovspor.embeddings import search as search_module
from lovspor.embeddings.search import EmbeddingIndex, SearchHit


def _int8_vector(values: list[int]) -> np.ndarray:
    return np.array(values, dtype=np.int8)


def _index(entries: list[tuple[str, str, list[int], float]]) -> EmbeddingIndex:
    return EmbeddingIndex.from_entries(
        [(slug, sid, _int8_vector(values), scale) for slug, sid, values, scale in entries],
    )


def test_top_k_returns_empty_for_non_positive_k_or_empty_index() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)

    assert _index([("a", "1", [1, 0], 1.0)]).top_k(query, k=0) == []
    assert _index([]).top_k(query, k=3) == []


def test_top_k_rejects_non_1d_query() -> None:
    query = np.array([[1.0, 0.0]], dtype=np.float32)

    with pytest.raises(ValueError, match=r"^query must be 1-D"):
        _index([("a", "1", [1, 0], 1.0)]).top_k(query, k=1)


def test_top_k_orders_by_score_descending() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("bad", "1", [0, 1], 1.0),
            ("best", "1", [1, 0], 1.0),
            ("mid", "1", [1, 1], 1.0),
            ("zero", "1", [0, 0], 1.0),
        ],
    )

    hits = index.top_k(query, k=3)

    assert [hit.slug for hit in hits] == ["best", "mid", "bad"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(np.sqrt(0.5))
    assert hits[2].score == pytest.approx(0.0)


def test_top_k_score_is_independent_of_quantization_scale() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)

    for scale in (0.5, 1.0, 0.0078125):
        hits = _index([("a", "1", [1, 0], scale)]).top_k(query, k=1)
        assert hits[0].score == pytest.approx(1.0)


def test_top_k_tie_break_prefers_earlier_slug_when_k_one() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("slug-b", "1", [1, 0], 1.0),
            ("slug-a", "1", [1, 0], 1.0),
        ],
    )

    assert index.top_k(query, k=1) == [
        SearchHit(slug="slug-a", section_id="1", score=1.0),
    ]


def test_top_k_tie_break_prefers_earlier_section_id_when_k_one() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("slug-a", "2", [1, 0], 1.0),
            ("slug-a", "1", [1, 0], 1.0),
        ],
    )

    assert index.top_k(query, k=1) == [
        SearchHit(slug="slug-a", section_id="1", score=1.0),
    ]


def test_top_k_sorts_equal_scores_deterministically() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("slug-b", "2", [1, 0], 1.0),
            ("slug-a", "2", [1, 0], 1.0),
            ("slug-a", "1", [1, 0], 1.0),
            ("slug-b", "1", [1, 0], 1.0),
        ],
    )

    hits = index.top_k(query, k=4)

    assert [(hit.slug, hit.section_id) for hit in hits] == [
        ("slug-a", "1"),
        ("slug-a", "2"),
        ("slug-b", "1"),
        ("slug-b", "2"),
    ]


def test_top_k_boundary_ties_resolve_deterministically() -> None:
    # Three identical scores competing for k=2 slots: the slug
    # tie-break (ascending) must decide which two survive, no matter
    # the insertion order.
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("slug-c", "1", [1, 0], 1.0),
            ("slug-a", "1", [1, 0], 1.0),
            ("slug-b", "1", [1, 0], 1.0),
        ],
    )

    hits = index.top_k(query, k=2)

    assert [(hit.slug, hit.section_id) for hit in hits] == [
        ("slug-a", "1"),
        ("slug-b", "1"),
    ]


def test_top_k_skips_zero_vectors_and_keeps_scanning() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("zero", "1", [0, 0], 1.0),
            ("hit", "1", [1, 0], 1.0),
        ],
    )

    assert index.top_k(query, k=2) == [
        SearchHit(slug="hit", section_id="1", score=1.0),
    ]


def test_top_k_filters_by_allowed_slugs() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index(
        [
            ("lov-a", "1", [1, 0], 1.0),
            ("forskrift-b", "1", [1, 0], 1.0),
        ],
    )

    hits = index.top_k(query, k=5, allowed_slugs={"forskrift-b"})

    assert [(hit.slug, hit.section_id) for hit in hits] == [("forskrift-b", "1")]


def test_top_k_empty_allowed_slugs_returns_empty() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = _index([("lov-a", "1", [1, 0], 1.0)])

    assert index.top_k(query, k=5, allowed_slugs=set()) == []


def test_unique_slugs_reflects_kept_rows_only() -> None:
    index = _index(
        [
            ("kept", "1", [1, 0], 1.0),
            ("kept", "2", [0, 1], 1.0),
            ("zero-only", "1", [0, 0], 1.0),
        ],
    )

    assert index.unique_slugs == frozenset({"kept"})


def test_len_counts_searchable_rows() -> None:
    index = _index(
        [
            ("a", "1", [1, 0], 1.0),
            ("a", "2", [0, 0], 1.0),
            ("b", "1", [0, 1], 1.0),
        ],
    )

    assert len(index) == 2
    assert len(_index([])) == 0


def test_from_entries_rejects_inconsistent_dimensions() -> None:
    entries = [
        ("a", "1", _int8_vector([1, 0]), 1.0),
        ("b", "1", _int8_vector([1, 0, 0]), 1.0),
    ]

    with pytest.raises(ValueError, match="dim"):
        EmbeddingIndex.from_entries(entries)


def test_chunked_scoring_matches_unchunked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a 2-row chunk so a 5-row index spans three blocks, then
    # compare against the naive per-row reference computation.
    rng = np.random.default_rng(7)
    dim = 16
    entries = [
        (f"slug-{i}", "1", rng.integers(-127, 128, dim, dtype=np.int8), 0.01) for i in range(5)
    ]
    query = rng.standard_normal(dim).astype(np.float32)
    query /= np.linalg.norm(query)

    expected: list[tuple[float, str]] = []
    for slug, _sid, vector, scale in entries:
        dequantized = vector.astype(np.float32) * scale
        expected.append(
            (float(np.dot(query, dequantized / np.linalg.norm(dequantized))), slug),
        )
    expected.sort(key=lambda pair: (-pair[0], pair[1]))

    monkeypatch.setattr(search_module, "_CHUNK_ROWS", 2)
    hits = EmbeddingIndex.from_entries(entries).top_k(query, k=5)

    assert [hit.slug for hit in hits] == [slug for _score, slug in expected]
    for hit, (score, _slug) in zip(hits, expected, strict=True):
        assert hit.score == pytest.approx(score, abs=1e-6)


def test_search_hit_is_immutable() -> None:
    hit = SearchHit(slug="a", section_id="1", score=1.0)

    with pytest.raises(FrozenInstanceError):
        hit.slug = "b"
