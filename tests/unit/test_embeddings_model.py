import json
import os
from typing import ClassVar

import httpx
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pytest_httpx import HTTPXMock

import lovspor.embeddings.model as model_module
from lovspor.embeddings.model import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL_NAME,
    EmbeddingModel,
    OpenAIEmbedder,
)

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
MAX_INPUT_TOKENS = 8000


class FakeModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 2), dtype=np.float32)

    def get_dimension(self) -> int:
        return 2


class FakeEncoding:
    encode_calls: ClassVar[list[str]] = []

    def encode(self, text: str) -> list[int]:
        self.encode_calls.append(text)
        return list(range(len(text)))

    def decode(self, tokens: list[int]) -> str:
        return f"decoded:{len(tokens)}:{tokens[-1]}"


def _embedding_response(embeddings: list[list[float]]) -> dict[str, object]:
    return _indexed_embedding_response(
        [(index, embedding) for index, embedding in enumerate(embeddings)],
    )


def _indexed_embedding_response(
    indexed_embeddings: list[tuple[int, list[float]]],
) -> dict[str, object]:
    return {
        "data": [
            {"object": "embedding", "embedding": embedding, "index": index}
            for index, embedding in indexed_embeddings
        ],
        "model": DEFAULT_MODEL_NAME,
        "object": "list",
    }


def _extractor() -> OpenAIEmbedder:
    return object.__new__(OpenAIEmbedder)


def test_embedding_model_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeModel(), EmbeddingModel)


def test_openai_embedder_requires_api_key_before_loading_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(_model_name: str) -> FakeEncoding:
        raise AssertionError("tokenizer should not be loaded for invalid config")

    monkeypatch.setattr(model_module.tiktoken, "encoding_for_model", fail_if_called)

    with pytest.raises(ValueError) as exc_info:
        OpenAIEmbedder(api_key="")
    assert str(exc_info.value) == (
        "OpenAI API key is required. Set OPENAI_API_KEY in the "
        "environment or pass api_key explicitly."
    )


def test_openai_embedder_default_config_matches_openai_contract() -> None:
    model = OpenAIEmbedder(api_key="sk-test")

    assert DEFAULT_MODEL_NAME == "text-embedding-3-large"
    assert DEFAULT_DIMENSION == 3072
    assert model.get_dimension() == 3072
    assert model._batch_size == 128
    assert model._timeout_seconds == 180.0
    assert model._max_retries == 3


def test_openai_embedder_reports_custom_dimension() -> None:
    assert OpenAIEmbedder(api_key="sk-test").get_dimension() == DEFAULT_DIMENSION
    assert OpenAIEmbedder(api_key="sk-test", dim=3).get_dimension() == 3


