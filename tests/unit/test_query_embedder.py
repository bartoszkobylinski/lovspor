"""Unit tests for the capped, cached embedding of search queries.

This is the only request-time path that spends money, so both properties it adds
are financial as much as behavioural: the cap bounds what one call can cost, and
the cache stops the same question being paid for twice.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import pytest

from lovspor.embeddings import model as model_module
from lovspor.embeddings import query as query_module
from lovspor.embeddings.model import (
    DEFAULT_MODEL_NAME,
    OpenAIEmbedder,
    _encoding_for,
    count_input_tokens,
    truncate_to_tokens,
)
from lovspor.embeddings.query import (
    DEFAULT_CACHE_ENTRIES,
    DEFAULT_MAX_QUERY_TOKENS,
    QueryEmbedder,
    _InFlight,
)
from lovspor.errors import NetworkError


class _CountingEmbedder:
    """Records every text it was asked to embed, so a test can prove a call to
    OpenAI did not happen rather than merely that the answer looked right."""

    def __init__(self, dim: int = 4) -> None:
        self.calls: list[list[str]] = []
        self._dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim


class _BlockingEmbedder(_CountingEmbedder):
    """Keep the first request in flight while a duplicate enters the cache."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.duplicate_started = threading.Event()
        self.release = threading.Event()

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.started.is_set():
            self.duplicate_started.set()
        self.started.set()
        assert self.release.wait(timeout=5), "test did not release the embedding call"
        return super().encode(texts)


def test_a_new_in_flight_call_has_no_vector() -> None:
    assert _InFlight().vector is None


def test_a_short_query_is_passed_through_untouched() -> None:
    embedder = _CountingEmbedder()
    query = "hvilke rettigheter har jeg som leietaker"

    _, truncated = QueryEmbedder(embedder).encode(query)

    assert not truncated
    assert embedder.calls == [[query]]


def test_an_oversized_query_is_cut_before_it_is_paid_for() -> None:
    """The cap has to bite *before* the network call: truncating afterwards would
    bound nothing, because the tokens are billed on the way in."""
    embedder = _CountingEmbedder()
    query = "paragraf " * 4000

    _, truncated = QueryEmbedder(embedder, max_tokens=64).encode(query)

    assert truncated
    (sent,) = embedder.calls
    assert count_input_tokens(sent[0]) <= 64


def test_the_same_question_is_embedded_once() -> None:
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)

    first, _ = subject.encode("når trådte husleieloven i kraft")
    second, _ = subject.encode("når trådte husleieloven i kraft")

    assert len(embedder.calls) == 1
    assert np.array_equal(first, second)


def test_a_cache_hit_does_not_claim_single_flight_leadership() -> None:
    """A populated cache entry is a completed result, never a new paid call."""
    subject = QueryEmbedder(_CountingEmbedder())
    subject.encode("question")

    cached, _, leads = subject._claim("question")

    assert cached is not None
    assert not leads


def test_concurrent_duplicate_questions_are_embedded_once() -> None:
    """The cache is a spend control, so overlapping duplicates must not both
    reach the paid provider before either has populated the cache."""
    embedder = _BlockingEmbedder()
    subject = QueryEmbedder(embedder)
    query = "når trådte husleieloven i kraft"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(subject.encode, query)
        assert embedder.started.wait(timeout=5), "first embedding call did not start"
        second = pool.submit(subject.encode, query)
        embedder.duplicate_started.wait(timeout=1)
        embedder.release.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert len(embedder.calls) == 1
    assert np.array_equal(first_result[0], second_result[0])


def test_the_leader_publishes_its_vector_to_waiters() -> None:
    subject = QueryEmbedder(_CountingEmbedder())
    flight = _InFlight()
    subject._inflight["question"] = flight

    vector = subject._lead("question", "question", flight)

    assert flight.vector is vector
    assert flight.done.is_set()


def test_trivial_variants_share_one_paid_embedding() -> None:
    """Case and surrounding whitespace do not change the question."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)

    subject.encode("Når trådte husleieloven i kraft")
    subject.encode("  når trådte HUSLEIELOVEN i kraft  ")

    assert len(embedder.calls) == 1


@pytest.mark.parametrize(
    ("first", "equivalent"),
    [
        # Fullwidth forms and a decomposed a-ring: what a copy-paste out of a PDF
        # or a Japanese IME produces. RUF001 flags the fullwidth letters as
        # ambiguous, which is exactly why they belong in a test about folding
        # them — the rule cannot be auto-fixed away without deleting the subject.
        ("lovens § 1", "ＬＯＶＥＮＳ § １"),  # noqa: RUF001
        ("blåbær", "bla\u030abær"),
    ],
)
def test_unicode_compatibility_variants_share_one_paid_embedding(
    first: str, equivalent: str
) -> None:
    """NFKC-equivalent copy/paste forms are the same query, so they are one paid
    embedding. Authored by the CI test author on PR #252."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)

    subject.encode(first)
    subject.encode(equivalent)

    assert len(embedder.calls) == 1


