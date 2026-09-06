"""Coverage for the bounds that make open self-service sign-up safe.

The behavioural tests for these live next to the code they exercise
(``test_quota.py``, ``test_access.py``, ``test_query_embedder.py``). This file
pins the things those tests are silent about and a mutant therefore survives:
the exact environment-variable names an operator types into a deployment file,
the exact boundary at which a brake refuses rather than admits, and the wiring
that decides which deployments get an instance ceiling at all.

Each of those is a place where being one off is invisible in normal use and
wrong in exactly the situation the bound exists for.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.embeddings.query as query_module
import lovspor.mcp as mcp_module
from lovspor.access import (
    SELF_SERVICE_LIMITS,
    Credential,
    CredentialStore,
    Limits,
    ServiceLimits,
    generate_token,
    hash_token,
    int_setting_from_env,
    self_service_limits_from_env,
    write_credential_file,
)
from lovspor.embeddings import LEGACY_SPACE_DESCRIPTOR, esi_for_descriptor, write_embeddings
from lovspor.embeddings.model import truncate_to_tokens
from lovspor.embeddings.query import (
    DEFAULT_CACHE_ENTRIES,
    DEFAULT_MAX_QUERY_TOKENS,
    QueryEmbedder,
)
from lovspor.errors import ConfigError
from lovspor.mcp import CorpusReader, HttpConfig, _build_enforcer, _build_verifier
from lovspor.quota import QuotaEnforcer, QuotaExceededError, _State
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest


class _Clock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.mono

    def utc_now(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.mono += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _store(path: Path, limits: Limits, *ids: str) -> CredentialStore:
    write_credential_file(
        path,
        [
            Credential(
                credential_id=cid,
                label=cid,
                token_sha256=hash_token(generate_token()),
                limits=limits,
            )
            for cid in (ids or ("a",))
        ],
    )
    return CredentialStore(path)


# --- the names an operator actually types ------------------------------------
#
# A mutated variable name is invisible: the code still reads *an* env var, finds
# nothing, and serves the built-in default. The operator sees the value they set
# being ignored, which is the exact failure the fail-closed parse exists to stop.


@pytest.mark.parametrize(
    ("variable", "field", "value"),
    [
        ("LOVSPOR_SELF_SERVICE_MAX_IN_FLIGHT", "max_in_flight", 7),
        ("LOVSPOR_SELF_SERVICE_RATE_PER_MINUTE", "rate_per_minute", 11),
        ("LOVSPOR_SELF_SERVICE_RATE_BURST", "rate_burst", 13),
        ("LOVSPOR_SELF_SERVICE_DAILY_QUOTA", "daily_quota", 17),
        ("LOVSPOR_SELF_SERVICE_PAID_DAILY_QUOTA", "paid_daily_quota", 19),
    ],
)
def test_every_self_service_variable_reaches_its_own_field(
    variable: str, field: str, value: int
) -> None:
    limits = self_service_limits_from_env({variable: str(value)})

    assert getattr(limits, field) == value
    # and nothing else moved: a mapping that wrote two fields would pass a
    # single-field assertion.
    untouched = limits.model_dump()
    untouched.pop(field)
    baseline = SELF_SERVICE_LIMITS.model_dump()
    baseline.pop(field)
    assert untouched == baseline


@pytest.mark.parametrize(
    ("variable", "field", "value"),
    [
        ("LOVSPOR_SERVICE_MAX_IN_FLIGHT", "max_in_flight", 3),
        ("LOVSPOR_SERVICE_DAILY_QUOTA", "daily_quota", 4444),
        ("LOVSPOR_SERVICE_PAID_DAILY_QUOTA", "paid_daily_quota", 555),
    ],
)
def test_every_service_variable_reaches_its_own_field(
    variable: str, field: str, value: int
) -> None:
    ceiling = ServiceLimits.from_env({variable: str(value)})

    assert getattr(ceiling, field) == value
    untouched = ceiling.model_dump()
    untouched.pop(field)
    baseline = ServiceLimits().model_dump()
    baseline.pop(field)
    assert untouched == baseline


def test_a_self_service_variable_does_not_answer_to_the_service_name() -> None:
    """The two families are one prefix apart, and confusing them would silently
    apply a per-user number as an instance ceiling or vice versa."""
    assert self_service_limits_from_env({"LOVSPOR_SERVICE_DAILY_QUOTA": "5"}) == (
        SELF_SERVICE_LIMITS
    )
    assert ServiceLimits.from_env({"LOVSPOR_SELF_SERVICE_DAILY_QUOTA": "5"}) == ServiceLimits()


def test_int_setting_reads_its_variable_and_falls_back_otherwise() -> None:
    assert int_setting_from_env("X", 9, {"X": "42"}) == 42
    assert int_setting_from_env("X", 9, {"X": ""}) == 9
    assert int_setting_from_env("X", 9, {"OTHER": "42"}) == 9


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "one", "12abc"])
def test_an_unusable_value_refuses_rather_than_defaulting(value: str) -> None:
    with pytest.raises(ConfigError):
        self_service_limits_from_env({"LOVSPOR_SELF_SERVICE_DAILY_QUOTA": value})


def test_the_refusal_names_the_variable_the_operator_set() -> None:
    """An error that does not name the variable sends the operator hunting
    through a file of them."""
    with pytest.raises(ConfigError, match="LOVSPOR_SERVICE_PAID_DAILY_QUOTA"):
        ServiceLimits.from_env({"LOVSPOR_SERVICE_PAID_DAILY_QUOTA": "nope"})


# --- the exact boundary ------------------------------------------------------
#
# Every brake is "refuse when used >= limit". Off by one in either direction is
# invisible on ordinary traffic and wrong precisely when the limit matters.


@pytest.mark.parametrize("quota", [1, 3])
def test_a_daily_quota_admits_exactly_its_number(tmp_path: Path, clock: _Clock, quota: int) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(daily_quota=quota, rate_per_minute=600, rate_burst=600)),
        clock.monotonic,
        clock.utc_now,
    )

    for _ in range(quota):
        with enforcer.guard("a"):
            pass

    assert enforcer.daily_used("a") == quota
    with pytest.raises(QuotaExceededError), enforcer.guard("a"):
        pass  # pragma: no cover - guard raises before the body


def test_a_paid_quota_admits_exactly_its_number(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(paid_daily_quota=2)),
        clock.monotonic,
        clock.utc_now,
    )

    for _ in range(2):
        with enforcer.guard("a", paid=True):
            pass

    assert enforcer.paid_daily_used("a") == 2
    with pytest.raises(QuotaExceededError), enforcer.guard("a", paid=True):
        pass  # pragma: no cover - guard raises before the body


def test_in_flight_admits_exactly_its_number(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(max_in_flight=2)), clock.monotonic, clock.utc_now
    )

    with (
        enforcer.guard("a"),
        enforcer.guard("a"),
        pytest.raises(QuotaExceededError),
        enforcer.guard("a"),
    ):
        pass  # pragma: no cover - the third guard raises before the body


def test_the_instance_ceiling_admits_exactly_its_number(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b"),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(daily_quota=2, paid_daily_quota=1),
    )

    with enforcer.guard("a"):
        pass
    with enforcer.guard("b"):
        pass

    assert enforcer.service_daily_used() == 2
    with pytest.raises(QuotaExceededError), enforcer.guard("a"):
        pass  # pragma: no cover - guard raises before the body


def test_an_unknown_credential_reports_no_paid_usage(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(_store(tmp_path / "c.json", Limits()), clock.monotonic, clock.utc_now)

    assert enforcer.paid_daily_used("never-seen") == 0
    assert enforcer.service_paid_daily_used() == 0


# --- eviction boundary -------------------------------------------------------


def test_a_state_at_exactly_the_idle_window_is_evicted(tmp_path: Path, clock: _Clock) -> None:
    """The comparison is ``>=``: at the window it goes. One second short of it
    stays — asserted below — so the boundary itself is pinned rather than the
    fact that eviction happens eventually."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b"),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=1,
        eviction_idle_seconds=60.0,
    )
    with enforcer.guard("a"):
        pass
    clock.now = clock.now.replace(day=clock.now.day + 1)  # yesterday's spend
    clock.advance(60.0)

    with enforcer.guard("b"):
        pass

    assert enforcer.tracked_credentials() == 1


