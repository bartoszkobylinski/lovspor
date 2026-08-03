"""Provider boundary and embedding-space safety.

Two things are pinned here. The boundary: application code asks one factory for
an :class:`EmbeddingModel` instead of naming a vendor, and the default
configuration is byte-for-byte the historical OpenAI one. And the safety
invariant that motivated the boundary — a query may only be compared against
corpus vectors from a space it is *known* to share, because the failure mode of
getting that wrong is not an exception but a page of confident, meaningless
results.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings import (
    LEGACY_SPACE_DESCRIPTOR,
    SUPPORTED_PROVIDERS,
    EmbeddingConfig,
    OpenAIEmbedder,
    create_embedder,
    esi_for_descriptor,
    space_id_of,
    write_embeddings,
)
from lovspor.embeddings.provider import UNKNOWN_SPACE_ID
from lovspor.errors import ConfigError
from lovspor.mcp import CorpusNotFoundError, CorpusReader
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest

LEGACY_SPACE_ID = esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR)

_EMBEDDING_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_APIKEY",
    "LOVSPOR_EMBEDDING_API_KEY",
    "LOVSPOR_EMBEDDING_PROVIDER",
    "LOVSPOR_EMBEDDING_MODEL",
    "LOVSPOR_EMBEDDING_DIMENSION",
    "LOVSPOR_EMBEDDING_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_embedding_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own OPENAI_API_KEY must not change what these assert."""
    for name in _EMBEDDING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _FakeEmbedder:
    """Minimal adapter that declares its own space."""

    def __init__(self, dim: int = 4, space_id: str = LEGACY_SPACE_ID) -> None:
        self._dim = dim
        self._space_id = space_id

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim

    @property
    def space_id(self) -> str:
        return self._space_id


class _LegacyProtocolEmbedder:
    """An adapter written against the two-method protocol, declaring no space."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)

    def get_dimension(self) -> int:
        return 4


# --- 1. the installed base keeps working unchanged ------------------------


def test_openai_api_key_alone_reproduces_the_historical_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-installed-base")

    config = EmbeddingConfig.from_env()

    assert config.provider == "openai"
    assert config.model_name == "text-embedding-3-large"
    assert config.dimension == 3072
    assert config.base_url is None
    assert config.api_key == "sk-installed-base"
    assert config.descriptor == LEGACY_SPACE_DESCRIPTOR
    assert config.space_id == LEGACY_SPACE_ID


def test_underscore_less_openai_apikey_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback four call sites used to re-implement independently."""
    monkeypatch.setenv("OPENAI_APIKEY", "sk-compact")

    assert EmbeddingConfig.from_env().api_key == "sk-compact"


def test_settings_built_with_only_openai_api_key_still_reaches_the_factory(
    tmp_path: Path,
) -> None:
    """Deployments and callers that set the old field must keep embedding.

    The credential landing in ``Settings.openai_api_key`` but never in the
    embedding config would present exactly like "no key configured": sync
    silently stops writing sidecars, with no error anywhere.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        openai_api_key="sk-legacy-field",
    )

    assert settings.embedding.api_key == "sk-legacy-field"
    assert settings.embedding.space_id == LEGACY_SPACE_ID
    assert isinstance(create_embedder(settings.embedding), OpenAIEmbedder)


def test_settings_mirrors_an_embedding_credential_back_to_the_legacy_field(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        embedding=EmbeddingConfig(api_key="sk-new-field"),
    )

    assert settings.openai_api_key == "sk-new-field"


def _settings_paths(tmp_path: Path) -> dict[str, dict[str, object]]:
    """Every supported way to build Settings, as kwargs."""
    base: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "lovverk_repo_path": tmp_path / "corpus",
    }
    return {
        "bare": base,
        "embedding-config-override": {
            **base,
            "embedding": EmbeddingConfig(model_name="text-embedding-3-small", dimension=1536),
        },
        "embedding-dict-override": {
            **base,
            "embedding": {"model_name": "text-embedding-3-small", "dimension": 1536},
        },
    }


@pytest.mark.parametrize("path", ["bare", "embedding-config-override", "embedding-dict-override"])
def test_from_env_credential_survives_every_construction_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """The ambient credential must reach the factory however Settings was built.

    This bug class has appeared twice: a key that lands in one field but not
    the other disables embeddings silently, and the symptom — sync quietly
    writing no sidecars — is indistinguishable from "no key configured".
    Enumerating the construction paths is the point; a single-path test is
    what let the `embedding=` override through the first time.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env(**_settings_paths(tmp_path)[path])  # type: ignore[arg-type]

    assert settings.openai_api_key == "env-key", path
    assert settings.embedding.api_key == "env-key", path
    assert create_embedder(settings.embedding) is not None, path