def test_different_questions_never_collide() -> None:
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)

    subject.encode("oppsigelse av leieforhold")
    subject.encode("oppsigelse av arbeidsforhold")

    assert len(embedder.calls) == 2
    assert subject.cached_queries() == 2


def test_the_cache_is_bounded_and_evicts_the_coldest_entry() -> None:
    """A stream of distinct queries must not grow the process without limit."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder, cache_entries=2)

    subject.encode("first")
    subject.encode("second")
    subject.encode("first")  # refreshes recency, so "second" is now coldest
    subject.encode("third")

    assert subject.cached_queries() == 2
    subject.encode("first")
    assert len(embedder.calls) == 3  # "first" survived; only three distinct paid
    subject.encode("second")
    assert len(embedder.calls) == 4  # "second" was the cold entry and was evicted


def test_truncation_is_reported_on_a_cache_hit_too() -> None:
    """The caller is told the query was shortened whether or not the vector came
    from the cache — otherwise the same query answers honestly once and silently
    afterwards."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder, max_tokens=8)
    query = "veldig lang " * 100

    _, first = subject.encode(query)
    _, second = subject.encode(query)

    assert first and second
    assert len(embedder.calls) == 1


def test_defaults_are_the_documented_ones() -> None:
    assert DEFAULT_MAX_QUERY_TOKENS == 256
    assert DEFAULT_CACHE_ENTRIES == 1024
    assert QueryEmbedder(_CountingEmbedder()).max_tokens == DEFAULT_MAX_QUERY_TOKENS


def test_truncate_to_tokens_reports_whether_it_cut() -> None:
    assert truncate_to_tokens("kort spørsmål", 256) == ("kort spørsmål", False)

    cut_text, was_cut = truncate_to_tokens("ord " * 1000, 32)
    assert was_cut
    assert count_input_tokens(cut_text) <= 32


@pytest.mark.parametrize(
    "query",
    [
        "🇳🇴 norsk lov",  # flag: one codepoint pair, several tokens
        "ååå 😀😀 spørsmål",
        "日本語のテキスト",
    ],
)
def test_truncation_never_leaves_half_a_character(query: str) -> None:
    """A BPE token is a byte sequence, not a character, so a cut can land inside
    a multi-byte one. Decoding that fragment normally yields U+FFFD — which would
    put a character the user never wrote into the text that gets embedded and
    billed. Found by the CI test author on this PR, not by review here."""
    for max_tokens in range(1, 8):
        text, _ = truncate_to_tokens(query, max_tokens)
        assert "�" not in text
        text.encode("utf-8")  # round-trips: the tail was dropped, not mangled


def test_the_embedder_truncation_path_has_the_same_guarantee() -> None:
    """The same defect lived in OpenAIEmbedder's own 8191-token cut, where it is
    rarer but not less wrong: a section ending in an emoji would embed a
    replacement character."""
    embedder = OpenAIEmbedder.__new__(OpenAIEmbedder)
    embedder._encoding = _encoding_for(DEFAULT_MODEL_NAME)
    with patch.object(model_module, "_MAX_INPUT_TOKENS", 3):
        assert "�" not in embedder._truncate_to_tokens("日本語のテキスト")