def test_encode_empty_input_returns_typed_empty_array(httpx_mock: HTTPXMock) -> None:
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    encoded = model.encode([])

    assert encoded.shape == (0, 3)
    assert encoded.dtype == np.float32
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("scenario", ["success", "transport_error", "http_status"])
def test_encode_sends_dimensions_on_success_and_retry_attempts(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    monkeypatch.setattr(model_module, "_sleep", lambda _seconds: None)
    if scenario == "transport_error":
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    elif scenario == "http_status":
        httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=503)
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    encoded = model.encode(["tekst"])

    np.testing.assert_array_equal(encoded, np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    expected_request_count = 1 if scenario == "success" else 2
    assert len(httpx_mock.get_requests()) == expected_request_count
    for request in httpx_mock.get_requests():
        assert json.loads(request.content)["dimensions"] == 3


def test_encode_posts_batches_and_l2_normalizes_response(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]),
    )
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[0.0, -5.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=2)

    encoded = model.encode(["first", "second", "third"])

    assert encoded.dtype == np.float32
    np.testing.assert_allclose(
        encoded,
        np.array(
            [
                [0.6, 0.8, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, -1.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )
    first_request, second_request = httpx_mock.get_requests()
    assert first_request.headers["Authorization"] == "Bearer sk-test"
    assert first_request.headers["Content-Type"] == "application/json"
    assert json.loads(first_request.content) == {
        "model": DEFAULT_MODEL_NAME,
        "input": ["first", "second"],
        "dimensions": 3,
    }
    assert json.loads(second_request.content) == {
        "model": DEFAULT_MODEL_NAME,
        "input": ["third"],
        "dimensions": 3,
    }


def test_encode_aligns_out_of_order_response_indices(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_indexed_embedding_response(
            [
                (1, [0.0, 2.0, 0.0]),
                (0, [3.0, 0.0, 0.0]),
                (2, [0.0, 0.0, 4.0]),
            ],
        ),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    encoded = model.encode(["first", "second", "third"])

    np.testing.assert_allclose(
        encoded,
        np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        ),
    )


def test_extract_aligned_embeddings_rejects_count_mismatch() -> None:
    with pytest.raises(RuntimeError, match="count mismatch"):
        _extractor()._extract_aligned_embeddings(
            _indexed_embedding_response([(0, [1.0, 0.0])]),
            expected_count=2,
        )


@pytest.mark.parametrize("bad_index", [-1, 2])
def test_extract_aligned_embeddings_rejects_out_of_range_index(bad_index: int) -> None:
    with pytest.raises(RuntimeError, match=f"out-of-range index {bad_index}"):
        _extractor()._extract_aligned_embeddings(
            _indexed_embedding_response([(bad_index, [1.0, 0.0]), (1, [0.0, 1.0])]),
            expected_count=2,
        )


def test_extract_aligned_embeddings_rejects_duplicate_index() -> None:
    with pytest.raises(RuntimeError, match="duplicate embedding for index 0"):
        _extractor()._extract_aligned_embeddings(
            _indexed_embedding_response([(0, [1.0, 0.0]), (0, [0.0, 1.0])]),
            expected_count=2,
        )


@pytest.mark.xfail(
    reason=(
        "Gap detection is unreachable for count-matched JSON responses with "
        "in-range integer indices because duplicates are rejected first."
    ),
    strict=True,
)
def test_extract_aligned_embeddings_rejects_gap_distinctly() -> None:
    with pytest.raises(RuntimeError, match="did not return embeddings for indices"):
        _extractor()._extract_aligned_embeddings(
            _indexed_embedding_response([(0, [1.0, 0.0]), (0, [0.0, 1.0])]),
            expected_count=2,
        )


@given(count=st.integers(min_value=1, max_value=8), data=st.data())
def test_extract_aligned_embeddings_reconstructs_any_valid_permutation(
    count: int,
    data: st.DataObject,
) -> None:
    embeddings = [[float(index), float(index + 100)] for index in range(count)]
    order = data.draw(st.permutations(list(range(count))))
    body = _indexed_embedding_response([(index, embeddings[index]) for index in order])

    assert _extractor()._extract_aligned_embeddings(body, count) == embeddings


@given(
    count=st.integers(min_value=2, max_value=8),
    mutation=st.sampled_from(["drop", "duplicate", "negative", "too_high"]),
)
def test_extract_aligned_embeddings_rejects_single_invalid_mutation(
    count: int,
    mutation: str,
) -> None:
    entries = [
        {"object": "embedding", "embedding": [float(index)], "index": index}
        for index in range(count)
    ]
    if mutation == "drop":
        entries.pop()
    elif mutation == "duplicate":
        entries[1]["index"] = 0
    elif mutation == "negative":
        entries[0]["index"] = -1
    else:
        entries[-1]["index"] = count

    with pytest.raises(RuntimeError):
        _extractor()._extract_aligned_embeddings({"data": entries}, count)


def test_truncate_to_tokens_returns_original_text_under_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_encoding = FakeEncoding()
    monkeypatch.setattr(model_module.tiktoken, "encoding_for_model", lambda _name: fake_encoding)
    model = OpenAIEmbedder(api_key="sk-test")

    assert model._truncate_to_tokens("abc") == "abc"
    assert fake_encoding.encode_calls == ["abc"]


def test_truncate_to_tokens_decodes_prefix_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_encoding = FakeEncoding()
    monkeypatch.setattr(model_module.tiktoken, "encoding_for_model", lambda _name: fake_encoding)
    model = OpenAIEmbedder(api_key="sk-test")
    long_text = "x" * (MAX_INPUT_TOKENS + 5)

    assert model_module._MAX_INPUT_TOKENS == MAX_INPUT_TOKENS
    assert (
        model._truncate_to_tokens(long_text) == f"decoded:{MAX_INPUT_TOKENS}:{MAX_INPUT_TOKENS - 1}"
    )


def test_truncate_to_tokens_keeps_text_at_exact_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_encoding = FakeEncoding()
    monkeypatch.setattr(model_module.tiktoken, "encoding_for_model", lambda _name: fake_encoding)
    model = OpenAIEmbedder(api_key="sk-test")
    exact_limit_text = "x" * MAX_INPUT_TOKENS

    assert model._truncate_to_tokens(exact_limit_text) == exact_limit_text


def test_encode_retries_timeout_then_succeeds(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    encoded = model.encode(["tekst"])

    np.testing.assert_array_equal(encoded, np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    assert sleeps == [2]
    assert len(httpx_mock.get_requests()) == 2


def test_encode_raises_after_final_timeout_without_extra_attempt(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    httpx_mock.add_exception(httpx.ReadTimeout("still slow"))
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    with pytest.raises(httpx.ReadTimeout):
        model.encode(["tekst"])

    assert sleeps == [2]
    assert len(httpx_mock.get_requests()) == 2


def test_encode_single_retry_timeout_does_not_make_extra_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OneTimeoutThenSuccessClient:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, _url: str, **_kwargs: object) -> httpx.Response:
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadTimeout("slow")
            request = httpx.Request("POST", OPENAI_EMBEDDINGS_URL)
            return httpx.Response(200, json=_embedding_response([[1.0, 0.0, 0.0]]), request=request)

    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    client = OneTimeoutThenSuccessClient()
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=1)

    with pytest.raises(httpx.ReadTimeout):
        model._encode_batch(client, ["tekst"])  # type: ignore[arg-type]

    assert sleeps == []
    assert client.calls == 1


def test_encode_uses_exponential_backoff_for_repeated_timeouts(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    httpx_mock.add_exception(httpx.ReadTimeout("still slow"))
    httpx_mock.add_exception(httpx.ReadTimeout("slower"))
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=4)

    encoded = model.encode(["tekst"])

    np.testing.assert_array_equal(encoded, np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    assert sleeps == [2, 4, 8]
    assert len(httpx_mock.get_requests()) == 4
    assert capsys.readouterr().err.splitlines() == [
        "openai-embed: ReadTimeout (attempt 1/4); backoff 2s (ReadTimeout)",
        "openai-embed: ReadTimeout (attempt 2/4); backoff 4s (ReadTimeout)",
        "openai-embed: ReadTimeout (attempt 3/4); backoff 8s (ReadTimeout)",
    ]


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ConnectError("connect failed"),
        httpx.ReadError("read failed"),
        httpx.WriteError("write failed"),
        httpx.PoolTimeout("pool exhausted"),
    ],
)
def test_encode_retries_transport_error_subclasses(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception: httpx.TransportError,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_exception(exception)
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    encoded = model.encode(["tekst"])

    exception_name = type(exception).__name__
    np.testing.assert_array_equal(encoded, np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    assert sleeps == [2]
    assert capsys.readouterr().err.splitlines() == [
        f"openai-embed: {exception_name} (attempt 1/2); backoff 2s ({exception_name})",
    ]
    assert len(httpx_mock.get_requests()) == 2
    for request in httpx_mock.get_requests():
        assert json.loads(request.content)["dimensions"] == 3


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_encode_retries_transient_status_then_succeeds(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=status_code)
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[0.0, 1.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    encoded = model.encode(["tekst"])

    np.testing.assert_array_equal(encoded, np.array([[0.0, 1.0, 0.0]], dtype=np.float32))
    assert sleeps == [2]
    assert len(httpx_mock.get_requests()) == 2


def test_encode_uses_exponential_backoff_for_repeated_transient_status(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=500)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=502)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=503)
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[0.0, 1.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=4)

    encoded = model.encode(["tekst"])

    np.testing.assert_array_equal(encoded, np.array([[0.0, 1.0, 0.0]], dtype=np.float32))
    assert sleeps == [2, 4, 8]
    assert len(httpx_mock.get_requests()) == 4
    assert capsys.readouterr().err.splitlines() == [
        "openai-embed: status 500 (attempt 1/4); backoff 2s (HTTPStatusError)",
        "openai-embed: status 502 (attempt 2/4); backoff 4s (HTTPStatusError)",
        "openai-embed: status 503 (attempt 3/4); backoff 8s (HTTPStatusError)",
    ]


def test_encode_raises_after_final_retriable_status_without_extra_attempt(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=503)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=503)
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=2)

    with pytest.raises(httpx.HTTPStatusError):
        model.encode(["tekst"])

    assert sleeps == [2]
    assert len(httpx_mock.get_requests()) == 2


def test_encode_does_not_retry_non_retriable_client_error(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(model_module, "_sleep", sleeps.append)
    httpx_mock.add_response(method="POST", url=OPENAI_EMBEDDINGS_URL, status_code=400)
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=3)

    with pytest.raises(httpx.HTTPStatusError):
        model.encode(["tekst"])

    assert sleeps == []
    assert len(httpx_mock.get_requests()) == 1


def test_sleep_accepts_zero_second_delay() -> None:
    model_module._sleep(0)


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is required for the real OpenAI dimensions smoke test",
)
def test_real_openai_dimensions_parameter_changes_vector_length() -> None:
    model = OpenAIEmbedder(
        api_key=os.environ["OPENAI_API_KEY"],
        dim=256,
        batch_size=1,
        max_retries=1,
    )

    encoded = model.encode(["lovspor dimensions parameter smoke test"])

    assert encoded.shape == (1, 256)


# ---------- split_to_token_chunks ----------


def test_split_to_token_chunks_returns_single_chunk_under_limit() -> None:
    text = "Kort paragraf om skatteplikt."
    assert model_module.split_to_token_chunks(text) == [text]


def test_split_to_token_chunks_splits_long_text_without_losing_content() -> None:
    text = " ".join(f"ord{i}" for i in range(600))

    chunks = model_module.split_to_token_chunks(text, max_tokens=100)

    assert len(chunks) > 1
    # BPE tokens are full byte sequences, so chunk boundaries are clean
    # text boundaries and the chunks reassemble the original exactly.
    assert "".join(chunks) == text
    encoding = model_module._encoding_for(DEFAULT_MODEL_NAME)
    for chunk in chunks:
        assert len(encoding.encode(chunk)) <= 100


def test_split_to_token_chunks_exact_limit_is_single_chunk() -> None:
    encoding = model_module._encoding_for(DEFAULT_MODEL_NAME)
    text = " ".join("ord" for _ in range(50))
    token_count = len(encoding.encode(text))

    assert model_module.split_to_token_chunks(text, max_tokens=token_count) == [text]


def test_split_to_token_chunks_empty_text_is_single_empty_chunk() -> None:
    assert model_module.split_to_token_chunks("") == [""]


def test_split_to_token_chunks_preserves_multitoken_unicode_characters() -> None:
    """Codex PR #66 round 1: an emoji (or any character that encodes
    to multiple BPE tokens) straddling a chunk boundary decoded to
    U+FFFD replacement characters, corrupting the embedded text and
    breaking the chunks-reassemble-to-original contract."""
    text = "😀" * 10

    chunks = model_module.split_to_token_chunks(text, max_tokens=1)

    assert "".join(chunks) == text
    assert all("�" not in chunk for chunk in chunks)


def test_split_to_token_chunks_boundary_backs_up_to_character_edge() -> None:
    """A boundary that would land mid-character moves back to the
    nearest character edge; chunks stay within max_tokens and still
    reassemble exactly."""
    encoding = model_module._encoding_for(DEFAULT_MODEL_NAME)
    text = ("ord " * 30) + ("😀" * 8) + (" mer norsk tekst æøå" * 5)

    chunks = model_module.split_to_token_chunks(text, max_tokens=20)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    for chunk in chunks:
        assert "�" not in chunk
        # A single character wider than max_tokens is the only allowed
        # overflow; none of these characters is, so the cap holds.
        assert len(encoding.encode(chunk)) <= 20


def _tokens_in(request_content: bytes) -> int:
    encoding = model_module._encoding_for(DEFAULT_MODEL_NAME)
    return sum(len(encoding.encode(text)) for text in json.loads(request_content)["input"])


def test_a_request_stays_under_the_endpoints_total_token_budget() -> None:
    """The 2026-07-27 sync failure. The endpoint caps the tokens SUMMED over
    a request's inputs, separately from the per-input limit, and answers 400
    when the sum is exceeded. Batching by item count could not honour that:
    kosmetikkforskriften renders to 206 chunks, and the first 128 of them
    carried 583,824 tokens.
    """
    chunk = "ord " * 2000  # ~2,000 tokens each; 128 of them blow the budget
    count = 200
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    batches = list(model._batches([chunk] * count))

    assert sum(len(batch) for batch in batches) == count
    for batch in batches:
        assert len(batch) <= model_module._DEFAULT_BATCH_SIZE
        tokens = sum(len(model._encoding.encode(text)) for text in batch)
        assert tokens <= model_module._MAX_REQUEST_TOKENS


def test_the_token_budget_never_starves_a_batch_of_its_only_input() -> None:
    """A single input can never exceed the budget — it is truncated to
    _MAX_INPUT_TOKENS first — but the guard must not spin on one anyway."""
    model = OpenAIEmbedder(api_key="sk-test", dim=3)
    huge = "ord " * 3000

    batches = list(model._batches([huge, huge, huge]))

    assert [len(batch) for batch in batches] == [3]


def test_batching_still_splits_on_item_count_when_the_budget_is_ample(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
    )
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json=_embedding_response([[1.0, 0.0, 0.0]]),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=2)

    model.encode(["a", "b", "c"])

    first, second = httpx_mock.get_requests()
    assert json.loads(first.content)["input"] == ["a", "b"]
    assert json.loads(second.content)["input"] == ["c"]
    assert _tokens_in(first.content) < model_module._MAX_REQUEST_TOKENS


