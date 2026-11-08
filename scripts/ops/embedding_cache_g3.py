"""G3 cross-runner checks of the remote embedding cache, with a fake provider (#802).

Run by ``.github/workflows/embedding-cache-staging.yml`` against the staging
bucket only, once the owner has passed G1 and approved G3 (ADR-0022). It never
calls a paid provider. The "provider" is a deterministic fake, and its
descriptor names this workflow run, so every run writes fresh keys.

Steps, each on its own runner with an empty disk:

* ``crash``: a child sync SIGKILLs itself inside request k+1, after k
  checkpoints. Asserts it was killed and paid exactly k.
* ``resume``: on a fresh runner, pays exactly n - k. A third sync pays 0. The
  sidecar equals an uncached run's.
* ``race --writer a|b``: two runners sync the same new keys with providers
  that return **different** valid vectors. Each holds its first cache-entry
  ``PUT`` at a barrier until the other has advertised its own candidate for the
  same key (``embedding_cache_g3_race.py``), then sends it. Each prints its
  evidence: the key, both candidates' digests, its ``PUT`` status, the bucket's
  and its local copy's digests, its sidecar's digest. A writer whose peer never
  arrives fails at ``LOVSPOR_G3_BARRIER_TIMEOUT_SECONDS`` (default 600).
* ``race-check``: the evidence shows one ``PUT`` answered 200 and one 412/409,
  the loser holding the winner's bytes and both the same sidecar; a third
  runner then pays 0 and writes that sidecar. Equal sidecars alone are refused.

Credentials come from the ``LOVSPOR_EMBEDDING_CACHE_*`` environment, exactly as
a sync reads them. ``--endpoint`` points the store at a loopback fake. It exists
for this script's offline tests, never for the workflow.
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
from embedding_cache_g3_race import (
    CREATED,
    WRITERS,
    Barrier,
    RaceError,
    RacingClient,
    judge,
    sha256,
)

from lovspor.embeddings.cache import CachingEmbedder, read_policy_from_env
from lovspor.embeddings.cache_remote import REMOTE_PREFIX, RemoteEntryStore, with_remote
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.model import EmbeddingModel
from lovspor.embeddings.provider import esi_for_descriptor
from lovspor.embeddings.r2_client import R2Client
from lovspor.sync.orchestrator import _write_embeddings_for_doc

REQUESTS = 4  # SECTIONS inputs, two per request
SECTIONS = 7
KILL_AFTER = 2
BARRIER_TIMEOUT = ("LOVSPOR_G3_BARRIER_TIMEOUT_SECONDS", 600.0)
BARRIER_POLL = ("LOVSPOR_G3_BARRIER_POLL_SECONDS", 1.0)
MAX_WAIT = 3600.0
_RENDERED = "---\ntitle: g3\n---\n\n" + "".join(
    f"### § {i}. Overskrift\n\nSyntetisk tekst nummer {i}.\n\n" for i in range(1, SECTIONS + 1)
)


class FakeProvider:
    """Deterministic per text and ``noise``; appends one fsynced ledger line per request."""

    def __init__(self, run_id: str, noise: str, ledger: Path, kill_at: int) -> None:
        self.descriptor = f"provider=openai;model=g3-staging-{run_id};dim=4;endpoint=default"
        self.space_id = esi_for_descriptor(self.descriptor)
        self._noise, self._ledger, self._kill_at = noise, ledger, kill_at
        self.requests = 0

    def get_dimension(self) -> int:
        return 4

    def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
        return [(i, min(i + 2, len(texts))) for i in range(0, len(texts), 2)]

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.requests == self._kill_at:
            os.kill(os.getpid(), signal.SIGKILL)
        self.requests += 1
        digests = [hashlib.sha256((self._noise + t).encode()).digest()[:4] for t in texts]
        with self._ledger.open("a") as handle:
            handle.write(f"{len(texts)}\n")
            handle.flush()
            os.fsync(handle.fileno())
        matrix = np.asarray([np.frombuffer(d, np.int8) for d in digests], np.float32) + 0.5
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def pay(args: argparse.Namespace, wrap: "Wrap | None" = None) -> dict[str, object]:
    """One synthetic sync of one document on an empty runner disk; its cost and sidecar."""
    work = Path(tempfile.mkdtemp(prefix="g3-"))
    ledger = work / "ledger"
    provider = FakeProvider(args.run_id, args.noise, ledger, args.kill_at)
    corpus = work / "lovverk"
    corpus.mkdir()
    embedder = _cached(provider, work / "cache", args.endpoint, wrap) if args.cached else provider
    path, _ = _write_embeddings_for_doc(corpus, "gjeldende-lover", "g3", _RENDERED, embedder)
    paid = len(ledger.read_text().splitlines()) if ledger.exists() else 0
    return {"paid": paid, "sidecar_sha256": sha256(path.read_bytes()), "cache": str(work / "cache")}


Wrap = Callable[[R2Client], object]


def _cached(
    provider: FakeProvider, root: Path, endpoint: str | None, wrap: Wrap | None
) -> EmbeddingModel:
    local = LocalEntryStore.open(root, (), read_policy_from_env())
    store = with_remote(local)
    if not isinstance(store, RemoteEntryStore):
        raise SystemExit("G3 needs LOVSPOR_EMBEDDING_CACHE_REMOTE=r2 and the R2 credentials")
    client = store.client
    if endpoint is not None:
        client = R2Client(client.credentials, endpoint=endpoint)
    wrapped = client if wrap is None else wrap(client)
    return CachingEmbedder(provider, RemoteEntryStore(local, wrapped), provider.space_id)  # type: ignore[arg-type]


def _child(args: argparse.Namespace, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, __file__, "pay", "--run-id", args.run_id, *extra]
    if args.endpoint:
        command += ["--endpoint", args.endpoint]
    # This script with its own fixed arguments: no shell, no outside input.
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)  # noqa: S603


def _sync(args: argparse.Namespace, *extra: str) -> dict[str, object]:
    done = _child(args, *extra)
    if done.returncode != 0:
        raise SystemExit(f"synthetic sync failed (exit {done.returncode}):\n{done.stderr}")
    result: dict[str, object] = json.loads(done.stdout)
    return result


def crash(args: argparse.Namespace) -> None:
    done = _child(args, "--kill-at", str(KILL_AFTER))
    _expect(done.returncode == -signal.SIGKILL, f"child was not killed (exit {done.returncode})")
    print(f"crash: killed after {KILL_AFTER} checkpoints")


def resume(args: argparse.Namespace) -> None:
    reference = _sync(args, "--uncached")
    resumed = _sync(args)
    _expect(resumed["paid"] == REQUESTS - KILL_AFTER, f"resume paid {resumed['paid']}")
    again = _sync(args)
    _expect(again["paid"] == 0, f"third run paid {again['paid']}")
    same = {reference["sidecar_sha256"], resumed["sidecar_sha256"], again["sidecar_sha256"]}
    _expect(len(same) == 1, "sidecars differ from the uncached run")
    print(f"resume: paid {resumed['paid']}, then 0; sidecar equals the uncached one")


def race(args: argparse.Namespace) -> None:
    """One writer of the race; its evidence on stdout and in ``--github-output``."""
    racing: list[RacingClient] = []

    def wrap(client: R2Client) -> RacingClient:
        barrier = Barrier(client, args.run_id, args.writer, _seconds(BARRIER_TIMEOUT))
        barrier.poll = _seconds(BARRIER_POLL)
        racing.append(RacingClient(client, barrier))
        return racing[0]

    args.noise = args.writer
    try:
        result = pay(args, wrap)
    except RaceError as exc:
        raise SystemExit(f"G3 race failed (writer {args.writer}): {exc}") from None
    evidence = _evidence(args, racing[0], result)
    print(f"race {args.writer}: paid {result['paid']}; evidence {json.dumps(evidence)}")
    if args.github_output:
        with Path(args.github_output).open("a") as handle:
            handle.write(f"evidence={json.dumps(evidence, sort_keys=True)}\n")


def _evidence(
    args: argparse.Namespace, racing: RacingClient, result: dict[str, object]
) -> dict[str, object]:
    attempt = racing.attempt
    _expect(attempt.target is not None, "this writer never sent a cache PUT")
    target = str(attempt.target)
    remote = racing.inner.get(target)
    _expect(remote.status == CREATED, f"the target key could not be read back ({remote.status})")
    local = Path(str(result["cache"])) / target.removeprefix(f"{REMOTE_PREFIX}/")
    return {
        "writer": args.writer,
        "run_id": args.run_id,
        "target": target,
        "candidate_sha256": attempt.candidate_sha256,
        "peer_candidate_sha256": attempt.peer_candidate_sha256,
        "put_status": attempt.put_status,
        "remote_sha256": sha256(remote.body),
        "local_sha256": sha256(local.read_bytes()),
        "sidecar_sha256": result["sidecar_sha256"],
        "paid": result["paid"],
    }


def race_check(args: argparse.Namespace) -> None:
    a, b = _parsed(args.evidence)
    failures = judge(a, b)
    if failures:
        raise SystemExit("G3 race evidence refused:\n" + "\n".join(f"- {f}" for f in failures))
    third = _sync(args, "--noise", "c")
    _expect(third["paid"] == 0, f"third runner paid {third['paid']}")
    _expect(third["sidecar_sha256"] == a["sidecar_sha256"], "third runner's sidecar differs")
    winner = a["writer"] if a["put_status"] == CREATED else b["writer"]
    print(
        f"race: writer {winner}'s PUT won, the other was answered 412/409 and adopted it; "
        "both wrote the same sidecar; third paid 0"
    )


def _parsed(evidence: list[str]) -> tuple[dict[str, object], dict[str, object]]:
    _expect(len(evidence) == len(WRITERS), "race-check needs the evidence of writer a and writer b")
    try:
        a, b = (json.loads(text) for text in evidence)
    except ValueError:
        raise SystemExit("G3 race evidence refused:\n- evidence is not JSON") from None
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise SystemExit("G3 race evidence refused:\n- evidence is not a JSON object")
    return a, b


def _seconds(setting: tuple[str, float]) -> float:
    name, default = setting
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        raise SystemExit(f"{name} must be a number of seconds") from None
    _expect(0 < value <= MAX_WAIT, f"{name} must be between 0 and {MAX_WAIT:g} seconds")
    return value


def _expect(condition: bool, failure: str) -> None:
    if not condition:
        raise SystemExit(f"G3 check failed: {failure}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["pay", "crash", "resume", "race", "race-check"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--writer", default="a")
    parser.add_argument("--noise", default="")
    parser.add_argument("--kill-at", type=int, default=-1)
    parser.add_argument("--uncached", dest="cached", action="store_false")
    parser.add_argument("--evidence", nargs="*", default=[])
    parser.add_argument("--github-output", default=None)
    parser.add_argument("--endpoint", default=None)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.step == "pay":
        print(json.dumps(pay(args)))
        return
    {"crash": crash, "resume": resume, "race": race, "race-check": race_check}[args.step](args)


if __name__ == "__main__":
    main()
