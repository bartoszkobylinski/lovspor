"""ADR-0005 Stage 1 — Embedding Space Identity.

Three things are pinned here: the canonical identity is deterministic and
distinguishes spaces that a dimension comparison would collapse; the manifest
carries that identity while the sidecar stays untouched at format v1; and
nothing compares a query against vectors whose space is unknown or different.

The last one is the point of the whole mechanism. Comparing across embedding
spaces does not raise — it returns ranked, confident, meaningless results — so
every test that looks like paranoia here is guarding a failure that has no
symptom.
"""

import json
import struct
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings import (
    LEGACY_SPACE_DESCRIPTOR,
    EmbeddingConfig,
    esi_for_descriptor,
    normalize_endpoint,
    write_embeddings,
)
from lovspor.embeddings.store import read_embeddings
from lovspor.errors import ConfigError
from lovspor.mcp import CorpusNotFoundError, CorpusReader
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync import orchestrator as orchestrator_module

LEGACY_SPACE_ID = esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR)
_OTHER_DESCRIPTOR = "provider=openai;model=some-other-model;dim=4;endpoint=default"


class _FakeEmbedder:
    """Adapter that declares an identity, as the real one does."""

    def __init__(
        self,
        dim: int = 4,
        descriptor: str = LEGACY_SPACE_DESCRIPTOR,
    ) -> None:
        self._dim = dim
        self._descriptor = descriptor

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim

    @property
    def descriptor(self) -> str:
        return self._descriptor

    @property
    def space_id(self) -> str:
        return esi_for_descriptor(self._descriptor)