def test_a_state_just_inside_the_idle_window_survives(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b"),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=1,
        eviction_idle_seconds=60.0,
    )
    with enforcer.guard("a"):
        pass
    clock.now = clock.now.replace(day=clock.now.day + 1)
    clock.advance(59.0)

    with enforcer.guard("b"):
        pass

    assert enforcer.tracked_credentials() == 2


def test_no_sweep_happens_below_the_threshold(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b"),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=99,
        eviction_idle_seconds=1.0,
    )
    with enforcer.guard("a"):
        pass
    clock.now = clock.now.replace(day=clock.now.day + 1)
    clock.advance(10_000)

    with enforcer.guard("b"):
        pass

    assert enforcer.tracked_credentials() == 2


# --- wiring ------------------------------------------------------------------


def _oauth_config(tmp_path: Path, **overrides: object) -> HttpConfig:
    return HttpConfig(
        credentials_path=tmp_path / "c.json",
        authkit_domain="https://example.authkit.app",
        public_url="https://lovspor.no/mcp",
        **overrides,
    )


def test_only_hosted_oauth_gets_an_instance_ceiling(tmp_path: Path) -> None:
    """Opaque tokens are hand-issued, so their aggregate is already bounded by
    how many the operator issued; a server-wide cap there would refuse a known
    tester because of strangers."""
    store = _store(tmp_path / "c.json", Limits())

    oauth = _build_enforcer(_oauth_config(tmp_path), store)
    opaque = _build_enforcer(HttpConfig(credentials_path=tmp_path / "c.json"), store)

    assert oauth is not None
    assert oauth._service_limits == ServiceLimits()
    assert opaque is not None
    assert opaque._service_limits is None


