"""Progress and a per-run token budget at the paid embedding call (#790 Task 1).

Every corpus vector is paid for through ``encode`` on the sync's adapter, on
the change path and the backfill/repair path alike. :class:`MeteredEmbedder`
wraps that adapter for the whole sync process:

* **progress** — one stderr line every ``LOVSPOR_EMBEDDING_PROGRESS_EVERY``
  completed calls (default 25), and counts only: completed calls, inputs and
  the tiktoken estimate of their tokens. Never text, keys or paths, so a long
  run can be diagnosed from the log without leaking the corpus or a secret.
* **budget** — ``LOVSPOR_EMBEDDING_TOKEN_BUDGET`` (unset: no budget) caps the
  estimated tokens of the calls one ``run_sync`` completes, backfill and
  change loop together (one adapter per run). A call that would cross it is
  refused **before** it is sent, with :class:`EmbeddingBudgetError`.

What this counts, and what it does not. A *call* is one ``encode`` on this
adapter: with the durable cache on (#802) that is exactly one provider
request; with it off it is one document, which the provider adapter may send
as several requests. Counts move only when a call returns vectors. The HTTP
attempts inside a call are invisible here: a retried attempt (429, 5xx,
transport error) and a failed call may have been billed and are not counted.
So the budget is a ceiling on estimated tokens of completed calls, not a
monetary cap (#790 audit, S6; attempt accounting: #835). With the cache on,
what a stopped run paid for is kept and a rerun resumes; with it off, a
stopped run keeps nothing.
"""

import os
import sys
from dataclasses import dataclass

import numpy as np

from lovspor.embeddings.model import EmbeddingModel, count_input_tokens
from lovspor.embeddings.provider import DEFAULT_MODEL_NAME, UNKNOWN_SPACE_ID, space_id_of
from lovspor.errors import ConfigError, EmbeddingBudgetError

BUDGET_VARIABLE = "LOVSPOR_EMBEDDING_TOKEN_BUDGET"
PROGRESS_VARIABLE = "LOVSPOR_EMBEDDING_PROGRESS_EVERY"
DEFAULT_PROGRESS_EVERY = 25


@dataclass
class SpendMeter:
    """What this run's completed calls sent to the provider, as counts."""

    requests: int = 0
    inputs: int = 0
    tokens: int = 0

    def line(self, budget: int | None) -> str:
        limit = "none" if budget is None else str(budget)
        return (
            f"embedding-spend: completed_calls={self.requests} inputs={self.inputs} "
            f"estimated_tokens_completed={self.tokens} budget={limit}"
        )


class MeteredEmbedder:
    """The configured adapter, with progress and a token ceiling on ``encode``."""

    def __init__(self, inner: EmbeddingModel, budget: int | None, every: int) -> None:
        self._inner = inner
        self._budget = budget
        self._every = every
        self.meter = SpendMeter()

    def encode(self, texts: list[str]) -> np.ndarray:
        tokens = sum(count_input_tokens(text, self.model_name) for text in texts)
        self._refuse_over_budget(tokens)
        vectors = self._inner.encode(texts)
        self.meter.requests += 1
        self.meter.inputs += len(texts)
        self.meter.tokens += tokens
        if self.meter.requests % self._every == 0:
            print(self.meter.line(self._budget), file=sys.stderr, flush=True)
        return vectors

    def get_dimension(self) -> int:
        return self._inner.get_dimension()

    @property
    def space_id(self) -> str:
        return space_id_of(self._inner) or UNKNOWN_SPACE_ID

    @property
    def descriptor(self) -> str | None:
        descriptor = getattr(self._inner, "descriptor", None)
        return descriptor if isinstance(descriptor, str) else None

    @property
    def model_name(self) -> str:
        return str(getattr(self._inner, "model_name", DEFAULT_MODEL_NAME))

    def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
        plan = getattr(self._inner, "plan_requests", None)
        return plan(texts) if callable(plan) else [(0, len(texts))]

    def _refuse_over_budget(self, tokens: int) -> None:
        if self._budget is None or self.meter.tokens + tokens <= self._budget:
            return
        raise EmbeddingBudgetError(
            f"embedding token budget reached: {self.meter.tokens} estimated tokens in "
            f"completed calls, the next call needs {tokens}, budget {self._budget} "
            f"({BUDGET_VARIABLE}). It was not sent. The estimate excludes retried and "
            "failed attempts, which may also be billed. With LOVSPOR_EMBEDDING_CACHE_DIR "
            "set, a rerun resumes from what was paid; without it, this run's vectors are "
            "lost.",
        )


def with_meter(embedder: EmbeddingModel | None) -> EmbeddingModel | None:
    """Wrap the sync's adapter with progress and the optional budget."""
    if embedder is None:
        return None
    every = _positive_int(PROGRESS_VARIABLE) or DEFAULT_PROGRESS_EVERY
    return MeteredEmbedder(embedder, _positive_int(BUDGET_VARIABLE), every)


def _positive_int(variable: str) -> int | None:
    raw = os.environ.get(variable)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise ConfigError(f"{variable} must be a positive integer, got: {raw!r}")
    return value
