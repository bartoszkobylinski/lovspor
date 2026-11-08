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
* ``race --writer a|b``: two runners sync together with providers that return
  **different** valid vectors. Each prints its sidecar digest.
* ``race-check``: both digests are equal, and a third runner pays 0 and writes
  the same sidecar.

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
from pathlib import Path

import numpy as np

from lovspor.embeddings.cache import CachingEmbedder, read_policy_from_env
from lovspor.embeddings.cache_remote import RemoteEntryStore, with_remote
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.model import EmbeddingModel
from lovspor.embeddings.provider import esi_for_descriptor
from lovspor.embeddings.r2_client import R2Client
from lovspor.sync.orchestrator import _write_embeddings_for_doc

REQUESTS = 4  # SECTIONS inputs, two per request
SECTIONS = 7
KILL_AFTER = 2
WRITERS = 2  # race a and race b
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


def pay(args: argparse.Namespace) -> dict[str, object]:
    """One synthetic sync of one document on an empty runner disk; its cost and sidecar."""
    work = Path(tempfile.mkdtemp(prefix="g3-"))
    ledger = work / "ledger"
    provider = FakeProvider(args.run_id, args.noise, ledger, args.kill_at)
    corpus = work / "lovverk"
    corpus.mkdir()
    embedder = _cached(provider, work / "cache", args.endpoint) if args.cached else provider
    path, _ = _write_embeddings_for_doc(corpus, "gjeldende-lover", "g3", _RENDERED, embedder)
    paid = len(ledger.read_text().splitlines()) if ledger.exists() else 0
    return {"paid": paid, "sidecar_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _cached(provider: FakeProvider, root: Path, endpoint: str | None) -> EmbeddingModel:
    local = LocalEntryStore.open(root, (), read_policy_from_env())
    store = with_remote(local)
    if not isinstance(store, RemoteEntryStore):
        raise SystemExit("G3 needs LOVSPOR_EMBEDDING_CACHE_REMOTE=r2 and the R2 credentials")
    if endpoint is not None:
        store = RemoteEntryStore(local, R2Client(store.client.credentials, endpoint=endpoint))
    return CachingEmbedder(provider, store, provider.space_id)


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
    result = _sync(args, "--noise", args.writer)
    print(f"race {args.writer}: paid {result['paid']}, sidecar {result['sidecar_sha256']}")
    if args.github_output:
        with Path(args.github_output).open("a") as handle:
            handle.write(f"digest={result['sidecar_sha256']}\n")


def race_check(args: argparse.Namespace) -> None:
    digests = set(args.digests)
    _expect(
        len(args.digests) == WRITERS and len(digests) == 1,
        "the two writers wrote different sidecars",
    )
    third = _sync(args, "--noise", "c")
    _expect(third["paid"] == 0, f"third runner paid {third['paid']}")
    _expect({third["sidecar_sha256"]} == digests, "third runner's sidecar differs")
    print("race: one value per key; both writers and a third runner agree; third paid 0")


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
    parser.add_argument("--digests", nargs="*", default=[])
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