def test_nothing_is_metered_without_a_credential_source(tmp_path: Path) -> None:
    assert _build_enforcer(_oauth_config(tmp_path), None) is None


def test_the_verifier_hands_self_service_users_the_configured_limits(tmp_path: Path) -> None:
    """The limits an operator set must reach the bucket WorkOS users meter
    against — a verifier built with the built-in defaults instead would ignore
    the deployment's configuration silently."""
    tightened = SELF_SERVICE_LIMITS.model_copy(update={"paid_daily_quota": 3})
    store = _store(tmp_path / "c.json", Limits())

    _, metering = _build_verifier(_oauth_config(tmp_path, self_service_limits=tightened), store)

    assert metering is not None
    assert metering.limits_for("workos:abc") == tightened
    # a hand-issued credential keeps its own, from the store
    assert metering.limits_for("a") == Limits()


def test_without_oauth_the_store_is_both_verifier_and_metering(tmp_path: Path) -> None:
    store = _store(tmp_path / "c.json", Limits())

    verifier, metering = _build_verifier(HttpConfig(credentials_path=tmp_path / "c.json"), store)

    assert verifier is store
    assert metering is store


# --- query embedder ----------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "attribute", "value"),
    [
        ("LOVSPOR_SEMANTIC_QUERY_MAX_TOKENS", "max_tokens", 32),
        ("LOVSPOR_SEMANTIC_QUERY_CACHE_ENTRIES", "cache_entries", 7),
    ],
)
def test_query_embedder_reads_its_own_variables(
    monkeypatch: pytest.MonkeyPatch, variable: str, attribute: str, value: int
) -> None:
    monkeypatch.setenv(variable, str(value))

    subject = QueryEmbedder.from_env(_CountingEmbedder())

    assert getattr(subject, f"_{attribute}") == value


def test_query_embedder_defaults_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOVSPOR_SEMANTIC_QUERY_MAX_TOKENS", raising=False)
    monkeypatch.delenv("LOVSPOR_SEMANTIC_QUERY_CACHE_ENTRIES", raising=False)

    subject = QueryEmbedder.from_env(_CountingEmbedder())

    assert subject._max_tokens == DEFAULT_MAX_QUERY_TOKENS
    assert subject._cache_entries == DEFAULT_CACHE_ENTRIES


