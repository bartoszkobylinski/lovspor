"""The provider request plan is one function shared by the embedder and the cache (#802).

A cached run must send exactly the requests an uncached run sends: batch
composition moves provider vectors (ADR-0005 Amendment 1), so a cache unit
that straddled two requests would store vectors no uncached run produces.
"""

import httpx
import pytest

from lovspor.embeddings.model import OpenAIEmbedder, request_bounds


def test_bounds_cover_every_item_once_in_order() -> None:
    bounds = request_bounds([1] * 300, batch_size=128)
    assert bounds == [(0, 128), (128, 256), (256, 300)]


def test_token_budget_splits_before_the_item_that_would_exceed_it() -> None:
    assert request_bounds([150_000, 150_000, 10], batch_size=128) == [(0, 1), (1, 3)]


def test_budget_exactly_reached_stays_in_one_request() -> None:
    assert request_bounds([125_000, 125_000], batch_size=128) == [(0, 2)]


def test_single_oversized_item_still_gets_a_request() -> None:
    assert request_bounds([400_000], batch_size=128) == [(0, 1)]


def test_no_items_no_requests() -> None:
    assert request_bounds([], batch_size=128) == []


def test_token_and_item_limits_reset_after_each_request() -> None:
    """Both limits apply afresh after either one ends a request (#802)."""
    costs = [100_000, 100_000, 60_000, 60_000, 60_000, 60_000, 60_000]
    bounds = request_bounds(costs, batch_size=3)
    assert bounds == [(0, 2), (2, 5), (5, 7)]
    assert [index for start, end in bounds for index in range(start, end)] == list(
        range(len(costs))
    )
    for start, end in bounds:
        assert 0 < end - start <= 3
        assert sum(costs[start:end]) <= 250_000


def test_zero_token_inputs_still_count_toward_request_item_limit() -> None:
    """The item cap is independent of token cost, including empty strings."""
    assert request_bounds([0] * 7, batch_size=3) == [(0, 3), (3, 6), (6, 7)]


@pytest.mark.parametrize("size", [1, 2, 5])
def test_embedder_plan_matches_its_own_batches(size: int) -> None:
    model = OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=size)
    texts = [f"tekst {i}" for i in range(11)]
    planned = [texts[start:end] for start, end in model.plan_requests(texts)]
    assert planned == list(model._batches(texts))


def test_plan_applies_input_truncation_before_the_request_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_requests promises the requests encode sends, after truncation."""
    monkeypatch.setattr("lovspor.embeddings.model._MAX_INPUT_TOKENS", 4)
    monkeypatch.setattr("lovspor.embeddings.model._MAX_REQUEST_TOKENS", 8)
    model = OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=128)
    texts = [" a" * 12, " b" * 12, " c" * 12]
    truncated = [model._truncate_to_tokens(text) for text in texts]
    assert truncated == [" a" * 4, " b" * 4, " c" * 4]
    bounds = model.plan_requests(texts)
    assert bounds == [(0, 2), (2, 3)]
    assert [truncated[start:end] for start, end in bounds] == list(model._batches(truncated))


def test_plan_matches_sent_requests_after_truncation_and_token_splitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = OpenAIEmbedder(api_key="sk-test", dim=3, batch_size=128)
    # 32 capped inputs exceed the 250,000-token request ceiling, even
    # though their item count fits; the last input exercises the tail.
    texts = [" ord" * 8100] * 32 + ["slutt"]
    sent: list[list[str]] = []

    def record(_client: httpx.Client, batch: list[str]) -> list[list[float]]:
        sent.append(batch)
        return [[1.0, 0.0, 0.0] for _ in batch]

    monkeypatch.setattr(model, "_encode_batch", record)
    bounds = model.plan_requests(texts)
    vectors = model.encode(texts)
    truncated = model._truncate_to_tokens(texts[0])
    assert truncated != texts[0]
    assert bounds == [(0, 31), (31, 33)]
    assert sent == [[truncated] * 31, [truncated, "slutt"]]
    assert vectors.shape == (33, 3)
