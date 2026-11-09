"""The G3 race: a cross-runner barrier at the PUT boundary, and its evidence gate (#802).

Staging only. ``scripts/ops/embedding_cache_g3.py`` wraps the R2 client in
:class:`RacingClient` for its ``race`` step. No sync, CLI or server path imports
this module (a test pins that); it lives under ``src/`` so the PR mutation gate
measures it.

Why a barrier: two jobs that merely share ``needs:`` may run one after the
other, and then the second writer finds the first one's object with a ``GET``
and never sends a competing ``PUT``. Equal sidecars and a third runner paying 0
hold just the same, so they prove eventual agreement, not the conditional-PUT
race of design §8.1.

The barrier sits where that race happens. :class:`RacingClient` lets every call
through except the writer's first cache-entry ``PUT``, which ``RemoteEntryStore``
sends only after the key's ``GET`` missed, the fake provider was paid and the
entry was built. Before sending it, the writer:

1. advertises readiness: a conditional ``PUT`` of its own coordination object,
   naming the run, the target key and the digest of the entry it is about to
   send;
2. ``GET``s the peer's coordination object until it exists, valid and naming
   the same run and target, or the deadline passes (then it fails, and no
   competing ``PUT`` is sent).

So when either writer sends the target ``PUT``, the other already holds a
different candidate for the same key: whoever commits second can only be
answered ``412``/``409``, and must adopt the winner. :func:`judge` checks that
both writers' evidence shows exactly that. A sequential schedule cannot pass:
the first writer times out at the barrier.

Coordination objects live under ``lovspor-embedding-cache/g3-barrier/<run>/<key
digest>/<writer>.json``: the staging bucket, the prefix the 30-day lifecycle
rule expires, a fresh path per run and per target key. Only ``GET`` and
conditional ``PUT``: no LIST, no DELETE, no other credentials.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol

from lovspor.embeddings.cache_remote import REMOTE_PREFIX
from lovspor.embeddings.r2_client import Reply
from lovspor.errors import EmbeddingCacheError

BARRIER_PREFIX = f"{REMOTE_PREFIX}/g3-barrier"
WRITERS = ("a", "b")
CREATED, FOUND, ABSENT = 200, 200, 404
HELD = (409, 412)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")


class RaceError(EmbeddingCacheError):
    """The G3 race could not be run, or its evidence does not prove a race."""


class Bucket(Protocol):
    def put_if_absent(self, key: str, data: bytes) -> Reply: ...

    def get(self, key: str) -> Reply: ...


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def peer_of(writer: str) -> str:
    if writer not in WRITERS:
        raise RaceError(f"writer must be one of {WRITERS}, got {writer!r}")
    return WRITERS[1 - WRITERS.index(writer)]


@dataclass(frozen=True)
class Readiness:
    """What a writer advertises at the barrier."""

    run_id: str
    writer: str
    target: str
    candidate_sha256: str

    def encode(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True).encode()

    @classmethod
    def decode(cls, data: bytes, expected: "Readiness") -> "Readiness":
        """The peer's object, refused unless it names this run, this key and the peer."""
        try:
            fields = json.loads(data)
            peer = cls(**fields)
        except (ValueError, TypeError) as exc:
            raise RaceError("the peer's coordination object is corrupt") from exc
        same = (peer.run_id, peer.writer, peer.target) == (
            expected.run_id,
            peer_of(expected.writer),
            expected.target,
        )
        if not same or not _SHA256.fullmatch(str(peer.candidate_sha256)):
            raise RaceError("the peer's coordination object names another run, writer or key")
        return peer