def test_a_query_at_exactly_the_cap_is_not_truncated() -> None:
    """``<=`` not ``<``: a query that fits exactly is whole, and reporting it as
    truncated would put a false notice on a complete answer."""
    text = "ord " * 200
    at_cap, _ = truncate_to_tokens(text, 10)

    same, truncated = truncate_to_tokens(at_cap, 10)

    assert not truncated
    assert same == at_cap


class _CountingEmbedder:
    def __init__(self, dim: int = 4) -> None:
        self.calls: list[list[str]] = []
        self._dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def get_dimension(self) -> int:
        return self._dim

    @property
    def space_id(self) -> str:
        return esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR)


def test_the_cache_stores_what_was_sent_not_what_was_asked() -> None:
    """Two queries that differ only past the cap are the same paid embedding,
    because the cap is applied before the key is taken."""
    embedder = _CountingEmbedder()
    subject = QueryEmbedder(embedder, max_tokens=4)

    subject.encode("husleieloven paragraf en to tre fire fem")
    subject.encode("husleieloven paragraf en to tre fire seks")

    assert len(embedder.calls) == 1


# --- the notice a truncated search carries -----------------------------------


def _seed_corpus(root: Path, *, dim: int = 4) -> None:
    record = ManifestRecord.model_validate(
        {
            "doc_type": "lov",
            "xml_hash": "a" * 64,
            "markdown_path": "lover/testloven.md",
            "source_dataset": "gjeldende-lover",
            "last_seen": datetime(2026, 4, 27, tzinfo=UTC),
            "status": "current",
            "slug": "testloven",
            "title": "Testloven",
            "embedding_hash": "a" * 64,
            "embedding_space": LEGACY_SPACE_DESCRIPTOR,
            "embedding_space_id": esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR),
        }
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


def test_a_truncated_search_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranking against the part of the question that fitted, without saying so,
    would answer a question the user did not ask."""
    monkeypatch.setenv("LOVSPOR_SEMANTIC_QUERY_MAX_TOKENS", "2")
    _seed_corpus(tmp_path)

    result = CorpusReader(tmp_path, embedder=_CountingEmbedder()).semantic_search("formal " * 50)

    assert result["notice"] is not None
    assert "truncated to the first 2 tokens" in result["notice"]
    assert [hit["slug"] for hit in result["results"]] == ["testloven"]


def test_a_short_search_carries_no_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOVSPOR_SEMANTIC_QUERY_MAX_TOKENS", "256")
    _seed_corpus(tmp_path)

    result = CorpusReader(tmp_path, embedder=_CountingEmbedder()).semantic_search("formal")

    assert result["notice"] is None


# --- what a refused caller is actually told ----------------------------------
#
# A tool body cannot set an HTTP status, so the refusal reason and the retry hint
# travel in the message and in ``retry_after_seconds``. That text IS the API for
# this path: a client that cannot tell "you are over your own limit" from "the
# server is full", or that gets no retry hint, has to guess.


def _refusal(
    enforcer: QuotaEnforcer, credential_id: str = "a", *, paid: bool = False
) -> QuotaExceededError:
    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard(credential_id, paid=paid):
        pass  # pragma: no cover - guard raises before the body
    return excinfo.value


def test_a_daily_refusal_points_past_midnight(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(daily_quota=1)), clock.monotonic, clock.utc_now
    )
    with enforcer.guard("a"):
        pass

    error = _refusal(enforcer)

    assert "daily quota of 1 calls is exhausted" in str(error)
    # 12:00 UTC in the fixture clock, so exactly twelve hours remain.
    assert error.retry_after_seconds == 12 * 3600


def test_a_paid_refusal_says_the_free_tools_still_work(tmp_path: Path, clock: _Clock) -> None:
    """The message carries the difference that makes a tight paid budget
    acceptable — without it a user reads "exhausted" as "locked out"."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(paid_daily_quota=1)), clock.monotonic, clock.utc_now
    )
    with enforcer.guard("a", paid=True):
        pass

    error = _refusal(enforcer, paid=True)

    assert "daily limit of 1 semantic searches is exhausted" in str(error)
    assert "the other fifteen tools are unaffected" in str(error)
    assert error.retry_after_seconds == 12 * 3600