@pytest.mark.parametrize("path", ["bare", "embedding-config-override", "embedding-dict-override"])
def test_from_env_without_any_credential_stays_unconfigured(
    tmp_path: Path,
    path: str,
) -> None:
    """The mirror of the above: absent stays absent, on every path."""
    settings = Settings.from_env(**_settings_paths(tmp_path)[path])  # type: ignore[arg-type]

    assert settings.openai_api_key is None, path
    assert settings.embedding.api_key is None, path
    assert create_embedder(settings.embedding) is None, path


def test_from_env_embedding_override_keeps_its_own_model_and_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inheriting the ambient key must not overwrite an explicit one."""
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        embedding=EmbeddingConfig(
            model_name="text-embedding-3-small",
            dimension=1536,
            api_key="sk-explicit",
        ),
    )

    assert settings.embedding.api_key == "sk-explicit"
    assert settings.embedding.model_name == "text-embedding-3-small"
    assert settings.embedding.dimension == 1536
    assert settings.openai_api_key == "sk-explicit"


def test_from_env_explicit_key_override_beats_the_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        openai_api_key="sk-override",
    )

    assert settings.embedding.api_key == "sk-override"


@pytest.mark.parametrize(
    "blanked",
    [
        pytest.param({"openai_api_key": ""}, id="legacy-field"),
        pytest.param({"embedding": EmbeddingConfig(api_key="")}, id="embedding-config"),
    ],
)
def test_a_blanked_credential_is_not_refilled_from_the_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blanked: dict[str, object],
) -> None:
    """Blanking the key must disable embeddings, not fall back to the ambient one.

    An empty override is a decision — "run without a credential" — and quietly
    substituting whatever is exported in the environment overrides that
    decision in the one direction an operator cannot see. The numeric fields
    in Settings already use an explicit None check for the same reason; the
    credential is the field where getting it wrong matters most.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        **blanked,  # type: ignore[arg-type]
    )

    assert not settings.openai_api_key
    assert not settings.embedding.api_key
    assert create_embedder(settings.embedding) is None


def test_a_blanked_credential_is_honoured_on_direct_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
        openai_api_key="",
    )

    assert not settings.embedding.api_key
    assert create_embedder(settings.embedding) is None


def test_from_env_rejects_an_unusable_embedding_override(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid embedding override"):
        Settings.from_env(
            data_dir=tmp_path / "data",
            lovverk_repo_path=tmp_path / "corpus",
            embedding={"dimension": "not-a-number"},
        )


# --- 2. no credential: core intact, semantic capability absent ------------


def test_absent_credential_yields_no_embedder_rather_than_an_error() -> None:
    assert create_embedder(EmbeddingConfig()) is None


def test_absent_credential_is_not_a_sync_failure(tmp_path: Path) -> None:
    """``None`` is the ordinary state of an install without a key."""
    from lovspor.sync.orchestrator import _load_embedder  # noqa: PLC0415

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "corpus",
    )

    assert _load_embedder(settings) is None


# --- 3./4. the factory, and unknown providers -----------------------------


def test_factory_returns_the_configured_adapter() -> None:
    embedder = create_embedder(EmbeddingConfig(api_key="sk-test"))

    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.get_dimension() == 3072
    assert embedder.space_id == LEGACY_SPACE_ID


def test_factory_honours_model_and_dimension_overrides() -> None:
    embedder = create_embedder(
        EmbeddingConfig(api_key="sk-test", model_name="text-embedding-3-small", dimension=1536),
    )

    assert embedder is not None
    assert embedder.get_dimension() == 1536
    assert embedder.descriptor == (
        "provider=openai;model=text-embedding-3-small;dim=1536;endpoint=default"
    )


def test_unknown_provider_fails_loudly_and_never_falls_back() -> None:
    with pytest.raises(ConfigError) as exc_info:
        create_embedder(EmbeddingConfig(provider="voyage", api_key="sk-test"))

    message = str(exc_info.value)
    assert "voyage" in message
    assert "openai" in message
    assert "LOVSPOR_EMBEDDING_PROVIDER" in message


