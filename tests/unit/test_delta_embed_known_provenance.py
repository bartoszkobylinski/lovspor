"""Unknown quantization provenance forces a full re-embed (#936, owner decision P7 E7).

Int8 rows cannot prove their header scale is ``float32(max|v| / 127)`` of
their own floats: a sidecar holding a ±127 row under a stale, larger scale
passes every byte check. So a committed row is carried only from a sidecar
whose bytes match the digest this engine's writer stamped on the record or,
unstamped, the audited legacy set; anything else is paid and counted in full.
"""

import json
from pathlib import Path

import numpy as np
import pytest

import lovspor.embeddings.provenance as provenance_module
from lovspor.embeddings.inputs import hash_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.embeddings.provenance import (
    attested_sidecars,
    has_known_provenance,
    parse_attested,
    sidecar_sha256,
)
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.errors import ConfigError, MassReembedError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, read_manifest, write_manifest
from lovspor.sync.delta_embed import load_prior_vectors
from lovspor.sync.orchestrator import _embeddings_path, _tombstone
from tests.unit.test_delta_embed import (
    _SECTION_ONE,
    _SECTION_TWO_NEW,
    _inputs,
    _law,
    _published,
    _record,
    _sync,
    _target,
    _TextEmbedder,
)
from tests.unit.test_sync_orchestrator import _git


def _sidecar(settings: Settings) -> Path:
    return _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a")


def _unstamp(settings: Settings) -> None:
    """Commit the record as a legacy writer left it: no sidecar digest."""
    path = settings.lovverk_repo_path / "manifest.json"
    manifest = read_manifest(path)
    record = manifest.documents["lov-a"].model_copy(update={"embedding_sidecar_sha256": None})
    write_manifest(manifest.model_copy(update={"documents": {"lov-a": record}}), path)
    _git(settings.lovverk_repo_path, "commit", "-q", "-am", "legacy record")


def _stale_scale(settings: Settings) -> None:
    """Same int8 rows (one still at ±127), header scale doubled: no byte check can tell."""
    path = _sidecar(settings)
    sidecar = read_embeddings(path)
    write_embeddings(
        path,
        sidecar.sections,
        sidecar.scale * 2,
        sidecar.dim,
        embedding_space_id=sidecar.embedding_space_id,
    )
    _git(settings.lovverk_repo_path, "commit", "-q", "-am", "stale scale")


def _attest(monkeypatch: pytest.MonkeyPatch, *pairs: tuple[str, str | None]) -> None:
    monkeypatch.setattr(provenance_module, "attested_sidecars", lambda: frozenset(pairs))


def _pair(settings: Settings) -> tuple[str, str | None]:
    return sidecar_sha256(_sidecar(settings)), _record(settings).embedding_input_hash


def test_the_writer_stamps_the_digest_of_the_bytes_it_wrote(tmp_path: Path) -> None:
    settings = _published(tmp_path)

    assert _record(settings).embedding_sidecar_sha256 == sidecar_sha256(_sidecar(settings))
    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is not None


def test_an_unstamped_sidecar_outside_the_audited_set_is_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    _unstamp(settings)
    _attest(monkeypatch)

    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is None


def test_an_unstamped_sidecar_in_the_audited_set_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    _unstamp(settings)
    _attest(monkeypatch, _pair(settings))

    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is not None


def test_an_attested_sidecar_does_not_vouch_for_a_record_naming_other_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    _unstamp(settings)
    digest, _input_hash = _pair(settings)
    _attest(monkeypatch, (digest, "e" * 64))

    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is None


@pytest.mark.parametrize("stamped", [True, False], ids=["stamped-record", "legacy-record"])
def test_a_stale_scale_sidecar_is_not_reused_though_it_passes_every_byte_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stamped: bool
) -> None:
    settings = _published(tmp_path)
    _attest(monkeypatch, _pair(settings))
    if not stamped:
        _unstamp(settings)
    _stale_scale(settings)
    rows = read_embeddings(_sidecar(settings)).sections
    assert any(int(np.abs(row.astype(np.int16)).max()) == 127 for _, row in rows)

    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is None