def test_a_non_retriable_error_carries_the_apis_own_explanation(
    httpx_mock: HTTPXMock,
) -> None:
    """raise_for_status reports the status line only, so the sync failure
    surfaced as a bare 400 and the cause had to be reconstructed from the
    corpus. The body names the limit in one sentence."""
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=400,
        json={"error": {"message": "Requested 583824 tokens, max 300000 tokens per request"}},
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    with pytest.raises(httpx.HTTPStatusError, match="max 300000 tokens per request"):
        model.encode(["text"])


def test_an_exhausted_retry_also_carries_the_apis_explanation(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "_sleep", lambda _seconds: None)
    for _ in range(3):
        httpx_mock.add_response(
            method="POST",
            url=OPENAI_EMBEDDINGS_URL,
            status_code=503,
            json={"error": {"message": "upstream capacity"}},
        )
    model = OpenAIEmbedder(api_key="sk-test", dim=3, max_retries=3)

    with pytest.raises(httpx.HTTPStatusError, match="upstream capacity"):
        model.encode(["text"])


def test_an_empty_error_body_leaves_the_original_error_untouched(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=400,
        content=b"",
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    with pytest.raises(httpx.HTTPStatusError, match="400 Bad Request") as caught:
        model.encode(["text"])
    assert "API said" not in str(caught.value)


def test_the_budget_splits_exactly_at_the_boundary_not_one_input_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An input whose cost lands the request EXACTLY on the budget still
    belongs in it: the cap is a maximum, not a threshold to stay under. With
    a real 250,000-token budget the boundary is unreachable from a test, so
    the budget is shrunk to make the off-by-one observable.
    """
    monkeypatch.setattr(model_module, "_MAX_REQUEST_TOKENS", 10)
    model = OpenAIEmbedder(api_key="sk-test", dim=3)
    encoding = model_module._encoding_for(DEFAULT_MODEL_NAME)
    text = "ord ord ord ord ord"
    assert len(encoding.encode(text)) == 5, "fixture must cost exactly half the budget"

    batches = list(model._batches([text] * 4))

    # 5+5 fills the budget exactly, so two inputs per request, twice over.
    assert [len(batch) for batch in batches] == [2, 2]


def test_the_token_accumulator_resets_to_zero_between_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carrying anything over from the flushed request shrinks every later
    one — invisible until a document needs three requests or more."""
    monkeypatch.setattr(model_module, "_MAX_REQUEST_TOKENS", 10)
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    batches = list(model._batches(["ord ord ord ord ord"] * 6))

    assert [len(batch) for batch in batches] == [2, 2, 2]


def test_the_budget_keeps_a_margin_under_the_endpoints_documented_cap() -> None:
    """The endpoint rejects a request above 300,000 tokens. Client-side
    tiktoken and the server's accounting are known to disagree, and the cost
    of being wrong is a failed sync mid-run, so the gap is deliberate and
    pinned: shrinking it silently is what this test exists to catch."""
    documented_cap = 300_000

    assert documented_cap > model_module._MAX_REQUEST_TOKENS
    assert documented_cap - model_module._MAX_REQUEST_TOKENS == 50_000


def test_a_long_error_body_is_sampled_not_carried_whole(
    httpx_mock: HTTPXMock,
) -> None:
    """An API may answer with an HTML page. The reason belongs in the
    traceback; the page does not."""
    filler = "x" * 5000
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=400,
        content=f"limit exceeded {filler}".encode(),
    )
    model = OpenAIEmbedder(api_key="sk-test", dim=3)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        model.encode(["text"])

    message = str(caught.value)
    assert "limit exceeded" in message
    assert filler not in message
    assert message.count("x") <= model_module._API_MESSAGE_SAMPLE