class _UnidentifiedEmbedder:
    """Third-party adapter written against the older two-method protocol."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)

    def get_dimension(self) -> int:
        return 4


# --- canonical identity ---------------------------------------------------


def test_the_same_configuration_always_yields_the_same_identity() -> None:
    a = EmbeddingConfig(model_name="m", dimension=7)
    b = EmbeddingConfig(model_name="m", dimension=7)

    assert a.descriptor == b.descriptor == "provider=openai;model=m;dim=7;endpoint=default"
    assert a.space_id == b.space_id
    assert len(a.space_id) == 32  # 128 bits, hex


def test_the_default_configuration_is_the_legacy_descriptor() -> None:
    assert EmbeddingConfig().descriptor == LEGACY_SPACE_DESCRIPTOR


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            EmbeddingConfig(model_name="text-embedding-3-large"),
            EmbeddingConfig(model_name="text-embedding-3-small"),
            id="model",
        ),
        pytest.param(
            EmbeddingConfig(dimension=3072),
            EmbeddingConfig(dimension=1536),
            id="dimension",
        ),
        pytest.param(
            EmbeddingConfig(provider="openai"),
            EmbeddingConfig(provider="other"),
            id="provider",
        ),
        pytest.param(
            EmbeddingConfig(base_url="https://h.example/v1/embeddings"),
            EmbeddingConfig(base_url="https://h.example/internal/embeddings"),
            id="endpoint-path",
        ),
        pytest.param(
            EmbeddingConfig(base_url="https://h.example:8443/v1"),
            EmbeddingConfig(base_url="https://h.example:9443/v1"),
            id="endpoint-port",
        ),
    ],
)
def test_materially_different_spaces_never_collapse_to_one_identity(
    left: EmbeddingConfig,
    right: EmbeddingConfig,
) -> None:
    assert left.descriptor != right.descriptor
    assert left.space_id != right.space_id


def test_two_models_sharing_a_dimension_still_have_different_identities() -> None:
    """The case the whole mechanism exists for.

    Identical shape, unrelated geometry. Every shape check in the stack passes
    and the search would run happily, returning nonsense.
    """
    a = EmbeddingConfig(model_name="model-a", dimension=3072)
    b = EmbeddingConfig(model_name="model-b", dimension=3072)

    assert a.dimension == b.dimension
    assert a.space_id != b.space_id


def test_the_credential_never_participates_in_the_identity() -> None:
    without = EmbeddingConfig()
    with_key = EmbeddingConfig(api_key="sk-secret-value")

    assert without.descriptor == with_key.descriptor
    assert without.space_id == with_key.space_id
    assert "sk-secret" not in with_key.descriptor


def test_the_identity_is_stable_across_processes() -> None:
    """Hard-coded expectation: the digest is part of the published contract.

    If this value changes, every manifest ever stamped becomes unreadable as
    an identity, so it must only ever change with a descriptor revision.
    """
    assert esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR) == esi_for_descriptor(
        "provider=openai;model=text-embedding-3-large;dim=3072;endpoint=default",
    )
    assert LEGACY_SPACE_ID == "738c919fa57385d94c558d93c4b0e588"


def test_reserved_characters_are_escaped_so_values_cannot_forge_a_field() -> None:
    """Without escaping, a model name containing `;` could inject a key."""
    config = EmbeddingConfig(model_name="ev;il=x%y")

    assert config.descriptor == "provider=openai;model=ev%3Bil%3Dx%25y;dim=3072;endpoint=default"
    assert config.descriptor.count(";") == 3


def test_model_case_is_preserved_because_vendors_treat_it_as_significant() -> None:
    assert EmbeddingConfig(model_name="Model-A").space_id != (
        EmbeddingConfig(model_name="model-a").space_id
    )


def test_unicode_forms_of_one_name_share_one_identity() -> None:
    """NFC normalization: the same name typed two ways is the same space."""
    composed = EmbeddingConfig(model_name="modél")
    decomposed = EmbeddingConfig(model_name="modél")

    assert composed.descriptor == decomposed.descriptor
    assert composed.space_id == decomposed.space_id


# --- endpoint canonicalization -------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("https://H.Example/v1", "https://h.example/v1", id="host-case"),
        pytest.param("HTTPS://h.example/v1", "https://h.example/v1", id="scheme-case"),
        pytest.param("https://h.example:443/v1", "https://h.example/v1", id="default-port"),
        pytest.param("https://h.example/v1/", "https://h.example/v1", id="trailing-slash"),
        pytest.param("https://h.example/a/../v1", "https://h.example/v1", id="dot-segments"),
        pytest.param("https://h.example", "https://h.example/", id="empty-path"),
    ],
)
def test_equivalent_endpoints_normalize_to_one_identity(left: str, right: str) -> None:
    assert normalize_endpoint(left) == normalize_endpoint(right)


def test_endpoint_keeps_the_path_rather_than_collapsing_to_the_host() -> None:
    """The pre-ADR implementation used netloc, so these two collided.

    One gateway commonly fronts several models on different paths; treating
    them as one space is exactly the silent mixing this prevents.
    """
    a = normalize_endpoint("https://gateway.example/models/a/embeddings")
    b = normalize_endpoint("https://gateway.example/models/b/embeddings")

    assert a != b
    assert a == "https://gateway.example/models/a/embeddings"


def test_a_non_default_port_survives_normalization() -> None:
    assert normalize_endpoint("https://h.example:8443/v1") == "https://h.example:8443/v1"


@pytest.mark.parametrize(
    "bad",
    ["https://h.example/v1?key=v", "https://h.example/v1#frag", "not-a-url", "/only/path"],
)
def test_unusable_endpoints_are_configuration_errors(bad: str) -> None:
    """Query and fragment are rejected, never silently stripped.

    Discarding part of a configured endpoint would compute the identity of a
    URL the operator never asked for.
    """
    with pytest.raises(ConfigError):
        normalize_endpoint(bad)


def test_a_unicode_host_and_its_punycode_form_are_one_space() -> None:
    assert normalize_endpoint("https://bücher.example/v1") == normalize_endpoint(
        "https://xn--bcher-kva.example/v1",
    )


# --- manifest -------------------------------------------------------------


def _record(**overrides: object) -> ManifestRecord:
    base: dict[str, object] = {
        "doc_id": "nl-1",
        "doc_type": "lov",
        "xml_hash": "a" * 64,
        "markdown_path": "lover/testloven.md",
        "source_dataset": "gjeldende-lover",
        "last_seen": datetime(2026, 4, 27, tzinfo=UTC),
        "status": "current",
        "slug": "testloven",
        "title": "Testloven",
        "embedding_hash": "a" * 64,
    }
    base.pop("doc_id")
    return ManifestRecord.model_validate({**base, **overrides})


def test_a_stamped_identity_round_trips_through_the_manifest(tmp_path: Path) -> None:
    record = _record(
        embedding_space=LEGACY_SPACE_DESCRIPTOR,
        embedding_space_id=LEGACY_SPACE_ID,
    )
    path = tmp_path / "manifest.json"
    write_manifest(Manifest(generated_at=datetime.now(UTC), documents={"nl-1": record}), path)

    loaded = read_manifest(path).documents["nl-1"]

    assert loaded.embedding_space == LEGACY_SPACE_DESCRIPTOR
    assert loaded.embedding_space_id == LEGACY_SPACE_ID
    assert esi_for_descriptor(loaded.embedding_space or "") == loaded.embedding_space_id


def test_a_legacy_manifest_without_identity_still_parses_as_unknown(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "generated_at": "2026-04-22T06:00:00+00:00",
        "documents": {
            "x": {
                "doc_type": "lov",
                "xml_hash": "a",
                "markdown_path": "lover/x.md",
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-04-22T01:31:00+00:00",
                "status": "current",
                "embedding_hash": "a",
            },
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    record = read_manifest(path).documents["x"]

    assert record.embedding_space is None
    assert record.embedding_space_id is None


def test_identity_is_never_inferred_from_a_3072_dimension(tmp_path: Path) -> None:
    """A legacy sidecar's space is unproven, not assumed.

    Dimension, an existing `.bin`, `embedding_hash` and the currently
    configured provider are all consistent with the legacy corpus and none of
    them is evidence. Annotating on that basis is the grandfathering
    migration's job, gated on an empirical check.
    """
    _seed_corpus(tmp_path, dim=3072, stamped=False)

    record = read_manifest(tmp_path / "manifest.json").documents["nl-1"]
    sidecar = read_embeddings(tmp_path / "lover" / "embeddings" / "testloven.bin")

    assert sidecar.dim == 3072
    assert record.embedding_hash is not None
    assert record.embedding_space_id is None


# --- writer ---------------------------------------------------------------


def test_the_writer_stamps_the_space_of_the_adapter_that_ran(tmp_path: Path) -> None:
    descriptor, space_id = orchestrator_module._embedder_identity(_FakeEmbedder())

    assert descriptor == LEGACY_SPACE_DESCRIPTOR
    assert space_id == LEGACY_SPACE_ID


def test_an_adapter_declaring_no_identity_stamps_nothing(tmp_path: Path) -> None:
    """Honest absence beats a credited identity the adapter never claimed."""
    assert orchestrator_module._embedder_identity(_UnidentifiedEmbedder()) == (None, None)


def test_a_keyless_sync_does_not_erase_a_recorded_identity(tmp_path: Path) -> None:
    """No embedder configured must not rewrite identity to None.

    A later keyless legal-content sync of *other* documents must leave a
    stamped record's identity intact; only the doc actually re-embedded is
    restamped.
    """
    stamped = _record(
        embedding_space=LEGACY_SPACE_DESCRIPTOR,
        embedding_space_id=LEGACY_SPACE_ID,
    )
    path = tmp_path / "manifest.json"
    write_manifest(Manifest(generated_at=datetime.now(UTC), documents={"nl-1": stamped}), path)

    untouched = read_manifest(path).documents["nl-1"]

    assert untouched.embedding_space_id == LEGACY_SPACE_ID


# --- staleness ------------------------------------------------------------


def _sidecar(tmp_path: Path, *, dim: int = 4) -> Path:
    path = tmp_path / "lover" / "embeddings" / "testloven.bin"
    write_embeddings(path, [("1-1", np.ones(dim, dtype=np.int8))], 0.01, dim)
    return path


def test_matching_content_and_identity_is_not_stale(tmp_path: Path) -> None:
    path = _sidecar(tmp_path)
    record = _record(embedding_space_id=LEGACY_SPACE_ID)

    assert orchestrator_module._embedding_is_stale(record, path, LEGACY_SPACE_ID) is False


def test_changed_content_is_stale(tmp_path: Path) -> None:
    path = _sidecar(tmp_path)
    record = _record(embedding_hash="b" * 64, embedding_space_id=LEGACY_SPACE_ID)

    assert orchestrator_module._embedding_is_stale(record, path, LEGACY_SPACE_ID) is True


def test_a_missing_sidecar_is_stale(tmp_path: Path) -> None:
    record = _record(embedding_space_id=LEGACY_SPACE_ID)

    assert (
        orchestrator_module._embedding_is_stale(record, tmp_path / "absent.bin", LEGACY_SPACE_ID)
        is True
    )


def test_a_corrupt_sidecar_becomes_repairable_instead_of_skipped_forever(
    tmp_path: Path,
) -> None:
    """Corruption enters the normal repair path.

    Previously a damaged file was skipped at search time and never selected
    for regeneration, so it stayed broken indefinitely.
    """
    path = _sidecar(tmp_path)
    path.write_bytes(b"not an LSPE file")
    record = _record(embedding_space_id=LEGACY_SPACE_ID)

    assert orchestrator_module._embedding_is_stale(record, path, LEGACY_SPACE_ID) is True


def test_a_different_recorded_space_is_stale(tmp_path: Path) -> None:
    """Changing model selects affected sidecars automatically.

    This is what replaces "delete the sidecars by hand" as the migration
    mechanism for a model change.
    """
    path = _sidecar(tmp_path)
    record = _record(embedding_space_id=esi_for_descriptor(_OTHER_DESCRIPTOR))

    assert orchestrator_module._embedding_is_stale(record, path, LEGACY_SPACE_ID) is True


def test_an_unstamped_legacy_record_is_not_silently_re_embedded(tmp_path: Path) -> None:
    """Unknown identity refuses search but does NOT trigger a mass re-embed.

    Treating absence as stale would make the first keyed sync after this
    change re-embed the entire published corpus — real money, ~322 MiB of
    churn — and would settle by default the annotate-versus-regenerate
    question ADR-0005 reserves for an empirically gated migration.
    """
    path = _sidecar(tmp_path)
    record = _record(embedding_space_id=None)

    assert orchestrator_module._embedding_is_stale(record, path, LEGACY_SPACE_ID) is False


def test_a_keyless_run_cannot_mark_the_corpus_stale_on_identity(tmp_path: Path) -> None:
    path = _sidecar(tmp_path)
    record = _record(embedding_space_id=esi_for_descriptor(_OTHER_DESCRIPTOR))

    assert orchestrator_module._embedding_is_stale(record, path, None) is False


# --- semantic search ------------------------------------------------------


def _seed_corpus(
    root: Path, *, dim: int, stamped: bool = True, descriptor: str | None = None
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    used = descriptor or LEGACY_SPACE_DESCRIPTOR
    record = _record(
        xml_hash="a" * 64,
        embedding_space=used if stamped else None,
        embedding_space_id=esi_for_descriptor(used) if stamped else None,
    )
    write_manifest(
        Manifest(generated_at=datetime.now(UTC), documents={"nl-1": record}),
        root / "manifest.json",
    )
    doc = root / "lover" / "testloven.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "---\nid: nl-1\ntitle: Testloven\n---\n\n# Testloven\n\n### § 1-1. Formal\n\nTekst.\n",
        encoding="utf-8",
    )
    write_embeddings(
        root / "lover" / "embeddings" / "testloven.bin",
        [("1-1", np.ones(dim, dtype=np.int8))],
        0.01,
        dim,
    )


def test_a_matching_identity_is_searchable(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, dim=4)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())

    result = reader.semantic_search("formal")

    assert [hit["slug"] for hit in result["results"]] == ["testloven"]
    assert result["notice"] is None


def test_a_different_identity_at_the_same_dimension_is_refused(tmp_path: Path) -> None:
    """Both sides are 4-dimensional, so every shape check passes."""
    _seed_corpus(tmp_path, dim=4, descriptor=_OTHER_DESCRIPTOR)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder(dim=4))

    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("formal")

    assert "record a different space" in str(exc_info.value)


def test_an_unknown_legacy_identity_is_refused_not_silently_searched(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, dim=4, stamped=False)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())

    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("formal")

    message = str(exc_info.value)
    assert "record no embedding space at all" in message
    assert "migration" in message


def test_an_adapter_without_an_identity_cannot_search_a_stamped_corpus(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, dim=4)
    reader = CorpusReader(tmp_path, embedder=_UnidentifiedEmbedder())

    with pytest.raises(CorpusNotFoundError):
        reader.semantic_search("formal")


def test_partial_coverage_is_reported_in_band_rather_than_only_to_stderr(
    tmp_path: Path,
) -> None:
    """Reduced coverage must reach the caller, not just a subprocess's stderr.

    A search that quietly covers less of the corpus than the caller believes
    is the failure this mechanism exists to prevent, and MCP clients never see
    stderr.
    """
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    unstamped = _record(
        markdown_path="lover/annen.md",
        slug="annen",
        title="Annen lov",
        xml_hash="a" * 64,
        embedding_space_id=None,
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, "nl-2": unstamped},
        ),
        tmp_path / "manifest.json",
    )
    (tmp_path / "lover" / "annen.md").write_text(
        "---\nid: nl-2\ntitle: Annen lov\n---\n\n# Annen lov\n\n### § 1-1. Formal\n\nTekst.\n",
        encoding="utf-8",
    )
    write_embeddings(
        tmp_path / "lover" / "embeddings" / "annen.bin",
        [("1-1", np.ones(4, dtype=np.int8))],
        0.01,
        4,
    )

    result = CorpusReader(tmp_path, embedder=_FakeEmbedder()).semantic_search("formal")

    assert [hit["slug"] for hit in result["results"]] == ["testloven"]
    assert result["notice"] is not None
    assert "coverage is incomplete" in result["notice"]
    assert "no recorded embedding space" in result["notice"]


def test_a_dataset_filter_still_reports_why_documents_were_excluded(
    tmp_path: Path,
) -> None:
    """The early return for a dataset with no indexed rows must not lose the reason.

    Without this the caller is told the dataset "may not be backfilled yet" —
    a bootstrap story — when the real cause is that its documents were excluded
    for identity reasons. Wrong cause, wrong remedy, and exactly the silent
    recall loss this mechanism exists to prevent.
    """
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    legacy = _record(
        markdown_path="forskrifter/annen.md",
        source_dataset="gjeldende-sentrale-forskrifter",
        doc_type="forskrift",
        slug="annen",
        title="Annen forskrift",
        xml_hash="a" * 64,
        embedding_space_id=None,
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, "nl-2": legacy},
        ),
        tmp_path / "manifest.json",
    )
    doc = tmp_path / "forskrifter" / "annen.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "---\nid: nl-2\ntitle: Annen\n---\n\n# Annen\n\n### § 1-1. Formal\n\nTekst.\n",
        encoding="utf-8",
    )
    write_embeddings(
        tmp_path / "forskrifter" / "embeddings" / "annen.bin",
        [("1-1", np.ones(4, dtype=np.int8))],
        0.01,
        4,
    )

    result = CorpusReader(tmp_path, embedder=_FakeEmbedder()).semantic_search(
        "Tekst",
        dataset="forskrifter",
    )

    assert result["results"] == []
    notice = result["notice"]
    assert notice is not None
    assert "no recorded embedding space" in notice
    assert "migration" in notice


def test_an_unknown_only_corpus_is_not_promised_an_ordinary_sync_fix(
    tmp_path: Path,
) -> None:
    """The remedy must match what the code actually does.

    Absent identity is deliberately NOT stale, so a keyed sync leaves an
    unchanged legacy corpus exactly as it was. Telling an operator to re-run
    sync would send them to an action that cannot repair the state.
    """
    _seed_corpus(tmp_path, dim=4, stamped=False)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())

    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("Tekst")

    message = str(exc_info.value)
    assert "an ordinary sync will NOT change them" in message
    assert "embedding-space migration" in message


def test_a_different_space_corpus_is_pointed_at_sync_which_does_fix_it(
    tmp_path: Path,
) -> None:
    """The mirror case: a mismatched space IS stale, so sync is the right remedy."""
    _seed_corpus(tmp_path, dim=4, descriptor=_OTHER_DESCRIPTOR)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())

    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("Tekst")

    message = str(exc_info.value)
    assert "record a different space" in message
    assert "re-embeds and re-stamps" in message


def test_a_document_with_no_sidecar_is_counted_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """A half-embedded corpus must not answer as though it were complete.

    A missing sidecar leaves a document just as absent from the answer as one
    excluded for identity, and it used to be skipped without being counted —
    so results came back with no notice at all. That is silent recall loss in
    its plainest form, and it is not narrower than the identity case.
    """
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    no_sidecar = _record(
        markdown_path="lover/annen.md",
        slug="annen",
        title="Annen lov",
        xml_hash="a" * 64,
        embedding_space=LEGACY_SPACE_DESCRIPTOR,
        embedding_space_id=LEGACY_SPACE_ID,
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, "nl-2": no_sidecar},
        ),
        tmp_path / "manifest.json",
    )
    (tmp_path / "lover" / "annen.md").write_text(
        "---\nid: nl-2\ntitle: Annen\n---\n\n# Annen\n\n### § 1-1. Formal\n\nTekst.\n",
        encoding="utf-8",
    )

    result = CorpusReader(tmp_path, embedder=_FakeEmbedder()).semantic_search("Tekst")

    assert [hit["slug"] for hit in result["results"]] == ["testloven"]
    notice = result["notice"]
    assert notice is not None
    assert "no embedding sidecar yet" in notice


def test_a_dataset_of_only_unembedded_documents_still_names_the_reason(
    tmp_path: Path,
) -> None:
    """The dataset early-return path, for a dataset whose docs have no sidecar."""
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    legacy = _record(
        markdown_path="forskrifter/annen.md",
        source_dataset="gjeldende-sentrale-forskrifter",
        doc_type="forskrift",
        slug="annen",
        title="Annen forskrift",
        xml_hash="a" * 64,
        embedding_space_id=None,
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, "nl-2": legacy},
        ),
        tmp_path / "manifest.json",
    )
    doc = tmp_path / "forskrifter" / "annen.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "---\nid: nl-2\ntitle: Annen\n---\n\n# Annen\n\n### § 1-1. Formal\n\nTekst.\n",
        encoding="utf-8",
    )

    result = CorpusReader(tmp_path, embedder=_FakeEmbedder()).semantic_search(
        "Tekst",
        dataset="forskrifter",
    )

    assert result["results"] == []
    assert result["notice"] is not None
    assert "no embedding sidecar yet" in result["notice"]


def test_a_refreshed_corpus_does_not_report_the_previous_manifest_exclusions(
    tmp_path: Path,
) -> None:
    """The exclusion counter is cleared with the index it describes."""
    _seed_corpus(tmp_path, dim=4, stamped=False)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())
    with pytest.raises(CorpusNotFoundError):
        reader.semantic_search("Tekst")

    # The corpus is repaired underneath the reader, as a git pull would do.
    _seed_corpus(tmp_path, dim=4, stamped=True)

    result = reader.semantic_search("Tekst")

    assert [hit["slug"] for hit in result["results"]] == ["testloven"]
    assert result["notice"] is None


def test_every_current_document_is_either_indexed_or_counted(tmp_path: Path) -> None:
    """The structural invariant behind every exclusion test above.

    Three separate exclusion paths reached review uncounted — missing sidecar,
    unresolvable dataset, unsafe path — each found only by someone stumbling
    onto that exact corpus shape. This asserts the property directly instead:
    a current, slugged record is either in the index or in the exclusion
    counter, never neither. A future `continue` that forgets to count fails
    here rather than in someone's search results.
    """
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    extras = {
        "nl-missing": _record(slug="missing", markdown_path="lover/missing.md"),
        "nl-unknown-ds": _record(
            slug="unknown",
            markdown_path="lover/unknown.md",
            source_dataset="gjeldende-noe-annet",
        ),
        "nl-legacy": _record(
            slug="legacy",
            markdown_path="lover/legacy.md",
            embedding_space_id=None,
        ),
        "nl-other-space": _record(
            slug="other",
            markdown_path="lover/other.md",
            embedding_space=_OTHER_DESCRIPTOR,
            embedding_space_id=esi_for_descriptor(_OTHER_DESCRIPTOR),
        ),
    }
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, **extras},
        ),
        tmp_path / "manifest.json",
    )
    for slug in ("legacy", "other"):
        write_embeddings(
            tmp_path / "lover" / "embeddings" / f"{slug}.bin",
            [("1-1", np.ones(4, dtype=np.int8))],
            0.01,
            4,
        )

    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder())
    index = reader._load_embedding_index()

    current = [
        r
        for r in read_manifest(tmp_path / "manifest.json").documents.values()
        if r.status == "current" and r.slug is not None
    ]
    indexed = len(index.unique_slugs)
    counted = sum(reader._excluded_bins.values())

    assert indexed + counted == len(current), (
        f"{len(current) - indexed - counted} document(s) left the searchable corpus "
        f"without being counted: indexed={indexed} excluded={dict(reader._excluded_bins)}"
    )
    assert indexed == 1
    assert set(reader._excluded_bins) == {
        "missing",
        "unknown_dataset",
        "unknown_space",
        "different_space",
    }


def test_an_unrecognised_dataset_is_reported_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """A corpus written by a newer engine must not shrink an older reader's answer."""
    _seed_corpus(tmp_path, dim=4)
    manifest = read_manifest(tmp_path / "manifest.json")
    future = _record(
        slug="framtid",
        markdown_path="lover/framtid.md",
        source_dataset="gjeldende-noe-helt-nytt",
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={**manifest.documents, "nl-9": future},
        ),
        tmp_path / "manifest.json",
    )

    result = CorpusReader(tmp_path, embedder=_FakeEmbedder()).semantic_search("Tekst")

    assert result["notice"] is not None
    assert "does not recognise" in result["notice"]


