from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from lovspor.embeddings.search import SearchHit, top_k_cosine


def _int8_vector(values: list[int]) -> np.ndarray:
    return np.array(values, dtype=np.int8)


def test_top_k_cosine_returns_empty_for_non_positive_k_or_empty_index() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)

    assert top_k_cosine(query, [("a", "1", _int8_vector([1, 0]), 1.0)], k=0) == []
    assert top_k_cosine(query, [], k=3) == []


def test_top_k_cosine_rejects_non_1d_query() -> None:
    query = np.array([[1.0, 0.0]], dtype=np.float32)

    with pytest.raises(ValueError, match=r"^query must be 1-D"):
        top_k_cosine(query, [("a", "1", _int8_vector([1, 0]), 1.0)], k=1)


def test_top_k_cosine_orders_by_score_descending() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = [
        ("bad", "1", _int8_vector([0, 1]), 1.0),
        ("best", "1", _int8_vector([1, 0]), 1.0),
        ("mid", "1", _int8_vector([1, 1]), 1.0),
        ("zero", "1", _int8_vector([0, 0]), 1.0),
    ]

    hits = top_k_cosine(query, index, k=3)

    assert [hit.slug for hit in hits] == ["best", "mid", "bad"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(np.sqrt(0.5))
    assert hits[2].score == pytest.approx(0.0)


def test_top_k_cosine_tie_break_prefers_earlier_slug_when_k_one() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = [
        ("slug-b", "1", _int8_vector([1, 0]), 1.0),
        ("slug-a", "1", _int8_vector([1, 0]), 1.0),
    ]

    assert top_k_cosine(query, index, k=1) == [
        SearchHit(slug="slug-a", section_id="1", score=1.0),
    ]


def test_top_k_cosine_tie_break_prefers_earlier_section_id_when_k_one() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = [
        ("slug-a", "2", _int8_vector([1, 0]), 1.0),
        ("slug-a", "1", _int8_vector([1, 0]), 1.0),
    ]

    assert top_k_cosine(query, index, k=1) == [
        SearchHit(slug="slug-a", section_id="1", score=1.0),
    ]


def test_top_k_cosine_sorts_equal_scores_deterministically() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = [
        ("slug-b", "2", _int8_vector([1, 0]), 1.0),
        ("slug-a", "2", _int8_vector([1, 0]), 1.0),
        ("slug-a", "1", _int8_vector([1, 0]), 1.0),
        ("slug-b", "1", _int8_vector([1, 0]), 1.0),
    ]

    hits = top_k_cosine(query, index, k=4)

    assert [(hit.slug, hit.section_id) for hit in hits] == [
        ("slug-a", "1"),
        ("slug-a", "2"),
        ("slug-b", "1"),
        ("slug-b", "2"),
    ]


def test_top_k_cosine_skips_zero_vectors_and_keeps_scanning() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    index = [
        ("zero", "1", _int8_vector([0, 0]), 1.0),
        ("hit", "1", _int8_vector([1, 0]), 1.0),
    ]

    assert top_k_cosine(query, index, k=1) == [
        SearchHit(slug="hit", section_id="1", score=1.0),
    ]


def test_search_hit_is_immutable() -> None:
    hit = SearchHit(slug="a", section_id="1", score=1.0)

    with pytest.raises(FrozenInstanceError):
        hit.slug = "b"