def test_an_in_flight_refusal_hints_a_second_not_a_day(tmp_path: Path, clock: _Clock) -> None:
    """An in-flight slot frees when a call finishes, so pointing at midnight
    would park a client for hours over a queue that clears in a moment."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(max_in_flight=1)), clock.monotonic, clock.utc_now
    )

    with enforcer.guard("a"):
        error = _refusal(enforcer)

    assert "1 calls already in flight for this credential" in str(error)
    assert error.retry_after_seconds == 1


def test_a_rate_refusal_hints_when_a_token_exists(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(rate_per_minute=60, rate_burst=1)),
        clock.monotonic,
        clock.utc_now,
    )
    with enforcer.guard("a"):
        pass

    error = _refusal(enforcer)

    assert "rate limit of 60/min exceeded" in str(error)
    assert error.retry_after_seconds == 1


def test_a_service_daily_refusal_names_the_server_not_the_caller(
    tmp_path: Path, clock: _Clock
) -> None:
    """Blaming the caller's own quota for a full server sends them to look at
    settings that are not the cause."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits()),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(daily_quota=1),
    )
    with enforcer.guard("a"):
        pass

    error = _refusal(enforcer)

    assert "this server has reached its daily ceiling of 1 calls across all users" in str(error)
    assert error.retry_after_seconds == 12 * 3600


def test_a_service_paid_refusal_names_semantic_searches(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits()),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(paid_daily_quota=1),
    )
    with enforcer.guard("a", paid=True):
        pass

    error = _refusal(enforcer, paid=True)

    assert (
        "this server has reached its daily ceiling of 1 semantic searches across all users"
        in str(error)
    )
    assert error.retry_after_seconds == 12 * 3600


def test_a_service_capacity_refusal_hints_a_second(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b"),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(max_in_flight=1),
    )

    with enforcer.guard("a"):
        error = _refusal(enforcer, "b")

    assert "this server is at capacity (1 calls in flight across all users)" in str(error)
    assert error.retry_after_seconds == 1


# --- the counters accumulate rather than latch -------------------------------


def test_both_service_counters_accumulate_across_callers(tmp_path: Path, clock: _Clock) -> None:
    """``+= 1`` mutated to ``= 1`` pins a counter at one and the ceiling never
    arrives — a single-call test cannot tell the two apart."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "a", "b", "c"),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(daily_quota=100, paid_daily_quota=100),
    )

    for credential_id in ("a", "b", "c"):
        with enforcer.guard(credential_id, paid=True):
            pass

    assert enforcer.service_daily_used() == 3
    assert enforcer.service_paid_daily_used() == 3


def test_a_credentials_counters_accumulate(tmp_path: Path, clock: _Clock) -> None:
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(daily_quota=10, paid_daily_quota=10)),
        clock.monotonic,
        clock.utc_now,
    )

    for _ in range(3):
        with enforcer.guard("a", paid=True):
            pass

    assert enforcer.daily_used("a") == 3
    assert enforcer.paid_daily_used("a") == 3


# --- exactly one tool is charged as paid -------------------------------------


def test_only_semantic_search_is_registered_as_a_paid_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which tools cost money is decided once, at registration. Marking one too
    many would meter free corpus reads against the embedding budget; marking one
    too few would leave the budget unenforced on the only tool that spends it."""
    seen: dict[str, bool] = {}
    original = mcp_module._with_quota

    def _recording(fn, enforcer, *, paid):  # type: ignore[no-untyped-def]
        seen[fn.__name__] = paid
        return original(fn, enforcer, paid=paid)

    monkeypatch.setattr(mcp_module, "_with_quota", _recording)
    _seed_corpus(tmp_path)
    mcp_module.build_server(
        tmp_path,
        http=HttpConfig(credentials_path=_credentials_file(tmp_path)),
    )

    assert seen["semantic_search"] is True
    assert [name for name, paid in seen.items() if paid] == ["semantic_search"]
    assert seen["get_law"] is False


