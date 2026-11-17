"""What the change path trusts in a committed sidecar, and what it refuses (#936 review).

Carrying an int8 row is exact only if the committed sidecar is what the rebuild
quantizer wrote: ``float32(max|v| / 127)`` over the floats of its own inputs.
Int8 bytes cannot prove that by themselves, so it rests on provenance: every
writer of a sidecar is the rebuild quantizer or the LSPE cutover, which copies
vectors and scale bit for bit. These tests pin that writer set, the cutover
case, the refusals that bytes *can* decide (no ±127 row, a scale that is not
finite and positive, unreadable Markdown), and that the change path's cache
lookup has no side effect on the remote cache.
"""

import ast
import subprocess
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import CachingEmbedder, read_policy_from_env
from lovspor.embeddings.cache_format import CachedVectors, EntryKey
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.quantize import quantize_at_scale, quantize_int8
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.storage.manifest import read_manifest
from lovspor.sync.delta_embed import (
    PriorVectors,
    ReusingEmbedder,
    document_vectors,
    embedding_target,
    exact_prior,
    load_prior_vectors,
)
from lovspor.sync.document_io import embeddings_path
from lovspor.sync.lspe_cutover import migrate_lspe_v2
from tests.unit.test_delta_embed_equivalence import (
    _DATASET,
    _PinnedEmbedder,
    _s0,
    _s1,
    _sidecars,
    _sync,
    _texts,
    _unit_vector,
)
from tests.unit.test_sync_orchestrator import _settings

_SRC = Path(__file__).resolve().parents[2] / "src" / "lovspor"


def _callers_of(name: str) -> set[tuple[str, str]]:
    """``(module path, enclosing function)`` of every call to ``name`` under src/."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for call in (n for n in ast.walk(func) if isinstance(n, ast.Call)):
                callee = call.func
                called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
                if called == name:
                    found.add((str(path.relative_to(_SRC)), func.name))
    return found


def test_only_the_rebuild_quantizer_and_the_cutover_write_sidecars() -> None:
    """A new sidecar writer must keep the rebuild-quantizer provenance, or reuse is unsound."""
    assert _callers_of("write_embeddings") == {
        ("sync/orchestrator.py", "_write_embeddings_for_doc"),
        ("sync/lspe_cutover.py", "_apply_cutover"),
    }
    assert _callers_of("document_vectors") >= {
        ("sync/orchestrator.py", "_write_embeddings_for_doc"),
    }


def test_a_cutover_written_sidecar_is_carried_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v1 -> v2 cutover copies vectors and scale, so its sidecars are rebuild-derived."""
    v1 = _settings(tmp_path / "cutover").model_copy(update={"lspe_writer_version": 1})
    _sync(v1, monkeypatch, _s0(), _PinnedEmbedder())
    migrate_lspe_v2(v1)
    delta = v1.model_copy(update={"lspe_writer_version": 2})
    embedder = _PinnedEmbedder()

    _sync(delta, monkeypatch, _s1(), embedder)

    rebuilt = _settings(tmp_path / "full")
    _sync(rebuilt, monkeypatch, _s1(), _PinnedEmbedder())
    assert _sidecars(delta.lovverk_repo_path) == _sidecars(rebuilt.lovverk_repo_path)
    assert not set(_texts(delta.lovverk_repo_path, "lov-same")) & set(embedder.paid)


def _rewrite_as_keep_scale(repo: Path, slug: str, factor: float) -> None:
    """A sidecar a keep-scale writer could leave: a stale, larger scale and no ±127 row."""
    path = embeddings_path(repo, _DATASET, slug)
    old = read_embeddings(path)
    texts = [i.text for i in build_embedding_inputs(_markdown_of(repo, slug))]
    scale = old.scale * factor
    rows = [quantize_at_scale(_unit_vector(text), scale) for text in texts]
    pairs = [(sid, row) for (sid, _), row in zip(old.sections, rows, strict=True)]
    write_embeddings(path, pairs, scale, old.dim, embedding_space_id=old.embedding_space_id)
    subprocess.run(["git", "commit", "-qam", "keep-scale sidecar"], cwd=repo, check=True)


def _markdown_of(repo: Path, doc_id: str) -> str:
    record = read_manifest(repo / "manifest.json").documents[doc_id]
    return (repo / record.markdown_path).read_text(encoding="utf-8")


def test_a_keep_scale_sidecar_without_a_full_scale_row_is_paid_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "keep")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    _rewrite_as_keep_scale(repo, "lov-grow", 1.2)
    embedder = _PinnedEmbedder()

    _sync(settings, monkeypatch, _s1(), embedder)

    rebuilt = _settings(tmp_path / "full")
    _sync(rebuilt, monkeypatch, _s1(), _PinnedEmbedder())
    assert _sidecars(repo) == _sidecars(rebuilt.lovverk_repo_path)
    grow = _texts(repo, "lov-grow")
    assert [call for call in embedder.calls if set(call) & set(grow)] == [grow]


@pytest.mark.parametrize("scale", [0.0, -0.01, float("inf"), float("nan")])
def test_a_scale_that_is_not_finite_and_positive_is_never_trusted(scale: float) -> None:
    loud = np.array([127, -3, 5, 0], dtype=np.int8)
    inputs = build_embedding_inputs("## § 1. A\n\na\n")
    rows = {inputs[0].text: loud}

    assert exact_prior(PriorVectors(rows=rows, scale=scale), inputs) is None
    assert exact_prior(PriorVectors(rows=rows, scale=0.01), inputs) is not None


def test_unreadable_committed_markdown_means_no_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "utf8")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    record = read_manifest(repo / "manifest.json").documents["lov-grow"]
    (repo / record.markdown_path).write_bytes(b"\xff\xfe not utf-8")

    assert load_prior_vectors(repo, record, embedding_target(_PinnedEmbedder())) is None


class _RemoteShaped:
    """A store with a local copy, like RemoteEntryStore; any remote access is recorded."""

    def __init__(self, local: LocalEntryStore) -> None:
        self.local = local
        self.remote_calls: list[str] = []

    def get(self, key: EntryKey) -> CachedVectors | None:
        self.remote_calls.append("get")
        return self.local.get(key)

    def put(self, entry: CachedVectors) -> CachedVectors:
        self.remote_calls.append("put")
        return self.local.put(entry)


def test_the_change_paths_cache_lookup_reads_only_the_local_copy(tmp_path: Path) -> None:
    """A remote lookup reconciles (uploads, adopts, may refuse); the change path must not."""
    local = LocalEntryStore.open(tmp_path / "cache", (), read_policy_from_env())
    store = _RemoteShaped(local)
    provider = _PinnedEmbedder()
    cached = CachingEmbedder(provider, store, provider.space_id)
    inputs = build_embedding_inputs("## § 1. A\n\nførste\n\n## § 2. B\n\nandre\n")
    digest = hash_embedding_inputs(inputs)
    q0, s0 = quantize_int8(np.vstack([_unit_vector(i.text) for i in inputs]))
    prior = PriorVectors(rows={i.text: q0[n] for n, i in enumerate(inputs)}, scale=s0)

    rows, scale = document_vectors(ReusingEmbedder(cached, prior), inputs, digest)

    assert store.remote_calls == []
    assert provider.paid == []
    assert np.array_equal(rows, q0)
    full = cached.document_vectors(inputs, digest)
    assert store.remote_calls == ["get", "put"]
    served, _ = document_vectors(ReusingEmbedder(cached, prior), inputs, digest)
    assert store.remote_calls == ["get", "put"]
    assert np.array_equal(served, full.vectors)
    assert scale == s0
