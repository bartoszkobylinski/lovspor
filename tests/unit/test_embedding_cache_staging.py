"""The G3 staging check, rehearsed offline, and the workflow that stays inert (#802).

``scripts/ops/embedding_cache_g3.py`` is what the staging workflow runs on
GitHub-hosted runners against the staging bucket, once the owner approves G3
(ADR-0022). Here it runs against a loopback mocked bucket
(:func:`tests.unit.r2_fakes.serve`). Each step is its own process with its own
empty disk, so the steps meet only in the bucket, as the runners would.

The workflow tests read ``.github/workflows/embedding-cache-staging.yml`` as
data and check that:

* it can only be dispatched by hand;
* every job is gated on the owner's variable, on ``main`` and on ``confirm=G3``;
* the secrets live only in the staging environment;
* nothing in it can reach a paid provider.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit import r2_fakes

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "ops" / "embedding_cache_g3.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "embedding-cache-staging.yml"
_GATE = (
    "vars.EMBEDDING_CACHE_STAGING_ENABLED == 'true' && github.ref == 'refs/heads/main' "
    "&& inputs.confirm == 'G3'"
)


@pytest.fixture
def bucket() -> r2_fakes.FakeBucket:
    return r2_fakes.FakeBucket()


@pytest.fixture
def endpoint(bucket: r2_fakes.FakeBucket) -> Iterator[str]:
    with r2_fakes.serve(bucket) as url:
        yield url


def _step(endpoint: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_EMBEDDING_CACHE")}
    env.pop("OPENAI_API_KEY", None)
    env.update(
        LOVSPOR_EMBEDDING_CACHE_REMOTE="r2",
        LOVSPOR_EMBEDDING_CACHE_R2_BUCKET=r2_fakes.BUCKET,
        LOVSPOR_EMBEDDING_CACHE_R2_ACCOUNT_ID=r2_fakes.ACCOUNT_ID,
        LOVSPOR_EMBEDDING_CACHE_R2_ACCESS_KEY_ID=r2_fakes.KEY_ID,
        LOVSPOR_EMBEDDING_CACHE_R2_SECRET_ACCESS_KEY=r2_fakes.SECRET,
        LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS="32",
    )
    command = [sys.executable, str(_SCRIPT), *args, "--endpoint", endpoint]
    return subprocess.run(
        command, env=env, capture_output=True, text=True, check=False, timeout=300
    )


def test_crash_then_resume_passes_on_separate_processes(
    endpoint: str, bucket: r2_fakes.FakeBucket
) -> None:
    crashed = _step(endpoint, "crash", "--run-id", "t1")
    assert crashed.returncode == 0, crashed.stderr
    assert bucket.puts() == 2, "two paid units were committed before the kill"
    resumed = _step(endpoint, "resume", "--run-id", "t1")
    assert resumed.returncode == 0, resumed.stderr
    assert "paid 2, then 0" in resumed.stdout


def test_resume_without_a_crash_fails_because_it_pays_everything(endpoint: str) -> None:
    resumed = _step(endpoint, "resume", "--run-id", "never-crashed")
    assert resumed.returncode != 0
    assert "resume paid 4" in resumed.stderr


def test_race_writers_with_different_vectors_agree_and_a_third_pays_nothing(
    endpoint: str, tmp_path: Path
) -> None:
    outputs = [tmp_path / "a.out", tmp_path / "b.out"]
    for writer, output in zip("ab", outputs, strict=True):
        done = _step(
            endpoint, "race", "--run-id", "t2", "--writer", writer, "--github-output", str(output)
        )
        assert done.returncode == 0, done.stderr
    digests = [o.read_text().strip().removeprefix("digest=") for o in outputs]
    checked = _step(endpoint, "race-check", "--run-id", "t2", "--digests", *digests)
    assert checked.returncode == 0, checked.stderr
    assert "third paid 0" in checked.stdout


def test_race_check_fails_on_different_digests(endpoint: str) -> None:
    checked = _step(endpoint, "race-check", "--run-id", "t3", "--digests", "aa", "bb")
    assert checked.returncode != 0
    assert "different sidecars" in checked.stderr


def test_the_script_refuses_to_run_without_the_remote_cache(endpoint: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_EMBEDDING_CACHE")}
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "pay", "--run-id", "t4", "--endpoint", endpoint],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode != 0
    assert "LOVSPOR_EMBEDDING_CACHE_REMOTE=r2" in done.stderr


def test_no_secret_reaches_the_script_output(endpoint: str) -> None:
    done = _step(endpoint, "crash", "--run-id", "t5")
    shown = done.stdout + done.stderr
    for secret in (r2_fakes.SECRET, r2_fakes.KEY_ID, r2_fakes.ACCOUNT_ID):
        assert secret not in shown


def _workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def test_the_staging_workflow_is_dispatch_only() -> None:
    triggers = _workflow()[True]  # YAML 1.1 reads the key `on` as True
    assert set(triggers) == {"workflow_dispatch"}
    assert triggers["workflow_dispatch"]["inputs"]["confirm"]["required"] is True


def test_every_staging_job_is_gated_and_bound_to_the_staging_environment() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == {"crash", "resume", "race-a", "race-b", "race-check"}
    for name, job in jobs.items():
        assert job["if"] == _GATE, name
        assert job["environment"] == "embedding-cache-staging", name
        assert job["timeout-minutes"] <= 30, name


def test_the_staging_workflow_reads_only_contents() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_the_staging_workflow_never_names_a_paid_provider_or_the_production_bucket() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "OPENAI" not in text
    assert "sync.yml" not in text
    assert "embedding-cache-staging" in text
    assert "environment: embedding-cache\n" not in text


def test_the_scheduled_sync_does_not_enable_the_remote_cache() -> None:
    sync = (_ROOT / ".github" / "workflows" / "sync.yml").read_text(encoding="utf-8")
    assert "LOVSPOR_EMBEDDING_CACHE" not in sync
    assert "R2_" not in sync
