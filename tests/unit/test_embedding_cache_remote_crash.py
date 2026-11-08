"""Crash and restart in separate processes, against a mocked R2 bucket (#802, M4).

Each run is its own Python process with its **own, empty** local cache
directory and corpus workspace: what a fresh GitHub-hosted runner would have.
The only thing the runs share is a mocked bucket. :func:`r2_fakes.serve` serves
it on a loopback port; it implements R2's conditional ``PUT`` and ``GET``, and
nothing reaches Cloudflare.

The child process SIGKILLs itself inside a paid request, after k checkpoints.
The run that follows must pay exactly n - k requests, and a third run 0, with
sidecars byte-identical to an uncached run.

This is the offline rehearsal of the G3 cross-runner test, not a substitute
for it. #802 closes only on a real cross-runner recovery (ADR-0022).
"""

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit import r2_fakes

_CHILD = r"""
import hashlib, os, signal, sys
from pathlib import Path
import numpy as np
from lovspor.embeddings.cache import CachingEmbedder, read_policy_from_env
from lovspor.embeddings.cache_remote import RemoteEntryStore
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.r2_client import R2Client, R2Credentials
from lovspor.embeddings.provider import esi_for_descriptor
from lovspor.settings import Settings
from lovspor.sync.orchestrator import _write_embeddings_for_doc

workspace, ledger, endpoint = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
kill_at, cached = int(sys.argv[4]), sys.argv[5] == "cached"
DESCRIPTOR = "provider=openai;model=fake-crash;dim=4;endpoint=default"


class Provider:
    descriptor = DESCRIPTOR
    space_id = esi_for_descriptor(DESCRIPTOR)
    requests = 0

    def get_dimension(self):
        return 4

    def plan_requests(self, texts):
        return [(i, min(i + 2, len(texts))) for i in range(0, len(texts), 2)]

    def encode(self, texts):
        if self.requests == kill_at:
            os.kill(os.getpid(), signal.SIGKILL)
        self.requests += 1
        rows = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:4], np.int8) for t in texts]
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
embedder = Provider()
if cached:
    credentials = R2Credentials(
        account_id=os.environ["FAKE_R2_ACCOUNT"],
        bucket=os.environ["FAKE_R2_BUCKET"],
        access_key_id=os.environ["FAKE_R2_KEY_ID"],
        secret_access_key=os.environ["FAKE_R2_SECRET"],
    )
    local = LocalEntryStore.open(workspace / "local-cache", (), read_policy_from_env())
    store = RemoteEntryStore(local, R2Client(credentials, endpoint=endpoint))
    embedder = CachingEmbedder(Provider(), store, Provider.space_id)
path, _ = _write_embeddings_for_doc(
    settings.lovverk_repo_path, "gjeldende-lover", "lov", rendered, embedder
)
sys.stdout.write(path.read_bytes().hex())
"""

_REQUESTS = 4  # 7 inputs, two per request


@pytest.fixture
def child(tmp_path: Path) -> Path:
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    return script


@pytest.fixture
def bucket() -> r2_fakes.FakeBucket:
    return r2_fakes.FakeBucket()


@pytest.fixture
def endpoint(bucket: r2_fakes.FakeBucket) -> Iterator[str]:
    with r2_fakes.serve(bucket) as url:
        yield url


def _run(
    child: Path, endpoint: str, workspace: Path, kill_at: int = -1, cached: bool = True
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_EMBEDDING_CACHE")}
    env.update(
        FAKE_R2_ACCOUNT=r2_fakes.ACCOUNT_ID,
        FAKE_R2_BUCKET=r2_fakes.BUCKET,
        FAKE_R2_KEY_ID=r2_fakes.KEY_ID,
        FAKE_R2_SECRET=r2_fakes.SECRET,
    )
    ledger = workspace.with_suffix(".ledger")
    mode = "cached" if cached else "uncached"
    return subprocess.run(
        [sys.executable, str(child), str(workspace), str(ledger), endpoint, str(kill_at), mode],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _paid(workspace: Path) -> int:
    ledger = workspace.with_suffix(".ledger")
    return len(ledger.read_text().splitlines()) if ledger.exists() else 0


def test_sigkill_then_a_fresh_runner_pays_only_the_misses_then_nothing(
    child: Path, endpoint: str, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    reference = _run(child, endpoint, tmp_path / "uncached", cached=False)
    assert reference.returncode == 0, reference.stderr

    killed = _run(child, endpoint, tmp_path / "runner-1", kill_at=2)
    assert killed.returncode == -signal.SIGKILL
    assert _paid(tmp_path / "runner-1") == 2
    assert bucket.puts() == 2, "both paid units were committed before the next request"

    resumed = _run(child, endpoint, tmp_path / "runner-2")
    assert resumed.returncode == 0, resumed.stderr
    assert _paid(tmp_path / "runner-2") == _REQUESTS - 2
    assert resumed.stdout == reference.stdout

    again = _run(child, endpoint, tmp_path / "runner-3")
    assert again.returncode == 0, again.stderr
    assert _paid(tmp_path / "runner-3") == 0
    assert again.stdout == reference.stdout


def test_sigkill_before_the_first_checkpoint_leaves_the_bucket_empty(
    child: Path, endpoint: str, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    killed = _run(child, endpoint, tmp_path / "runner-1", kill_at=0)
    assert killed.returncode == -signal.SIGKILL
    assert bucket.objects == {}
    resumed = _run(child, endpoint, tmp_path / "runner-2")
    assert resumed.returncode == 0, resumed.stderr
    assert _paid(tmp_path / "runner-2") == _REQUESTS


def test_a_corrupt_object_stops_the_fresh_runner_without_paying(
    child: Path, endpoint: str, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    _run(child, endpoint, tmp_path / "runner-1", kill_at=2)
    key = next(iter(bucket.objects))
    bucket.objects[key] = bucket.objects[key][:-1] + bytes([bucket.objects[key][-1] ^ 1])
    refused = _run(child, endpoint, tmp_path / "runner-2")
    assert refused.returncode != 0
    assert "remote entry refused" in refused.stderr
    assert _paid(tmp_path / "runner-2") == 0
    for secret in (r2_fakes.SECRET, r2_fakes.KEY_ID, r2_fakes.ACCOUNT_ID):
        assert secret not in refused.stderr + refused.stdout