class _FailOnceEmbedder(_CountingEmbedder):
    """First call blocks then fails; every later call succeeds."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self._failed = False

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._failed:
            self._failed = True
            self.started.set()
            assert self.release.wait(timeout=5), "test did not release the failing call"
            raise RuntimeError("provider down")
        return super().encode(texts)


def test_a_waiting_duplicate_retries_when_the_leading_call_fails() -> None:
    """An error is not a vector: it must not be cached, must not strand the
    waiting duplicate, and the retry pays exactly once."""
    embedder = _FailOnceEmbedder()
    subject = QueryEmbedder(embedder)
    query = "når trådte husleieloven i kraft"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(subject.encode, query)
        assert embedder.started.wait(timeout=5), "leading call did not start"
        second = pool.submit(subject.encode, query)
        embedder.release.set()
        with pytest.raises(RuntimeError, match="provider down"):
            first.result(timeout=5)
        vector, truncated = second.result(timeout=5)

    assert len(embedder.calls) == 1
    assert not truncated
    assert vector.shape == (4,)


def test_a_finished_flight_without_a_vector_is_a_retry_not_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flight that ended with no vector is a failed call, and joining it
    late must mean retrying — never handing the caller the nothing."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)
    failed_flight = _InFlight()
    failed_flight.done.set()

    real_claim = subject._claim
    handed_out = []

    def fake_claim(key: str) -> object:
        if not handed_out:
            handed_out.append(key)
            return None, failed_flight, False
        return real_claim(key)

    monkeypatch.setattr(subject, "_claim", fake_claim)
    vector, truncated = subject.encode("når trådte husleieloven i kraft")

    assert len(embedder.calls) == 1
    assert vector.shape == (4,)
    assert not truncated


def test_the_first_claim_leads_and_the_second_joins() -> None:
    """Exactly one caller per key leads the paid call; everyone after joins
    the same flight until it resolves. This is the invariant the bounded
    join timeout turns into a loud failure when broken."""
    subject = QueryEmbedder(_CountingEmbedder())
    cached, flight, leads = subject._claim("nøkkel")
    assert cached is None
    assert leads is True
    cached2, flight2, leads2 = subject._claim("nøkkel")
    assert cached2 is None
    assert leads2 is False
    assert flight2 is flight


def test_an_unresolved_join_fails_loudly_after_the_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The late publisher exists only so a broken-clock mutant terminates:
    the real code must raise on the zeroed clock long before it fires."""
    subject = QueryEmbedder(_CountingEmbedder())
    flight = _InFlight()
    subject._inflight["question"] = flight

    def publish_late() -> None:
        flight.vector = np.ones(4, dtype=np.float32)
        flight.done.set()

    late = threading.Timer(0.5, publish_late)
    late.start()
    monkeypatch.setattr(query_module, "_JOIN_TIMEOUT_SECONDS", 0.0)
    try:
        with pytest.raises(
            NetworkError,
            match=r"query embedding join timed out:.*within 0s",
        ):
            subject.encode("question")
    finally:
        late.cancel()


def test_a_silent_flight_trips_the_bounded_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The join gives up on the clock, not on the flight's mercy: a flight
    nobody resolves within the window is a defect reported by name."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)
    monkeypatch.setattr(query_module, "_JOIN_TIMEOUT_SECONDS", 0.05)
    silent_flight = _InFlight()
    late = threading.Timer(0.3, silent_flight.done.set)
    late.start()
    handed_out: list[str] = []
    real_claim = subject._claim

    def fake_claim(key: str) -> object:
        if not handed_out:
            handed_out.append(key)
            return None, silent_flight, False
        return real_claim(key)

    monkeypatch.setattr(subject, "_claim", fake_claim)
    try:
        with pytest.raises(NetworkError) as excinfo:
            subject.encode("hvor lenge gjelder en leiekontrakt")
    finally:
        late.cancel()
    assert str(excinfo.value) == (
        "query embedding join timed out: the in-flight call neither published nor failed within 0s"
    )
    assert embedder.calls == []


def test_knows_is_a_side_effect_free_peek() -> None:
    """The paid meter's peek: answers from the cache alone, pays nothing,
    and shares the normalised key with encode."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder)
    assert subject.knows("skatt") is False
    assert embedder.calls == []
    subject.encode("skatt")
    assert subject.knows("skatt") is True
    assert subject.knows("  SKATT  ") is True
    assert len(embedder.calls) == 1


def test_knows_does_not_refresh_recency() -> None:
    """A metering peek must not perturb eviction: peeking the oldest entry
    does not save it from the LRU."""
    subject = QueryEmbedder(_CountingEmbedder(), cache_entries=2)
    subject.encode("a")
    subject.encode("b")
    assert subject.knows("a") is True
    subject.encode("c")
    assert subject.knows("a") is False
    assert subject.knows("b") is True


def test_knows_shares_the_encoders_tokenizer() -> None:
    """The peek must truncate with the same tokenizer as encode, or a
    non-default model's cache entries would be invisible to the meter:
    gpt2 and the default encoding cut this query at different points."""
    subject = QueryEmbedder(_CountingEmbedder(), max_tokens=5, model_name="gpt2")
    query = "hva sier arbeidsmiljøloven om oppsigelsestid i prøvetiden"
    subject.encode(query)
    assert subject.knows(query) is True