@dataclass
class Barrier:
    """Meets the peer writer through two coordination objects in the bucket."""

    bucket: Bucket
    run_id: str
    writer: str
    timeout: float
    poll: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id):
            raise RaceError("the run id must be lowercase letters, digits and dashes")
        peer_of(self.writer)

    def key(self, target: str, writer: str) -> str:
        return f"{BARRIER_PREFIX}/{self.run_id}/{sha256(target.encode())[:32]}/{writer}.json"

    def meet(self, target: str, candidate_sha256: str) -> Readiness:
        """Advertise readiness for ``target``, then wait for the peer's; the peer's."""
        mine = Readiness(self.run_id, self.writer, target, candidate_sha256)
        reply = self.bucket.put_if_absent(self.key(target, self.writer), mine.encode())
        if reply.status in HELD:
            raise RaceError("this writer's coordination object already exists: rerun the race")
        if reply.status != CREATED:
            raise RaceError(f"could not advertise readiness (PUT answered {reply.status})")
        return self._await_peer(mine)

    def _await_peer(self, mine: Readiness) -> Readiness:
        deadline = self.clock() + self.timeout
        peer_key = self.key(mine.target, peer_of(self.writer))
        while True:
            reply = self.bucket.get(peer_key)
            if reply.status == FOUND:
                return Readiness.decode(reply.body, mine)
            if reply.status != ABSENT:
                raise RaceError(
                    f"could not read the peer's readiness (GET answered {reply.status})"
                )
            if self.clock() >= deadline:
                raise RaceError(
                    f"writer {peer_of(self.writer)} never reached the barrier within "
                    f"{self.timeout:g}s; no competing PUT was sent"
                )
            self.sleep(self.poll)


@dataclass
class Attempt:
    """What this writer saw at the target key."""

    target: str | None = None
    candidate_sha256: str | None = None
    peer_candidate_sha256: str | None = None
    put_status: int | None = None


@dataclass
class RacingClient:
    """An R2 client whose first cache-entry ``PUT`` waits at the barrier."""

    inner: Bucket
    barrier: Barrier
    attempt: Attempt = field(default_factory=Attempt)

    def put_if_absent(self, key: str, data: bytes) -> Reply:
        if self.attempt.target is not None or key.startswith(BARRIER_PREFIX):
            return self.inner.put_if_absent(key, data)
        candidate = sha256(data)
        peer = self.barrier.meet(key, candidate)
        self.attempt.target, self.attempt.candidate_sha256 = key, candidate
        self.attempt.peer_candidate_sha256 = peer.candidate_sha256
        reply = self.inner.put_if_absent(key, data)
        self.attempt.put_status = reply.status
        return reply

    def get(self, key: str) -> Reply:
        return self.inner.get(key)


def judge(a: dict[str, object], b: dict[str, object]) -> list[str]:
    """Every reason the two writers' evidence fails to prove the race; empty if it holds."""
    failures = _same_race(a, b) + _competed(a, b)
    if failures:
        return failures
    winner, loser = (a, b) if a["put_status"] == CREATED else (b, a)
    return _adopted(winner, loser) + _agreed(a, b)


def _same_race(a: dict[str, object], b: dict[str, object]) -> list[str]:
    failures = []
    if (a.get("writer"), b.get("writer")) != WRITERS:
        failures.append("evidence must come from writer a and writer b")
    if a.get("run_id") != b.get("run_id"):
        failures.append("the writers ran in different races")
    if a.get("target") is None or a.get("target") != b.get("target"):
        failures.append("the writers did not attempt the same key")
    return failures


def _competed(a: dict[str, object], b: dict[str, object]) -> list[str]:
    failures = []
    if a.get("candidate_sha256") == b.get("candidate_sha256"):
        failures.append("the writers' candidates are equal: nothing was contested")
    crossed = (a.get("peer_candidate_sha256"), b.get("peer_candidate_sha256"))
    if crossed != (b.get("candidate_sha256"), a.get("candidate_sha256")):
        failures.append("a writer did not see the other's candidate at the barrier")
    statuses = sorted([a.get("put_status"), b.get("put_status")], key=str)
    if statuses not in ([CREATED, 409], [CREATED, 412]):
        failures.append(f"expected one PUT answered 200 and one 412/409, got {statuses}")
    return failures


def _adopted(winner: dict[str, object], loser: dict[str, object]) -> list[str]:
    failures = []
    committed = winner["candidate_sha256"]
    if winner.get("remote_sha256") != committed or loser.get("remote_sha256") != committed:
        failures.append("the bucket does not hold the winner's candidate")
    if winner.get("local_sha256") != committed:
        failures.append("the winner's local copy is not its own candidate")
    if loser.get("local_sha256") != committed:
        failures.append("the loser did not adopt the winner's bytes")
    return failures


def _agreed(a: dict[str, object], b: dict[str, object]) -> list[str]:
    if a.get("sidecar_sha256") != b.get("sidecar_sha256"):
        return ["the two writers wrote different sidecars"]
    return []
