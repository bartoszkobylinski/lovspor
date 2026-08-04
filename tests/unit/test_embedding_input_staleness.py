"""ADR-0006 condition 5 and the mass-re-embed guard.

Absent-is-stale under keyed sync is the review-driven core of the accepted
contract (the old-writer hole); the guard is what makes that rule safe. Both
are pinned here at the unit level; the run_sync lifecycle versions live in
the integration suite.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.embeddings.inputs as inputs_module
from lovspor.embeddings.inputs import build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.store import write_embeddings
from lovspor.errors import MassReembedError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.orchestrator import (
    _BackfillItem,
    _embedding_is_stale,
    _guard_mass_reembed,
)

_ESI = "738c919fa57385d94c558d93c4b0e588"
_DOC = "---\ntitle: X\n---\n# X\n\n### § 1. Virkeområde\n\n" + ("ord " * 40) + "\n"


def _record(input_hash: str | None) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="h1",
        markdown_path="lover/x.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 8, 1, tzinfo=UTC),
        status="current",
        slug="x",
        title="X",
        embedding_hash="h1",
        embedding_space="provider=openai;model=text-embedding-3-large;dim=3072;endpoint=default",
        embedding_space_id=_ESI,
        embedding_input_hash=input_hash,
    )


def _sidecar(tmp_path: Path) -> Path:
    path = tmp_path / "lover" / "embeddings" / "x.bin"
    write_embeddings(path, [("1", np.ones(4, dtype=np.int8))], scale=0.01, dim=4)
    return path


def _current_hash() -> str:
    return hash_embedding_inputs(build_embedding_inputs(_DOC))


def test_matching_input_hash_is_fresh(tmp_path: Path) -> None:
    record = _record(_current_hash())
    assert _embedding_is_stale(record, _sidecar(tmp_path), _ESI, _current_hash()) is False


def test_a_mismatching_input_hash_is_stale(tmp_path: Path) -> None:
    record = _record("0" * 64)
    assert _embedding_is_stale(record, _sidecar(tmp_path), _ESI, _current_hash()) is True


def test_an_absent_input_hash_is_stale_on_a_keyed_run(tmp_path: Path) -> None:
    """The Codex-review rule: absence is unverifiable and old keyed writers
    strip the field, so absent must select for regeneration — the opposite of
    the ADR-0005 absent-ESI rule, on purpose."""
    record = _record(None)
    assert _embedding_is_stale(record, _sidecar(tmp_path), _ESI, _current_hash()) is True


def test_an_absent_input_hash_alone_is_not_stale_keyless(tmp_path: Path) -> None:
    """Keyless runs pass no current hash, so condition 5 never fires — core
    corpus sync must not depend on an embedder."""
    record = _record(None)
    assert _embedding_is_stale(record, _sidecar(tmp_path), None, None) is False


def test_same_count_boundary_drift_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The historical blind spot: same chunk count, same section id, different
    chunk text. The digest sees it where the count heuristic cannot."""
    record = _record(_current_hash())
    sidecar = _sidecar(tmp_path)

    original = inputs_module.split_to_token_chunks

    def shifted_boundaries(text: str, max_tokens: int = 8000) -> list[str]:
        chunks = original(text, max_tokens)
        if len(chunks) == 1 and len(chunks[0]) > 20:
            midpoint = len(chunks[0]) // 3
            return [chunks[0][:midpoint], chunks[0][midpoint:]]
        return chunks

    monkeypatch.setattr(inputs_module, "split_to_token_chunks", shifted_boundaries)
    drifted = hash_embedding_inputs(build_embedding_inputs(_DOC))
    assert drifted != record.embedding_input_hash
    assert _embedding_is_stale(record, sidecar, _ESI, drifted) is True