def test_a_stamped_record_vouches_only_for_its_own_bytes_even_if_they_are_attested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _attest(monkeypatch, ("b" * 64, "d" * 64))

    assert not has_known_provenance("a" * 64, "b" * 64, "d" * 64)
    assert has_known_provenance("b" * 64, "b" * 64, None)
    assert has_known_provenance(None, "b" * 64, "d" * 64)
    assert not has_known_provenance(None, "b" * 64, None)
    assert not has_known_provenance(None, "b" * 64, "e" * 64)
    assert not has_known_provenance(None, "c" * 64, "d" * 64)


def test_unknown_provenance_on_the_change_path_pays_and_counts_the_whole_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    _unstamp(settings)
    _attest(monkeypatch)
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    inputs = _inputs(changed)
    full = sum(count_input_tokens(item.text) for item in inputs)
    refused = settings.model_copy(update={"reembed_guard_max_tokens": full - 1})

    with pytest.raises(MassReembedError, match=f"token workload {full:,} > {full - 1:,}"):
        _sync(refused, monkeypatch, changed, _TextEmbedder())

    embedder = _TextEmbedder()
    _sync(settings, monkeypatch, changed, embedder)
    assert embedder.paid == [item.text for item in inputs]
    record = _record(settings)
    assert record.embedding_input_hash == hash_embedding_inputs(inputs)
    assert record.embedding_sidecar_sha256 == sidecar_sha256(_sidecar(settings))


def test_a_full_re_embed_after_unknown_provenance_writes_the_rebuild_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    rebuilt = _published(tmp_path / "rebuild", bodies=(_SECTION_ONE, _SECTION_TWO_NEW))
    settings = _published(tmp_path / "delta")
    _attest(monkeypatch)
    _unstamp(settings)
    _stale_scale(settings)

    _sync(settings, monkeypatch, changed, _TextEmbedder())

    assert _sidecar(settings).read_bytes() == _sidecar(rebuilt).read_bytes()


def test_the_shipped_audited_set_loads_and_holds_only_digests() -> None:
    attested_sidecars.cache_clear()
    digests = attested_sidecars()

    assert len(digests) == 3930
    assert len({sidecar for sidecar, _ in digests}) == 3930
    assert all(len(d) == 64 and not d.strip("0123456789abcdef") for pair in digests for d in pair)


_A, _B = "a" * 64, "b" * 64


@pytest.mark.parametrize(
    "line",
    [
        _A,
        f"{'A' * 64} {_B}",
        f"{_A} {'b' * 63}",
        f"{'g' * 64} {_B}",
        f"{_A} {_B} path",
        f"{_A}  {_B}",
    ],
)
def test_a_damaged_audited_set_is_refused(line: str) -> None:
    with pytest.raises(ConfigError, match="not a digest pair"):
        parse_attested(f"# header\n\n{_A} {_B}\n{line}\n")


def test_comments_and_blank_lines_are_not_digests() -> None:
    assert parse_attested(f"# c\n\n  {_A} {_B}  \n#{_B} {_A}\n") == frozenset({(_A, _B)})


def test_an_unstamped_manifest_serializes_without_the_field(tmp_path: Path) -> None:
    settings = _published(tmp_path)
    record = _record(settings)
    legacy = record.model_copy(update={"embedding_sidecar_sha256": None})
    path = tmp_path / "m.json"

    write_manifest(Manifest(generated_at=record.last_seen, documents={"x": legacy}), path)
    assert "embedding_sidecar_sha256" not in json.loads(path.read_text())["documents"]["x"]
    write_manifest(Manifest(generated_at=record.last_seen, documents={"x": record}), path)
    stored = json.loads(path.read_text())["documents"]["x"]["embedding_sidecar_sha256"]
    assert stored == record.embedding_sidecar_sha256
    assert read_manifest(path).documents["x"] == record


def test_a_tombstone_drops_the_sidecar_digest(tmp_path: Path) -> None:
    settings = _published(tmp_path)

    assert _tombstone(_record(settings)).embedding_sidecar_sha256 is None