def _credentials_file(tmp_path: Path) -> Path:
    path = tmp_path / "credentials.json"
    write_credential_file(
        path,
        [
            Credential(
                credential_id="a",
                label="a",
                token_sha256=hash_token(generate_token()),
                limits=Limits(),
            )
        ],
    )
    return path


# --- the rest of what the gate found unguarded -------------------------------


def test_a_limit_of_exactly_one_is_accepted(tmp_path: Path) -> None:
    """``< 1`` not ``<= 1``: one is the tightest usable limit, and refusing it
    would take the strictest setting an operator can express off the table."""
    assert self_service_limits_from_env({"LOVSPOR_SELF_SERVICE_DAILY_QUOTA": "1"}).daily_quota == 1
    assert ServiceLimits.from_env({"LOVSPOR_SERVICE_PAID_DAILY_QUOTA": "1"}).paid_daily_quota == 1


def test_the_sweep_looks_past_a_credential_that_is_in_flight(tmp_path: Path, clock: _Clock) -> None:
    """``continue`` not ``break``: a busy credential encountered early must skip
    itself, not end the sweep and strand every evictable state behind it."""
    enforcer = QuotaEnforcer(
        _store(tmp_path / "c.json", Limits(), "busy", "stale", "new"),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=2,
        eviction_idle_seconds=60.0,
    )
    with enforcer.guard("stale"):
        pass
    clock.now = clock.now.replace(day=clock.now.day + 1)
    clock.advance(120)

    with enforcer.guard("busy"):  # inserted first, holds a slot during the sweep
        with enforcer.guard("new"):
            pass
        # "stale" sits behind "busy" in insertion order and must still go
        assert enforcer.tracked_credentials() == 2


def test_a_fresh_state_records_when_it_was_created(clock: _Clock) -> None:
    """``last_seen`` is read as a number by the sweep; a state that never
    recorded one would raise there instead of being considered."""
    state = _State(clock.monotonic, clock.utc_now)

    assert state.last_seen == clock.monotonic()


def test_the_query_is_truncated_under_the_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is measured in tokens, and tokens differ per model, so the
    embedder's own model name has to reach the truncation call."""
    seen: dict[str, object] = {}

    def _recording(text: str, max_tokens: int, model_name: str) -> tuple[str, bool]:
        seen["model_name"] = model_name
        seen["max_tokens"] = max_tokens
        return text, False

    monkeypatch.setattr(query_module, "truncate_to_tokens", _recording)
    QueryEmbedder(_CountingEmbedder(), max_tokens=11, model_name="text-embedding-3-small").encode(
        "spørsmål"
    )

    assert seen == {"model_name": "text-embedding-3-small", "max_tokens": 11}


def test_the_verifier_binds_tokens_to_the_configured_public_url(tmp_path: Path) -> None:
    """The audience is what stops a token minted for another resource opening
    this one, so it has to be the URL this server actually answers on."""
    store = _store(tmp_path / "c.json", Limits())

    verifier, _ = _build_verifier(_oauth_config(tmp_path), store)

    assert verifier is not None
    assert verifier._workos._audience == "https://lovspor.no/mcp"
    assert verifier._workos._issuer == "https://example.authkit.app"


def test_an_empty_or_zero_limit_search_says_which_it_was(tmp_path: Path) -> None:
    """Both return no results, and a caller that cannot tell them apart cannot
    tell "you asked nothing" from "the corpus had nothing"."""
    _seed_corpus(tmp_path)
    reader = CorpusReader(tmp_path, embedder=_CountingEmbedder())

    assert reader.semantic_search("")["notice"] == "query is empty; nothing was searched."
    assert reader.semantic_search("formal", limit=0)["notice"] == (
        "limit is 0; nothing was searched."
    )


def test_the_default_limit_is_twenty(tmp_path: Path) -> None:
    """Documented in the tool description an AI client reads; a silent change
    would alter every caller's result set."""
    assert inspect.signature(CorpusReader.semantic_search).parameters["limit"].default == 20
