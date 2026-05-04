"""OpenAI Embeddings client with token-aware truncation and resilient batching.

Replaces the earlier sentence-transformers-based ``JinaModel`` after
the Sprint 9 PR-A → PR-B benchmark showed ``text-embedding-3-large``
beating ``nb-sbert-v2-large`` by **+24% Recall@5** on the lovverk
corpus over 47 realistic queries
(``benchmarks/embedding_comparison/results-2026-04-30.md``).

The trade-off (paid API, network dependency) was accepted because:

- $5-15/year ongoing cost is trivial for a solo project
- Norwegian law text is publicly available, so privacy is not a concern
- OpenAI API has been more stable than the supply-chain risk of
  ``trust_remote_code=True`` model loading

Why tiktoken-aware truncation: ``text-embedding-3`` has a hard 8191
token input limit. Char-based heuristics (the path used by the
benchmark) hit the limit on enumerative sections — EU regulation
lists, chemical substance tables — where Norwegian compound words
pack ~1.5 chars/token instead of the average ~3.6. ``tiktoken``
truncates by exact token count, eliminating the recursive
split-on-failure path entirely.

Why sync httpx instead of openai-python: keeps the dependency tree
small (the engine and MCP server only need httpx, which is already a
core dep for the Lovdata client). The ``openai`` package pulls in
its own dependency cluster we don't otherwise use.
"""

from typing import Any, Protocol, runtime_checkable

import httpx
import numpy as np
import tiktoken

DEFAULT_MODEL_NAME = "text-embedding-3-large"
DEFAULT_DIMENSION = 3072
"""Native dimensionality of text-embedding-3-large.

The OpenAI API supports dimensionality reduction via the
``dimensions`` parameter, but this engine uses native dim because
storage savings (~33% for 1024-dim, ~92% for 256-dim) come at a
~1-3% Recall@5 cost on benchmarks. The 200 MB int8 footprint at
3072 dim is acceptable for the lovverk corpus.
"""

_ENDPOINT = "https://api.openai.com/v1/embeddings"
_MAX_INPUT_TOKENS = 8000
"""text-embedding-3 hard limit is 8191; leave a 191-token margin
to absorb tokenizer drift between client-side tiktoken and
server-side encoding (they should agree exactly, but defense in
depth is cheap)."""

_DEFAULT_BATCH_SIZE = 128
"""Smaller batch keeps response payload manageable for 3072-dim
output. The benchmark proved 256 was too aggressive for 3-large
on slow OpenAI nodes (60 s timeouts). 128 trades batch count for
per-request reliability."""

_DEFAULT_TIMEOUT_SECONDS = 180.0
_DEFAULT_MAX_RETRIES = 3


