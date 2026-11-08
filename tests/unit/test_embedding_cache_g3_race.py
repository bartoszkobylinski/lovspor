"""The G3 race barrier and evidence gate, in process (#802, GPT-6 review 5819177180).

``scripts/ops/embedding_cache_g3_race.py`` is staging-only. These tests drive
its barrier against an in-memory bucket and a fake clock, so a timeout costs no
wall time, and feed its evidence gate the shapes a sequential schedule leaves.
The cross-process race itself is in ``test_embedding_cache_staging.py``.
"""

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lovspor.embeddings.r2_client import Reply

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "embedding_cache_g3_race.py"
_TARGET = "lovspor-embedding-cache/schema-1/esi/units/h/0-4-u.lsec"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("embedding_cache_g3_race", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


race = _load()


@dataclass
class Memory:
    """put_if_absent and get, as the fake bucket answers them, with a call log."""

    objects: dict[str, bytes] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    on_get: Any = None

    def put_if_absent(self, key: str, data: bytes) -> Reply:
        self.calls.append(("PUT", key))
        if key in self.objects:
            return Reply(status=412, body=b"")
        self.objects[key] = data
        return Reply(status=200, body=b"")

    def get(self, key: str) -> Reply:
        self.calls.append(("GET", key))
        if self.on_get is not None:
            self.on_get(key)
        data = self.objects.get(key)
        return Reply(status=404, body=b"") if data is None else Reply(status=200, body=data)


@dataclass
class Clock:
    now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _barrier(bucket: Memory, writer: str = "a", clock: Clock | None = None) -> Any:
    clock = clock or Clock()
    return race.Barrier(bucket, "r1", writer, timeout=3.0, poll=1.0, clock=clock, sleep=clock.sleep)


def _advertise(bucket: Memory, writer: str, candidate: str = "b" * 64) -> None:
    peer = race.Readiness("r1", writer, _TARGET, candidate)
    bucket.objects[_barrier(bucket).key(_TARGET, writer)] = peer.encode()


def test_the_barrier_returns_the_peers_candidate_once_it_has_advertised() -> None:
    bucket, clock = Memory(), Clock()
    bucket.on_get = lambda _key: _advertise(bucket, "b") if clock.now >= 2 else None

    peer = _barrier(bucket, clock=clock).meet(_TARGET, "a" * 64)

    assert peer.candidate_sha256 == "b" * 64
    assert clock.slept == [1.0, 1.0]
    assert ("PUT", _barrier(bucket).key(_TARGET, "a")) == bucket.calls[0]


def test_the_barrier_fails_at_the_deadline_and_sends_nothing_else() -> None:
    bucket, clock = Memory(), Clock()

    with pytest.raises(race.RaceError, match="writer b never reached the barrier within 3s"):
        _barrier(bucket, clock=clock).meet(_TARGET, "a" * 64)

    assert clock.now == 3.0
    assert [m for m, _k in bucket.calls].count("PUT") == 1


def test_coordination_keys_are_per_run_and_per_target_under_the_lifecycle_prefix() -> None:
    barrier = _barrier(Memory())

    key = barrier.key(_TARGET, "a")
    assert key.startswith("lovspor-embedding-cache/g3-barrier/r1/")
    assert key.endswith("/a.json")
    assert key != barrier.key(_TARGET + "x", "a")
    assert key != race.Barrier(Memory(), "r2", "a", timeout=1).key(_TARGET, "a")


@pytest.mark.parametrize("run_id", ["", "UPPER", "a/b", "../x", "a" * 101])
def test_a_run_id_that_could_leave_its_directory_is_refused(run_id: str) -> None:
    with pytest.raises(race.RaceError, match="run id"):
        race.Barrier(Memory(), run_id, "a", timeout=1)


def test_an_unknown_writer_is_refused() -> None:
    with pytest.raises(race.RaceError, match="writer must be one of"):
        race.Barrier(Memory(), "r1", "c", timeout=1)


@pytest.mark.parametrize(
    "fields",
    [
        {"run_id": "r1", "writer": "a", "target": _TARGET, "candidate_sha256": "b" * 64},
        {"run_id": "r1", "writer": "b", "target": _TARGET, "candidate_sha256": "short"},
        {"run_id": "r1", "writer": "b", "target": _TARGET},
        {"run_id": "r1", "writer": "b", "target": _TARGET, "candidate_sha256": "b" * 64, "x": 1},
    ],
    ids=["own-writer", "bad-digest", "missing-field", "extra-field"],
)
def test_a_malformed_peer_object_is_refused(fields: dict[str, object]) -> None:
    import json  # noqa: PLC0415

    bucket = Memory()
    bucket.objects[_barrier(bucket).key(_TARGET, "b")] = json.dumps(fields).encode()

    with pytest.raises(race.RaceError, match="peer's coordination object"):
        _barrier(bucket).meet(_TARGET, "a" * 64)


def test_an_unreadable_peer_object_is_an_error_not_a_wait() -> None:
    bucket = Memory()
    bucket.get = lambda _key: Reply(status=403, body=b"")  # type: ignore[method-assign]

    with pytest.raises(race.RaceError, match="GET answered 403"):
        _barrier(bucket).meet(_TARGET, "a" * 64)


def test_the_racing_client_holds_only_the_first_cache_put_at_the_barrier() -> None:
    bucket = Memory()
    _advertise(bucket, "b")
    client = race.RacingClient(bucket, _barrier(bucket))

    first = client.put_if_absent(_TARGET, b"entry-a")
    second = client.put_if_absent(_TARGET + "2", b"next")

    assert (first.status, second.status) == (200, 200)
    assert client.attempt.target == _TARGET
    assert client.attempt.candidate_sha256 == race.sha256(b"entry-a")
    assert client.attempt.peer_candidate_sha256 == "b" * 64
    assert client.attempt.put_status == 200
    advertised = [k for m, k in bucket.calls if m == "PUT" and "/g3-barrier/" in k]
    assert len(advertised) == 1
    assert bucket.calls.index(("PUT", advertised[0])) < bucket.calls.index(("PUT", _TARGET))


def test_the_racing_client_records_a_held_put() -> None:
    bucket = Memory(objects={_TARGET: b"winner"})
    _advertise(bucket, "b")
    client = race.RacingClient(bucket, _barrier(bucket))

    assert client.put_if_absent(_TARGET, b"loser").status == 412
    assert client.attempt.put_status == 412
    assert client.get(_TARGET).body == b"winner"


def _evidence(**changes: object) -> tuple[dict[str, object], dict[str, object]]:
    a = {
        "writer": "a",
        "run_id": "r1",
        "target": _TARGET,
        "candidate_sha256": "a" * 64,
        "peer_candidate_sha256": "b" * 64,
        "put_status": 200,
        "remote_sha256": "a" * 64,
        "local_sha256": "a" * 64,
        "sidecar_sha256": "c" * 64,
    }
    b = dict(a, writer="b", candidate_sha256="b" * 64, peer_candidate_sha256="a" * 64)
    b["put_status"] = 412
    for name, value in changes.items():
        writer, _, key = name.partition("__")
        (a if writer == "a" else b)[key] = value
    return a, b


def test_evidence_of_a_real_race_is_accepted() -> None:
    assert race.judge(*_evidence()) == []
    assert race.judge(*_evidence(b__put_status=409)) == []


def test_a_sequential_schedule_is_refused_even_with_equal_sidecars() -> None:
    """The second writer found the first one's object and never PUT: what two
    jobs sharing only ``needs:`` leave behind."""
    a, b = _evidence(b__put_status=None, b__peer_candidate_sha256=None)

    failures = race.judge(a, b)

    assert "a writer did not see the other's candidate at the barrier" in failures
    assert "expected one PUT answered 200 and one 412/409, got [200, None]" in failures


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"b__candidate_sha256": "a" * 64}, "nothing was contested"),
        ({"b__target": _TARGET + "x"}, "did not attempt the same key"),
        ({"a__target": None, "b__target": None}, "did not attempt the same key"),
        ({"b__run_id": "r2"}, "different races"),
        ({"b__writer": "a"}, "writer a and writer b"),
        ({"b__put_status": 200}, "got [200, 200]"),
        ({"a__put_status": 412}, "got [412, 412]"),
        ({"b__put_status": 500}, "got [200, 500]"),
        ({"a__peer_candidate_sha256": "d" * 64}, "did not see the other's candidate"),
        ({"b__local_sha256": "b" * 64}, "the loser did not adopt the winner's bytes"),
        ({"a__local_sha256": "b" * 64}, "the winner's local copy is not its own candidate"),
        ({"b__remote_sha256": "b" * 64}, "the bucket does not hold the winner's candidate"),
        ({"a__remote_sha256": "b" * 64}, "the bucket does not hold the winner's candidate"),
        ({"b__sidecar_sha256": "d" * 64}, "different sidecars"),
    ],
)
def test_evidence_that_does_not_prove_the_race_is_refused(
    changes: dict[str, object], failure: str
) -> None:
    failures = race.judge(*_evidence(**changes))

    assert any(failure in reason for reason in failures), failures


def test_the_winner_may_be_either_writer() -> None:
    a, b = _evidence(
        a__put_status=412,
        b__put_status=200,
        a__remote_sha256="b" * 64,
        b__remote_sha256="b" * 64,
        a__local_sha256="b" * 64,
        b__local_sha256="b" * 64,
    )

    assert race.judge(a, b) == []
