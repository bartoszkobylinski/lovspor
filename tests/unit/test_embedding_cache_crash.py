"""Crash and concurrency regressions for the durable embedding cache (#802, M3).

Each run is a separate Python process with its own corpus workspace, sharing
only the cache directory — what a rerun on a new job would see if the store
survived the job. A local restart proves the store's crash semantics; it is
NOT proof of remote job durability (design §7, §8).

The provider is a fake that writes a ledger line, fsynced, for every request
it is "paid" for, so the cost of each run is read from the ledger rather than
from the process that may have died.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

_CHILD = r"""
import hashlib, os, signal, sys
from pathlib import Path
import numpy as np
from lovspor.embeddings.cache import with_embedding_cache
from lovspor.settings import Settings
from lovspor.sync.orchestrator import _write_embeddings_for_doc

workspace, ledger = Path(sys.argv[1]), Path(sys.argv[2])
kill_at, noise = int(sys.argv[3]), sys.argv[4]
DESCRIPTOR = "provider=openai;model=fake-crash;dim=4;endpoint=default"


class Provider:
    descriptor = DESCRIPTOR
    requests = 0

    @property
    def space_id(self):
        from lovspor.embeddings.provider import esi_for_descriptor
        return esi_for_descriptor(DESCRIPTOR)

    def get_dimension(self):
        return 4

    def plan_requests(self, texts):
        return [(i, min(i + 2, len(texts))) for i in range(0, len(texts), 2)]

    def encode(self, texts):
        if self.requests == kill_at:
            os.kill(os.getpid(), signal.SIGKILL)
        self.requests += 1
        seed = noise + str(os.getpid()) if noise != "-" else ""
        digests = [hashlib.sha256((seed + t).encode()).digest()[:4] for t in texts]
        rows = [np.frombuffer(d, np.int8) for d in digests]
        with open(ledger, "a") as handle:
            handle.write(f"{len(texts)}\n")
            handle.flush()
            os.fsync(handle.fileno())
        matrix = np.asarray(rows, dtype=np.float32) + 0.5
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


rendered = "---\ntitle: t\n---\n\n" + "".join(
    f"### § {i}. Overskrift\n\nTekst nummer {i} om regler.\n\n" for i in range(1, 8)
)
(workspace / "lovverk").mkdir(parents=True, exist_ok=True)
settings = Settings(data_dir=workspace / "data", lovverk_repo_path=workspace / "lovverk")
embedder = with_embedding_cache(Provider(), settings)
path, _ = _write_embeddings_for_doc(
    settings.lovverk_repo_path, "gjeldende-lover", "lov", rendered, embedder
)
sys.stdout.write(path.read_bytes().hex())
"""

_REQUESTS_PER_DOCUMENT = 4  # 7 inputs, two per request


@pytest.fixture
def child(tmp_path: Path) -> Path:
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    return script


def _run(
    child: Path, cache: Path | None, workspace: Path, kill_at: int = -1, noise: str = "-"
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_EMBEDDING_CACHE")}
    if cache is not None:
        env["LOVSPOR_EMBEDDING_CACHE_DIR"] = str(cache)
    ledger = workspace.with_suffix(".ledger")
    return subprocess.run(
        [sys.executable, str(child), str(workspace), str(ledger), str(kill_at), noise],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _paid(workspace: Path) -> int:
    ledger = workspace.with_suffix(".ledger")
    return len(ledger.read_text().splitlines()) if ledger.exists() else 0


def test_sigkill_after_a_checkpoint_then_a_new_process_pays_only_the_misses(
    child: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    killed = _run(child, cache, tmp_path / "job-1", kill_at=2)
    assert killed.returncode == -signal.SIGKILL
    assert _paid(tmp_path / "job-1") == 2
    assert not list((tmp_path / "job-1" / "lovverk").rglob("*.bin"))

    resumed = _run(child, cache, tmp_path / "job-2")
    assert resumed.returncode == 0, resumed.stderr
    assert _paid(tmp_path / "job-2") == _REQUESTS_PER_DOCUMENT - 2

    again = _run(child, cache, tmp_path / "job-3")
    assert again.returncode == 0, again.stderr
    assert _paid(tmp_path / "job-3") == 0
    assert again.stdout == resumed.stdout


def test_resumed_sidecar_equals_an_uncached_uninterrupted_one(child: Path, tmp_path: Path) -> None:
    reference = _run(child, None, tmp_path / "uncached")
    cache = tmp_path / "cache"
    _run(child, cache, tmp_path / "job-1", kill_at=3)
    resumed = _run(child, cache, tmp_path / "job-2")
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout == reference.stdout
    uncached_texts = (tmp_path / "uncached.ledger").read_text().split()
    assert sum(int(n) for n in uncached_texts) == 7


def test_sigkill_before_the_first_checkpoint_leaves_nothing_to_trust(
    child: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    killed = _run(child, cache, tmp_path / "job-1", kill_at=0)
    assert killed.returncode == -signal.SIGKILL
    assert not list(cache.rglob("*.lsec"))
    resumed = _run(child, cache, tmp_path / "job-2")
    assert _paid(tmp_path / "job-2") == _REQUESTS_PER_DOCUMENT
    assert resumed.returncode == 0


def test_cache_off_writes_nothing_outside_the_workspace(child: Path, tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    done = _run(child, None, tmp_path / "job")
    assert done.returncode == 0, done.stderr
    created = set(tmp_path.iterdir()) - before
    assert created == {tmp_path / "job", tmp_path / "job.ledger"}


def test_concurrent_writers_of_one_document_publish_one_value(child: Path, tmp_path: Path) -> None:
    """Provider output differs per process here, as it can for real (ADR-0005
    Amendment 1): both writers must still end with the same sidecar."""
    cache = tmp_path / "cache"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_EMBEDDING_CACHE")}
    env["LOVSPOR_EMBEDDING_CACHE_DIR"] = str(cache)
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(child),
                str(tmp_path / f"w{i}"),
                str(tmp_path / f"w{i}.ledger"),
                "-1",
                "noisy",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    outputs = [proc.communicate(timeout=120) for proc in procs]
    assert all(proc.returncode == 0 for proc in procs), [err for _out, err in outputs]
    assert len({out for out, _err in outputs}) == 1
    assert not [p for p in cache.rglob("*") if ".tmp." in p.name]