@runtime_checkable
class EmbeddingModel(Protocol):
    """Minimal text-to-vector contract used by the engine and MCP server."""

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return one row per input text. Output rows are L2-normalized."""

    def get_dimension(self) -> int:
        """Output dimensionality (3072 for text-embedding-3-large)."""


class OpenAIEmbedder:
    """text-embedding-3-large client. Pre-truncates input by tokens.

    Each call to ``encode`` opens a single short-lived ``httpx.Client``
    so we don't hold sockets open between syncs. For the MCP server
    which calls ``encode`` repeatedly, the per-call overhead is
    ~1 ms — well below the OpenAI round-trip latency that dominates.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_MODEL_NAME,
        dim: int = DEFAULT_DIMENSION,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY in the "
                "environment or pass api_key explicitly.",
            )
        self._api_key = api_key
        self._model_name = model_name
        self._dim = dim
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._encoding = tiktoken.encoding_for_model(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        truncated = [self._truncate_to_tokens(t) for t in texts]
        out: list[list[float]] = []
        with httpx.Client(timeout=self._timeout_seconds) as client:
            for i in range(0, len(truncated), self._batch_size):
                batch = truncated[i : i + self._batch_size]
                out.extend(self._encode_batch(client, batch))
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(arr / norms, dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim

    def _truncate_to_tokens(self, text: str) -> str:
        """Encode, truncate to ``_MAX_INPUT_TOKENS``, decode back.

        Decoding a truncated token sequence yields valid UTF-8 text
        because BPE tokens are full byte sequences. This is the
        canonical approach OpenAI documents for handling the input
        token limit.
        """
        tokens = self._encoding.encode(text)
        if len(tokens) <= _MAX_INPUT_TOKENS:
            return text
        return self._encoding.decode(tokens[:_MAX_INPUT_TOKENS])

    def _encode_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        """Send a batch with retry on transient errors. Token truncation
        upstream means we should not see 400s from the input limit."""
        payload: dict[str, Any] = {
            "model": self._model_name,
            "input": batch,
            # Always send dimensions so the API result matches what
            # get_dimension() promises. Without this, OpenAI returns
            # the model's native dimensionality regardless of self._dim,
            # silently misaligning with downstream storage.
            "dimensions": self._dim,
        }
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = client.post(
                    _ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.TransportError as exc:
                # Covers both TimeoutException and NetworkError
                # (ConnectError, ReadError, WriteError, etc.). All
                # transport-level errors deserve the same retry
                # treatment; the previous code only caught timeouts
                # and let DNS / connection refused / broken-pipe
                # bubble up immediately.
                if attempt == self._max_retries:
                    raise
                wait = 2**attempt
                _log_retry(
                    f"{type(exc).__name__} "
                    f"(attempt {attempt}/{self._max_retries}); backoff {wait}s",
                    exc,
                )
                _sleep(wait)
                continue
            except httpx.HTTPStatusError as exc:
                # Rate limits + server errors are retriable; 4xx other
                # than 429 are not (likely a config or contract bug
                # the operator should see immediately).
                if exc.response.status_code in (429, 500, 502, 503, 504):
                    if attempt == self._max_retries:
                        raise
                    wait = 2**attempt
                    _log_retry(
                        f"status {exc.response.status_code} "
                        f"(attempt {attempt}/{self._max_retries}); backoff {wait}s",
                        exc,
                    )
                    _sleep(wait)
                    continue
                raise
            else:
                return self._extract_aligned_embeddings(resp.json(), len(batch))
        # Unreachable: every loop iteration either returns, raises,
        # or continues; the final iteration raises on attempt == max.
        raise RuntimeError("retry loop exited without returning")  # pragma: no cover

    def _extract_aligned_embeddings(
        self,
        body: dict[str, Any],
        expected_count: int,
    ) -> list[list[float]]:
        """Return embeddings indexed by the API's ``index`` field, not by
        list position. Validates that every input got exactly one
        response — silently dropping or duplicating an embedding would
        misalign sections to vectors at storage time, which is hard to
        detect later."""
        entries = body["data"]
        if len(entries) != expected_count:
            raise RuntimeError(
                f"OpenAI returned {len(entries)} embeddings for {expected_count} "
                f"inputs; input/output count mismatch indicates an API contract "
                f"violation",
            )
        result: list[list[float] | None] = [None] * expected_count
        for entry in entries:
            idx = entry["index"]
            if not 0 <= idx < expected_count:
                raise RuntimeError(
                    f"OpenAI returned out-of-range index {idx} for batch of {expected_count}",
                )
            if result[idx] is not None:
                raise RuntimeError(f"OpenAI returned duplicate embedding for index {idx}")
            result[idx] = entry["embedding"]
        if any(v is None for v in result):
            missing = [i for i, v in enumerate(result) if v is None]
            raise RuntimeError(f"OpenAI did not return embeddings for indices {missing}")
        # mypy can't infer 'no None' from the runtime check above; cast.
        return [v for v in result if v is not None]


def _log_retry(message: str, exc: Exception) -> None:
    """Single-line retry log so operators see backoff progress without
    drowning in noise. Goes to stderr via print() — keeps lovspor's
    dependency on a logging framework optional."""
    import sys  # noqa: PLC0415 — keep stdlib import local to the helper

    print(f"openai-embed: {message} ({type(exc).__name__})", file=sys.stderr, flush=True)


def _sleep(seconds: float) -> None:
    """Indirection so tests can monkey-patch sleep without hitting
    the real ``time.sleep`` and stalling the test suite."""
    import time  # noqa: PLC0415

    time.sleep(seconds)
