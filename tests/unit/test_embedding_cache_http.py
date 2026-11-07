"""Design #802 §6: checkpoints preserve the real adapter's HTTP requests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.cache_format import ReadPolicy
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.model import OpenAIEmbedder


@pytest.mark.parametrize(("batch_size", "token_budget"), [(2, 100), (128, 8)])
@pytest.mark.parametrize("failure", ["503", "400", "timeout"])
def test_real_adapter_resume_preserves_requests_and_quantized_vectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    batch_size: int,
    token_budget: int,
    failure: str,
) -> None:
    """Both batching limits act after truncation; completed HTTP work is reused."""
    monkeypatch.setattr("lovspor.embeddings.model._MAX_INPUT_TOKENS", 4)
    monkeypatch.setattr("lovspor.embeddings.model._MAX_REQUEST_TOKENS", token_budget)
    requests: list[list[str]] = []
    fail_second = False

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batch = body["input"]
        requests.append(batch)
        assert body["dimensions"] == 3
        if fail_second and len(requests) == 2:
            if failure == "timeout":
                raise httpx.ReadTimeout("response interrupted", request=request)
            return httpx.Response(int(failure))
        # Batch-dependent, unnormalised and out of order: exercise the real
        # adapter's alignment and normalisation before checkpointing float32.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [len(batch), i + 1, ord(text[1])]}
                    for i, text in reversed(list(enumerate(batch)))
                ],
            },
        )

    httpx_mock.add_callback(respond, is_reusable=True)

    def provider() -> OpenAIEmbedder:
        return OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=batch_size, max_retries=1)

    inputs = [EmbeddingInput(section_id=str(i), text=f" {c}" * 12) for i, c in enumerate("abc")]
    input_hash = hash_embedding_inputs(inputs)
    expected_vectors, expected_scale = quantized_document_vectors(provider(), inputs, input_hash)
    expected_requests = [[" a" * 4, " b" * 4], [" c" * 4]]
    assert requests == expected_requests
    requests.clear()
    fail_second = True
    root = tmp_path / "cache"
    policy = ReadPolicy(max_age=timedelta(days=30), clock=lambda: datetime.now(UTC))

    def cached() -> CachingEmbedder:
        inner = provider()
        return CachingEmbedder(inner, LocalEntryStore.open(root, (), policy), inner.space_id)

    expected_error = httpx.ReadTimeout if failure == "timeout" else httpx.HTTPStatusError
    with pytest.raises(expected_error):
        quantized_document_vectors(cached(), inputs, input_hash)
    assert requests == expected_requests
    checkpoints = {path: path.read_bytes() for path in root.rglob("*.lsec")}
    assert len(checkpoints) == 1
    assert all("units" in path.parts for path in checkpoints)

    fail_second = False
    resumed = cached()
    vectors, scale = quantized_document_vectors(resumed, inputs, input_hash)
    assert requests == [*expected_requests, expected_requests[1]]
    assert resumed.progress.units_cached == 1
    assert resumed.progress.units_paid == 1
    assert {path: path.read_bytes() for path in checkpoints} == checkpoints
    np.testing.assert_array_equal(vectors, expected_vectors)
    assert scale == expected_scale

    hit_vectors, hit_scale = quantized_document_vectors(cached(), inputs, input_hash)
    assert requests == [*expected_requests, expected_requests[1]]
    np.testing.assert_array_equal(hit_vectors, expected_vectors)
    assert hit_scale == expected_scale
