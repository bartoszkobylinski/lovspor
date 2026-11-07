"""A completed document remains usable after its transient units are pruned."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.embeddings.cache import (
    CachingEmbedder,
    quantized_document_vectors,
    read_policy_from_env,
    with_embedding_cache,
)
from lovspor.embeddings.cache_admin import prune
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import hash_embedding_inputs
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheExpiredError
from tests.unit.test_embedding_cache_boundary import _Provider, _settings, _write
from tests.unit.test_embedding_cache_format import _with_meta
from tests.unit.test_embedding_cache_remediation import _cached, _inputs


@pytest.mark.parametrize("remove_units", [False, True], ids=["units-present", "units-pruned"])
def test_completed_document_hit_needs_neither_units_nor_request_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remove_units: bool
) -> None:
    """Design §3.2 and §4: serve the document first; unit entries are transient."""
    root = tmp_path / "cache"
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_DIR", str(root))
    settings = _settings(tmp_path)
    reference = _write(settings, with_embedding_cache(_Provider(), settings))
    before = {path: path.read_bytes() for path in root.rglob("*.lsec")}
    documents = {path: data for path, data in before.items() if path.parent.name == "documents"}
    assert len(documents) == 1
    assert len(before) == 5

    if remove_units:
        store = LocalEntryStore.open(root, (), read_policy_from_env())
        report = prune(store)
        assert (report.expired, report.superseded_units, report.temporary, report.refused) == (
            0,
            4,
            0,
            [],
        )
        assert {path: path.read_bytes() for path in root.rglob("*.lsec")} == documents

    class HitOnlyProvider(_Provider):
        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            pytest.fail("a completed document hit must not plan provider requests")

    provider = HitOnlyProvider(fail_after=0)
    cached = with_embedding_cache(provider, settings)
    assert isinstance(cached, CachingEmbedder)
    assert _write(settings, cached) == reference
    assert provider.requests == 0
    assert cached.progress.documents_cached == 1
    assert cached.progress.units_cached == 0
    assert cached.progress.units_paid == 0
    assert {path: path.read_bytes() for path in root.rglob("*.lsec")} == (
        documents if remove_units else before
    )


@pytest.mark.parametrize("damage", ["digest", "expired", "dimension"])
def test_refused_document_is_not_rebuilt_from_valid_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """Design §3.4 and §5.4: refusal stops even when rebuilding needs no payment."""
    root = tmp_path / "cache"
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_DIR", str(root))
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    quantized_document_vectors(_cached(_Provider(), tmp_path), inputs, digest)
    (document,) = (root / "schema-1").glob("*/documents/*.lsec")
    assert len(list(root.rglob("*.lsec"))) == 3
    original = document.read_bytes()
    if damage == "digest":
        document.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    elif damage == "expired":
        document.write_bytes(
            _with_meta(original, created_at=(datetime.now(UTC) - timedelta(days=31)).isoformat())
        )
    else:
        document.write_bytes(_with_meta(original, dim=99))
    before = {path: path.read_bytes() for path in root.rglob("*.lsec")}

    class RefusalOnlyProvider(_Provider):
        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            pytest.fail("a refused document must stop before planning checkpoint reuse")

    provider = RefusalOnlyProvider(fail_after=0)
    cached = _cached(provider, tmp_path)
    error = EmbeddingCacheExpiredError if damage == "expired" else EmbeddingCacheError
    reason = {"digest": "digest", "expired": "expired", "dimension": "dim"}[damage]
    with pytest.raises(error, match=reason):
        quantized_document_vectors(cached, inputs, digest)

    assert provider.requests == 0
    assert cached.progress.documents_cached == 0
    assert cached.progress.units_cached == 0
    assert cached.progress.units_paid == 0
    assert {path: path.read_bytes() for path in root.rglob("*.lsec")} == before
    assert not list(root.rglob("*.tmp.*"))