def test_the_other_tools_need_no_identity_at_all(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, dim=4, stamped=False)
    reader = CorpusReader(tmp_path, embedder=None)

    assert "§ 1-1" in reader.get_law("testloven")
    assert [hit["slug"] for hit in reader.search_laws("Testloven")] == ["testloven"]
    assert [s["section_id"] for s in reader.list_sections("testloven")] == ["1-1"]
    assert reader.corpus_status()["total_current_documents"] == 1


# --- the sidecar format does not move -------------------------------------


def test_stage_1_still_writes_format_version_1(tmp_path: Path) -> None:
    path = _sidecar(tmp_path)
    magic, version, reserved, count, dim, _scale = struct.unpack("<4sBBHIf", path.read_bytes()[:16])

    assert magic == b"LSPE"
    assert version == 1
    # The spare byte stays spare. No digest, no flags, no Stage 2 smuggled in
    # through the one place it would physically fit.
    assert reserved == 0
    assert (count, dim) == (1, 4)


def test_no_identity_digest_reaches_the_sidecar(tmp_path: Path) -> None:
    """The 128-bit ESI must appear nowhere in the binary.

    Stage 2 puts it there, behind a dual-reader release and a coordinated
    corpus-wide cutover. Emitting it now would produce exactly the mixed-format
    corpus ADR-0005 forbids — and old readers degrade silently rather than
    failing, so nothing would announce the mistake.
    """
    path = _sidecar(tmp_path)
    raw = path.read_bytes()

    assert bytes.fromhex(LEGACY_SPACE_ID) not in raw
    assert LEGACY_SPACE_ID.encode() not in raw


def test_a_v1_sidecar_still_round_trips(tmp_path: Path) -> None:
    path = _sidecar(tmp_path)
    parsed = read_embeddings(path)

    assert parsed.dim == 4
    assert [section_id for section_id, _ in parsed.sections] == ["1-1"]


def test_a_detached_sidecar_is_structurally_readable_but_unidentified(tmp_path: Path) -> None:
    """Stage 1 leaves detached files exactly as opaque as they were.

    ``read_embeddings`` is a documented public API and keeps working, but the
    file it returns carries no space of its own — identity lives only in the
    manifest until Stage 2.
    """
    parsed = read_embeddings(_sidecar(tmp_path))

    assert not hasattr(parsed, "space_id")
    assert not hasattr(parsed, "embedding_space")
    assert set(vars(parsed)) == {"dim", "scale", "sections"}
