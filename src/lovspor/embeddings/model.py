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

from collections.abc import Iterator
from functools import lru_cache
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

_RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_API_MESSAGE_SAMPLE = 500
"""How much of an error response body to carry into the exception.

``raise_for_status`` reports the status line and nothing else, so the
2026-07-27 sync failure surfaced as a bare "400 Bad Request" and the
cause had to be reconstructed from the corpus. The body says which limit
was hit, in one sentence. Truncated because an API can answer with an
HTML page, and no secret travels in a response body — the key is only
ever sent in a request header."""

_MAX_REQUEST_TOKENS = 250_000
"""Token budget for ONE request, summed across its inputs.

The endpoint rejects a request whose inputs total more than 300,000
tokens with a 400 — separately from, and regardless of, the 8,191
per-input limit. Batching by item count alone cannot honour that:
128 inputs of 8,000 tokens is 1,024,000, and two of the corpus's
annexes (kosmetikkforskriften, tilsetningsstofforskriften) really do
reach 206 and 141 chunks, so the first request carried 583,824
tokens and the whole sync died on it (2026-07-27).

The 50,000-token margin under the documented cap absorbs the known
disagreement between client-side tiktoken counts and the server's
own accounting; the price of being wrong is a hard failure mid-sync,
so the margin is deliberately generous."""

_DEFAULT_TIMEOUT_SECONDS = 180.0
_DEFAULT_MAX_RETRIES = 3


@lru_cache(maxsize=4)
def _encoding_for(model_name: str) -> tiktoken.Encoding:
    """Cached tiktoken encoding lookup — ``encoding_for_model`` walks a
    registry on every call, and the chunker calls it per section."""
    return tiktoken.encoding_for_model(model_name)


def split_to_token_chunks(
    text: str,
    max_tokens: int = _MAX_INPUT_TOKENS,
    model_name: str = DEFAULT_MODEL_NAME,
) -> list[str]:
    """Split ``text`` into consecutive chunks of at most ``max_tokens``.

    The anti-truncation companion to ``OpenAIEmbedder``'s input
    limit: a section longer than the model's window used to be
    silently cut at ``_MAX_INPUT_TOKENS``, making its tail invisible
    to semantic search. Callers embed every chunk under the same
    section_id instead, so the whole section stays searchable.

    Chunks are split at token boundaries, but a boundary that lands
    inside a multi-token character (an emoji, for example) backs up
    to the nearest character edge — naive per-token slicing decoded
    such boundaries to U+FFFD replacement characters and corrupted
    the text (Codex PR #66 round 1). Chunks therefore always decode
    cleanly and concatenate back to the original. The single
    degenerate exception: a character whose own token count exceeds
    ``max_tokens`` becomes one chunk wider than the cap (only
    reachable with absurdly small ``max_tokens``; the production cap
    is 8000). No overlap between chunks: boundary-straddling phrases
    may embed weaker, accepted as the simple-first trade-off
    (documented in docs/embeddings.md).

    Text at or under the limit (including empty text) returns a
    single chunk, preserving the one-vector-per-section common case.
    """
    encoding = _encoding_for(model_name)
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = _character_safe_cut(encoding, tokens, start, min(start + max_tokens, len(tokens)))
        chunks.append(encoding.decode_bytes(tokens[start:end]).decode("utf-8"))
        start = end
    return chunks


def _character_safe_cut(
    encoding: tiktoken.Encoding,
    tokens: list[int],
    start: int,
    end: int,
) -> int:
    """Largest cut in ``(start, end]`` where ``tokens[start:cut]``
    decodes to complete UTF-8.

    ``start`` is always a character edge (guaranteed inductively: the
    previous cut was). When even ``start + 1`` is mid-character, the
    cut extends FORWARD past ``end`` until the character completes —
    the degenerate single-wide-character case documented on
    ``split_to_token_chunks``. Terminates because the full token
    sequence decodes to the original valid string.
    """
    cut = end
    while cut > start + 1 and not _is_complete_utf8(encoding.decode_bytes(tokens[start:cut])):
        cut -= 1
    while not _is_complete_utf8(encoding.decode_bytes(tokens[start:cut])):
        cut += 1
    return cut


def _is_complete_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


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
            for batch in self._batches(truncated):
                out.extend(self._encode_batch(client, batch))
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(arr / norms, dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        """Split ``texts`` into requests bounded by BOTH limits.

        Item count alone is not enough: the endpoint also caps the tokens
        summed over a request's inputs, and rejects the excess with a 400
        that no retry can fix. Each input is already truncated to
        ``_MAX_INPUT_TOKENS``, which is far below the budget, so a single
        input is never unsendable and the loop always makes progress.
        """
        batch: list[str] = []
        tokens = 0
        for text in texts:
            cost = len(self._encoding.encode(text))
            over_budget = batch and tokens + cost > _MAX_REQUEST_TOKENS
            if len(batch) == self._batch_size or over_budget:
                yield batch
                batch, tokens = [], 0
            batch.append(text)
            tokens += cost
        if batch:
            yield batch

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
                # the operator should see immediately) and are re-raised
                # carrying the API's own explanation.
                if exc.response.status_code not in _RETRIABLE_STATUS:
                    raise _with_api_message(exc) from exc
                if attempt == self._max_retries:
                    raise _with_api_message(exc) from exc
                wait = 2**attempt
                _log_retry(
                    f"status {exc.response.status_code} "
                    f"(attempt {attempt}/{self._max_retries}); backoff {wait}s",
                    exc,
                )
                _sleep(wait)
                continue
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


def _with_api_message(exc: httpx.HTTPStatusError) -> httpx.HTTPStatusError:
    """Return the same error with the API's explanation appended.

    ``raise_for_status`` builds its message from the status line alone, so
    an operator reading the traceback learns that the call failed and
    nothing about why. The body carries the reason — which limit, which
    field — and without it a failure costs a corpus-wide investigation to
    reconstruct.
    """
    try:
        body = exc.response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return exc
    if not body.strip():
        return exc
    detail = body.strip()[:_API_MESSAGE_SAMPLE]
    return httpx.HTTPStatusError(
        f"{exc}\nAPI said: {detail}",
        request=exc.request,
        response=exc.response,
    )


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
