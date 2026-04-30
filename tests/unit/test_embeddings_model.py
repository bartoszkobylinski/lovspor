import importlib
import sys
import types
from typing import ClassVar

import numpy as np
import pytest

from lovspor.embeddings.model import EmbeddingModel, JinaModel, get_default_model, set_model


class FakeModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 2), dtype=np.float32)

    def get_dimension(self) -> int:
        return 2


class FakeSentenceTransformer:
    instances: ClassVar[list["FakeSentenceTransformer"]] = []
    dimension: ClassVar[int | None] = 2

    def __init__(self, model_name: str, *, trust_remote_code: bool, revision: str) -> None:
        self.model_name = model_name
        self.trust_remote_code = trust_remote_code
        self.revision = revision
        self.encoded_calls: list[tuple[list[str], dict[str, object]]] = []
        FakeSentenceTransformer.instances.append(self)

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.encoded_calls.append((texts, kwargs))
        return [[1.0, 2.0] for _text in texts]

    def get_sentence_embedding_dimension(self) -> int | None:
        return self.dimension


def _install_fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSentenceTransformer.instances.clear()
    FakeSentenceTransformer.dimension = 2
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.__dict__["SentenceTransformer"] = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)


def test_embeddings_modules_import_without_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    for module_name in [
        "lovspor.embeddings.quantize",
        "lovspor.embeddings.store",
        "lovspor.embeddings.search",
        "lovspor.embeddings.sections",
    ]:
        importlib.import_module(module_name)

    assert "sentence_transformers" not in sys.modules


def test_embedding_model_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeModel(), EmbeddingModel)


def test_jina_model_uses_norwegian_default_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sentence_transformers(monkeypatch)

    JinaModel(revision="abc123")

    assert FakeSentenceTransformer.instances[0].model_name == "jinaai/jina-embeddings-v2-base-no"


def test_jina_model_passes_revision_and_encode_options_to_sentence_transformer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sentence_transformers(monkeypatch)

    model = JinaModel(revision="abc123", model_name="fake-model")
    encoded = model.encode(["tekst"])

    fake = FakeSentenceTransformer.instances[0]
    assert fake.model_name == "fake-model"
    assert fake.trust_remote_code is True
    assert fake.revision == "abc123"
    assert fake.encoded_calls == [
        (
            ["tekst"],
            {
                "batch_size": 32,
                "show_progress_bar": False,
                "convert_to_numpy": True,
                "normalize_embeddings": True,
            },
        ),
    ]
    assert encoded.dtype == np.float32
    np.testing.assert_array_equal(encoded, np.array([[1.0, 2.0]], dtype=np.float32))
    np.testing.assert_array_equal(model.encode([]), np.empty((0, 2), dtype=np.float32))
    assert model.get_dimension() == 2


def test_jina_model_raises_when_backend_dimension_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    FakeSentenceTransformer.dimension = None
    model = JinaModel(revision="abc123")

    with pytest.raises(RuntimeError) as exc_info:
        model.get_dimension()

    assert str(exc_info.value) == "model does not expose embedding dimension"


def test_jina_model_requires_explicit_revision_before_importing_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    with pytest.raises(ValueError) as exc_info:
        JinaModel(revision="")

    assert str(exc_info.value) == (
        "JinaModel requires an explicit Hugging Face revision "
        "(commit SHA preferred) to bound trust_remote_code "
        "supply-chain risk; pass 'main' explicitly to opt out"
    )
    assert "sentence_transformers" not in sys.modules


def test_default_model_lazy_loads_once_with_first_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    set_model(None)

    try:
        first = get_default_model(revision="first-revision")
        second = get_default_model(revision="second-revision")
    finally:
        set_model(None)

    assert first is second
    assert len(FakeSentenceTransformer.instances) == 1
    assert FakeSentenceTransformer.instances[0].revision == "first-revision"


def test_default_model_uses_test_override_without_loading_jina() -> None:
    fake = FakeModel()
    set_model(fake)

    try:
        assert get_default_model(revision="sentinel") is fake
    finally:
        set_model(None)