def test_unknown_provider_is_rejected_even_without_a_credential() -> None:
    """Misconfiguration must not be masked by the absent-key path.

    Returning ``None`` first would report a typo'd provider as the perfectly
    normal "no credentials" state, and the operator would never see it.
    """
    with pytest.raises(ConfigError):
        create_embedder(EmbeddingConfig(provider="nope"))


def test_only_openai_is_advertised_as_supported() -> None:
    """Docs promise exactly one provider; this is what enforces that."""
    assert frozenset({"openai"}) == SUPPORTED_PROVIDERS


# --- 5. no provider-specific knowledge in application code ----------------


@pytest.mark.parametrize(
    "module_path",
    ["src/lovspor/mcp.py", "src/lovspor/sync/orchestrator.py"],
)
def test_application_code_holds_no_openai_specific_configuration(module_path: str) -> None:
    """The MCP server and the sync engine must not name a vendor.

    They consume ``EmbeddingModel`` and ask the factory for one; every
    OpenAI-specific detail — endpoint, key env var, adapter class — belongs
    behind the adapter boundary. A regression here is how the four duplicated
    copies of the key-lookup rule appeared in the first place.
    """
    source = (Path(__file__).resolve().parents[2] / module_path).read_text(encoding="utf-8")

    assert "OpenAIEmbedder" not in source, module_path
    assert "api.openai.com" not in source, module_path
    assert 'environ.get("OPENAI_API_KEY")' not in source, module_path
    assert "OPENAI_APIKEY" not in source, module_path


# --- 8./9./10. embedding-space compatibility ------------------------------


def test_space_id_distinguishes_models_that_share_a_dimension() -> None:
    """The whole point: same dim, different space, different id.

    Two 3072-dim models produce vectors of identical shape whose cosine
    similarity is meaningless across spaces. Dimension cannot express that;
    the space id has to.
    """
    a = EmbeddingConfig(model_name="text-embedding-3-large", dimension=3072)
    b = EmbeddingConfig(model_name="some-other-3072-model", dimension=3072)

    assert a.dimension == b.dimension
    assert a.space_id != b.space_id
    assert a.space_id == LEGACY_SPACE_ID
    assert b.space_id != LEGACY_SPACE_ID


def test_a_custom_endpoint_is_not_labelled_as_openai() -> None:
    """An OpenAI-compatible host is not OpenAI.

    Same wire protocol, unknown model behind it. Labelling it ``openai:`` would
    make it collide with the corpus's space id and defeat the check.
    """
    config = EmbeddingConfig(base_url="https://embeddings.internal.example/v1/embeddings")

    assert config.descriptor == (
        "provider=openai;model=text-embedding-3-large;dim=3072;"
        "endpoint=https://embeddings.internal.example/v1/embeddings"
    )
    assert config.space_id != LEGACY_SPACE_ID


def test_an_adapter_declaring_no_space_is_treated_as_unknown() -> None:
    assert space_id_of(_LegacyProtocolEmbedder()) == UNKNOWN_SPACE_ID
    assert space_id_of(None) is None
    assert space_id_of(_FakeEmbedder()) == LEGACY_SPACE_ID


def _seed_minimal_corpus(root: Path, *, dim: int, stamped: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = ManifestRecord(
        doc_id="nl-1",
        doc_type="lov",
        source_dataset="gjeldende-lover",
        markdown_path="lover/testloven.md",
        xml_hash="h",
        renderer_version=1,
        status="current",
        slug="testloven",
        title="Testloven",
        last_changed="2026-04-27",
        last_seen="2026-04-27",
        total_changes=1,
        embedding_space=LEGACY_SPACE_DESCRIPTOR if stamped else None,
        embedding_space_id=LEGACY_SPACE_ID if stamped else None,
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
    vector = np.ones(dim, dtype=np.int8)
    write_embeddings(root / "lover" / "embeddings" / "testloven.bin", [("1-1", vector)], 0.01, dim)


def test_semantic_search_refuses_an_embedder_from_a_different_space(tmp_path: Path) -> None:
    """Item 10: same dimension, known-different identity, still refused.

    Both sides are 4-dimensional here, so every shape check in the stack
    passes and the search would run happily — returning ranked nonsense. The
    space id is the only thing standing between the caller and that.
    """
    _seed_minimal_corpus(tmp_path, dim=4)
    reader = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder(dim=4, space_id="openai:some-other-model:4"),
    )

    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("formal")

    message = str(exc_info.value)
    assert "record a different one" in message
    assert "cannot be compared" in message


def test_semantic_search_refuses_an_adapter_that_declares_no_space(tmp_path: Path) -> None:
    _seed_minimal_corpus(tmp_path, dim=4)
    reader = CorpusReader(tmp_path, embedder=_LegacyProtocolEmbedder())

    with pytest.raises(CorpusNotFoundError, match="cannot be compared"):
        reader.semantic_search("formal")


def test_semantic_search_allows_the_corpus_compatible_space(tmp_path: Path) -> None:
    """The default deployment must not be caught by its own safety net."""
    _seed_minimal_corpus(tmp_path, dim=4)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder(dim=4))

    result = reader.semantic_search("formal")

    assert [hit["slug"] for hit in result["results"]] == ["testloven"]