def test_old_writer_round_trip_drop_becomes_stale(tmp_path: Path) -> None:
    """The exact PR-review scenario: a correctly annotated record is round-
    tripped by an old writer that drops the unknown field; everything else
    stays fresh; the current keyed engine must classify it stale."""
    annotated = _record(_current_hash())
    manifest_path = tmp_path / "manifest.json"
    write_manifest(
        Manifest(generated_at=datetime(2026, 8, 1, tzinfo=UTC), documents={"x": annotated}),
        manifest_path,
    )
    # Old writer: parses with its own (older) schema and rewrites — unknown
    # fields vanish. Simulated at the JSON layer, which is what the pinned
    # drops-unknown-fields round-trip behaviour does.
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["documents"]["x"]["embedding_input_hash"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    stripped = read_manifest(manifest_path).documents["x"]
    assert stripped.embedding_input_hash is None
    assert stripped.embedding_hash == stripped.xml_hash
    assert stripped.embedding_space_id == _ESI
    sidecar = _sidecar(tmp_path)
    assert _embedding_is_stale(stripped, sidecar, _ESI, _current_hash()) is True
    assert _embedding_is_stale(stripped, sidecar, None, None) is False


def _prior(current_count: int) -> Manifest:
    records = {
        f"doc-{i}": _record(None).model_copy(
            update={"slug": f"doc-{i}", "markdown_path": f"lover/doc-{i}.md"},
        )
        for i in range(current_count)
    }
    return Manifest(generated_at=datetime(2026, 8, 1, tzinfo=UTC), documents=records)


def _selection(count: int, tokens_each: int) -> list[_BackfillItem]:
    prior = _prior(count)
    return [
        _BackfillItem(doc_id=doc_id, record=record, rendered="", input_tokens=tokens_each)
        for doc_id, record in prior.documents.items()
    ]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        **overrides,  # type: ignore[arg-type]
    )


def test_ordinary_small_repair_passes_the_guard(tmp_path: Path) -> None:
    _guard_mass_reembed(
        _selection(3, tokens_each=500),
        _prior(500),
        _settings(tmp_path),
        allow=False,
    )


def test_the_fraction_dimension_trips(tmp_path: Path) -> None:
    selection = _selection(30, tokens_each=10)
    with pytest.raises(MassReembedError) as exc_info:
        _guard_mass_reembed(selection, _prior(500), _settings(tmp_path), allow=False)
    message = str(exc_info.value)
    assert "document fraction" in message
    assert "No provider call was made" in message
    assert "--allow-mass-reembed" in message


def test_the_token_dimension_trips_independently_of_count(tmp_path: Path) -> None:
    """One enormous act must stop on workload even at a negligible fraction —
    the reason a count-only threshold was rejected at ADR acceptance."""
    selection = _selection(1, tokens_each=2_000_000)
    with pytest.raises(MassReembedError) as exc_info:
        _guard_mass_reembed(selection, _prior(500), _settings(tmp_path), allow=False)
    assert "token workload" in str(exc_info.value)


def test_the_fraction_dimension_skips_tiny_corpora(tmp_path: Path) -> None:
    """A bootstrap corpus backfilling 100% of itself is the normal keyless-
    then-keyed lifecycle, not drift — the fraction floor mirrors the
    mass-removal guard. The token dimension still applies at every size."""
    _guard_mass_reembed(
        _selection(3, tokens_each=100),
        _prior(3),
        _settings(tmp_path),
        allow=False,
    )
    with pytest.raises(MassReembedError):
        _guard_mass_reembed(
            _selection(3, tokens_each=2_000_000),
            _prior(3),
            _settings(tmp_path),
            allow=False,
        )


def test_the_explicit_override_permits_a_large_repair(tmp_path: Path) -> None:
    _guard_mass_reembed(
        _selection(30, tokens_each=2_000_000),
        _prior(500),
        _settings(tmp_path),
        allow=True,
    )


def test_thresholds_are_operator_configurable(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        reembed_guard_max_fraction=0.5,
        reembed_guard_max_tokens=100_000_000,
    )
    _guard_mass_reembed(_selection(100, tokens_each=200_000), _prior(500), settings, allow=False)
