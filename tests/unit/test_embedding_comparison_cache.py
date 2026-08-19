"""The embedding-comparison cache must never serve a matrix from another corpus."""

from __future__ import annotations

import importlib.util
import pickle
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "embedding_comparison" / "run.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("embedding_comparison_run", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run() -> ModuleType:
    return _load_script()


class _CountingBackend:
    """Records how often the corpus was actually embedded."""

    def __init__(self) -> None:
        self.name = "counting-backend"
        self.dim = 2
        self.calls = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        return np.ones((len(texts), self.dim), dtype=np.float32)


def _sections(run: ModuleType, *texts: str) -> list[object]:
    return [run.Section(slug="lov", section_id=f"§{i}", text=t) for i, t in enumerate(texts)]


def test_an_unchanged_corpus_is_a_cache_hit(run: ModuleType, tmp_path: Path) -> None:
    backend = _CountingBackend()
    sections = _sections(run, "første ledd", "andre ledd")

    first, indexing_seconds = run.embed_corpus_cached(backend, sections, tmp_path)
    second, cached_seconds = run.embed_corpus_cached(backend, sections, tmp_path)

    assert backend.calls == 1
    assert indexing_seconds > 0.0
    assert cached_seconds == 0.0
    assert np.array_equal(first, second)


def test_a_changed_corpus_is_never_served_from_cache(run: ModuleType, tmp_path: Path) -> None:
    """Issue #108: keyed by backend name alone, a re-run after a corpus change
    scored the old matrix and reported a Recall@5 for a corpus that no longer
    existed."""
    backend = _CountingBackend()
    run.embed_corpus_cached(backend, _sections(run, "første ledd", "andre ledd"), tmp_path)

    run.embed_corpus_cached(backend, _sections(run, "første ledd", "endret ledd"), tmp_path)

    assert backend.calls == 2


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda run, texts: run.Section(slug="annen-lov", **texts), id="slug"),
        pytest.param(
            lambda run, texts: run.Section(slug="lov", **{**texts, "section_id": "§9"}),
            id="section-id",
        ),
        pytest.param(
            lambda run, texts: run.Section(slug="lov", **{**texts, "text": "endret tekst"}),
            id="text",
        ),
    ],
)
def test_the_fingerprint_covers_every_embedded_field(run: ModuleType, mutate: object) -> None:
    base = run.Section(slug="lov", section_id="§1", text="samme tekst")
    changed = mutate(run, {"section_id": base.section_id, "text": base.text})  # type: ignore[operator]

    assert run.corpus_fingerprint([base]) != run.corpus_fingerprint([changed])


def test_fingerprint_does_not_collide_across_a_field_boundary(run: ModuleType) -> None:
    """Without a separator between fields, ("ab", "c") and ("a", "bc") would hash
    identically once concatenated — the null-byte separator must prevent that."""
    left = run.Section(slug="ab", section_id="c", text="x")
    right = run.Section(slug="a", section_id="bc", text="x")

    assert run.corpus_fingerprint([left]) != run.corpus_fingerprint([right])


def test_empty_corpus_fingerprint_is_deterministic(run: ModuleType) -> None:
    assert run.corpus_fingerprint([]) == run.corpus_fingerprint([])


def test_section_order_is_part_of_the_key(run: ModuleType) -> None:
    """Rows are positional: the same sections in another order index differently."""
    first, second = _sections(run, "a", "b")

    assert run.corpus_fingerprint([first, second]) != run.corpus_fingerprint([second, first])


def test_a_cache_file_without_a_fingerprint_is_ignored(run: ModuleType, tmp_path: Path) -> None:
    """A cache written before the key existed carries no proof of its corpus."""
    backend = _CountingBackend()
    sections = _sections(run, "første ledd")
    fingerprint = run.corpus_fingerprint(sections)
    legacy = tmp_path / f"{backend.name}-{fingerprint[:16]}.pkl"
    with legacy.open("wb") as f:
        pickle.dump({"matrix": np.zeros((1, 2), dtype=np.float32), "name": backend.name}, f)

    matrix, _ = run.embed_corpus_cached(backend, sections, tmp_path)

    assert backend.calls == 1
    assert np.array_equal(matrix, np.ones((1, 2), dtype=np.float32))