def test_dimension_mismatch_is_rejected_before_the_vectors_ever_meet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Item 9: a corpus-compatible space but a 3072-dim corpus vs 4-dim query."""
    _seed_minimal_corpus(tmp_path, dim=3072)
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder(dim=4))

    with pytest.raises(CorpusNotFoundError, match="older model with a different dimension"):
        reader.semantic_search("formal")

    assert "skipping testloven.bin with dim 3072" in capsys.readouterr().err


# --- 7. one tool degrades, the rest do not --------------------------------


def test_without_a_provider_only_semantic_search_is_unavailable(tmp_path: Path) -> None:
    _seed_minimal_corpus(tmp_path, dim=4)
    reader = CorpusReader(tmp_path, embedder=None)

    with pytest.raises(CorpusNotFoundError, match="OPENAI_API_KEY was not set"):
        reader.semantic_search("formal")

    # The corpus-reading tools are untouched by embedding configuration.
    assert "§ 1-1" in reader.get_law("testloven")
    assert [hit["slug"] for hit in reader.search_laws("Testloven")] == ["testloven"]
    assert [section["section_id"] for section in reader.list_sections("testloven")] == ["1-1"]
    assert reader.corpus_status()["total_current_documents"] == 1


def test_mcp_reports_an_unknown_provider_instead_of_crashing_the_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A misconfigured provider must cost one tool, not sixteen.

    ``create_embedder`` raises so the sync fails loudly on a typo; the stdio
    server catches it, because taking down fifteen working tools over an
    embedding setting is the worse failure.
    """
    from lovspor.mcp import _build_embedder  # noqa: PLC0415

    monkeypatch.setenv("LOVSPOR_EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert _build_embedder() is None
    assert "unknown embedding provider" in capsys.readouterr().err


# --- corpus artifacts are unchanged by this PR ----------------------------


def test_sidecar_bytes_are_unchanged_by_taking_dim_from_the_embedder(
    tmp_path: Path,
) -> None:
    """The write path now stamps ``embedder.get_dimension()`` into the header
    instead of a module constant that happened to hold the same number. For
    the production configuration those are the same 3072, so no published
    ``.bin`` changes — which is what makes this safe to ship without touching
    the corpus."""
    from lovspor.embeddings.store import EMBEDDING_DIM  # noqa: PLC0415

    vectors = [("1-1", np.ones(EMBEDDING_DIM, dtype=np.int8))]
    implicit = tmp_path / "implicit.bin"
    explicit = tmp_path / "explicit.bin"

    write_embeddings(implicit, vectors, 0.01)
    write_embeddings(explicit, vectors, 0.01, dim=_FakeEmbedder(dim=EMBEDDING_DIM).get_dimension())

    assert implicit.read_bytes() == explicit.read_bytes()


def test_identity_lives_in_the_manifest_and_not_in_the_sidecar(tmp_path: Path) -> None:
    """The Stage 1 boundary: manifest carries identity, the binary does not.

    ADR-0005 stages this deliberately. Putting the digest in the sidecar means
    rewriting every published file, so it waits for a coordinated cutover;
    until then the manifest is the only place identity exists.
    """
    _seed_minimal_corpus(tmp_path, dim=4)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    record = next(iter(manifest["documents"].values()))

    assert record["embedding_space"] == LEGACY_SPACE_DESCRIPTOR
    assert record["embedding_space_id"] == LEGACY_SPACE_ID

    sidecar = (tmp_path / "lover" / "embeddings" / "testloven.bin").read_bytes()
    assert sidecar[:4] == b"LSPE"
    assert sidecar[4] == 1
    # The spare header byte stays spare: no digest, no Stage 2 smuggled in.
    assert sidecar[5] == 0
