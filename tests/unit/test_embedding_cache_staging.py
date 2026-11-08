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

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
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


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOVSPOR_")}
    env.pop("OPENAI_API_KEY", None)
    env.update(
        LOVSPOR_EMBEDDING_CACHE_REMOTE="r2",
        LOVSPOR_EMBEDDING_CACHE_R2_BUCKET=r2_fakes.BUCKET,
        LOVSPOR_EMBEDDING_CACHE_R2_ACCOUNT_ID=r2_fakes.ACCOUNT_ID,
        LOVSPOR_EMBEDDING_CACHE_R2_ACCESS_KEY_ID=r2_fakes.KEY_ID,
        LOVSPOR_EMBEDDING_CACHE_R2_SECRET_ACCESS_KEY=r2_fakes.SECRET,
        LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS="32",
        LOVSPOR_G3_BARRIER_POLL_SECONDS="0.05",
        **extra,
    )
    return env


def _writer(
    endpoint: str, run: str, writer: str, output: Path, timeout: str
) -> subprocess.Popen[str]:
    """One race writer as its own process, started without waiting for it."""
    command = [sys.executable, str(_SCRIPT), "race", "--run-id", run, "--writer", writer]
    command += ["--github-output", str(output), "--endpoint", endpoint]
    env = _env(LOVSPOR_G3_BARRIER_TIMEOUT_SECONDS=timeout)
    return subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _finish(process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
    stdout, stderr = process.communicate(timeout=300)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _race(endpoint: str, run: str, outputs: list[Path]) -> list[subprocess.CompletedProcess[str]]:
    """Both writers started together; each waits for the other at the barrier."""
    started = [_writer(endpoint, run, w, o, "60") for w, o in zip("ab", outputs, strict=True)]
    return [_finish(process) for process in started]


def _evidence(output: Path) -> str:
    lines = output.read_text().splitlines() if output.exists() else []
    found = [line.removeprefix("evidence=") for line in lines if line.startswith("evidence=")]
    return found[0] if found else "{}"


def _check(endpoint: str, run: str, outputs: list[Path]) -> subprocess.CompletedProcess[str]:
    return _step(endpoint, "race-check", "--run-id", run, "--evidence", *map(_evidence, outputs))


def test_concurrent_writers_race_one_put_wins_and_the_loser_adopts(
    endpoint: str, tmp_path: Path
) -> None:
    outputs = [tmp_path / "a.out", tmp_path / "b.out"]
    done = _race(endpoint, "t2", outputs)
    for writer in done:
        assert writer.returncode == 0, writer.stderr
    a, b = (json.loads(_evidence(o)) for o in outputs)
    assert a["target"] == b["target"]
    assert a["candidate_sha256"] != b["candidate_sha256"]
    assert sorted([a["put_status"], b["put_status"]]) == [200, 412]
    winner, loser = (a, b) if a["put_status"] == 200 else (b, a)
    assert loser["local_sha256"] == winner["candidate_sha256"] != loser["candidate_sha256"]
    checked = _check(endpoint, "t2", outputs)
    assert checked.returncode == 0, checked.stderr
    assert "third paid 0" in checked.stdout


def test_the_old_sequential_schedule_is_not_g3_race_evidence(endpoint: str, tmp_path: Path) -> None:
    """Writer a runs alone to the end, then writer b: what two jobs sharing only
    ``needs:`` may do. Before the barrier this passed race-check (equal sidecars,
    a third runner paying 0) without any competing PUT."""
    outputs = [tmp_path / "a.out", tmp_path / "b.out"]
    first = _finish(_writer(endpoint, "t3", "a", outputs[0], "1"))
    assert first.returncode != 0, "writer a finished alone: a sequential schedule passed"
    assert "never reached the barrier" in first.stderr
    second = _finish(_writer(endpoint, "t3", "b", outputs[1], "1"))
    checked = _check(endpoint, "t3", outputs)
    assert checked.returncode != 0
    assert "G3 race evidence refused" in checked.stderr, (second.stderr, checked.stderr)


def test_a_missing_peer_fails_at_the_deadline_before_the_competing_put(
    endpoint: str, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    alone = _finish(_writer(endpoint, "t4", "a", tmp_path / "a.out", "1"))
    assert alone.returncode != 0
    assert "writer b never reached the barrier within 1s; no competing PUT was sent" in alone.stderr
    puts = [key for method, key in bucket.calls if method == "PUT"]
    assert len(puts) == 1
    assert puts[0].startswith("lovspor-embedding-cache/g3-barrier/t4/")
    assert puts[0].endswith("/a.json")


def _plant_peer(bucket: r2_fakes.FakeBucket, body: Callable[[str], bytes]) -> None:
    """When writer a advertises readiness, put ``body(target-dir)`` where b's object goes."""

    def plant(key: str) -> None:
        if key.endswith("/a.json"):
            bucket.objects[key.removesuffix("a.json") + "b.json"] = body(key)

    bucket.before_put = plant


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        (lambda _key: b"not json", "the peer's coordination object is corrupt"),
        (
            lambda _key: json.dumps(
                {
                    "run_id": "t5",
                    "writer": "b",
                    "target": "lovspor-embedding-cache/other",
                    "candidate_sha256": "0" * 64,
                }
            ).encode(),
            "names another run, writer or key",
        ),
        (
            lambda _key: json.dumps(
                {"run_id": "other", "writer": "b", "target": "x", "candidate_sha256": "0" * 64}
            ).encode(),
            "names another run, writer or key",
        ),
    ],
    ids=["corrupt", "other-key", "other-run"],
)
def test_a_corrupt_or_mismatched_peer_object_stops_before_the_competing_put(
    endpoint: str,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    body: Callable[[str], bytes],
    refusal: str,
) -> None:
    _plant_peer(bucket, body)
    done = _finish(_writer(endpoint, "t5", "a", tmp_path / "a.out", "5"))
    assert done.returncode != 0
    assert refusal in done.stderr
    assert all("/g3-barrier/" in key for method, key in bucket.calls if method == "PUT")


def test_a_reused_coordination_object_is_refused(
    endpoint: str, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    def occupy(key: str) -> None:
        if key.endswith("/a.json"):
            bucket.objects.setdefault(key, b"{}")

    bucket.before_put = occupy
    done = _finish(_writer(endpoint, "t6", "a", tmp_path / "a.out", "5"))
    assert done.returncode != 0
    assert "coordination object already exists" in done.stderr


def test_race_check_refuses_disagreeing_or_tampered_evidence(endpoint: str, tmp_path: Path) -> None:
    outputs = [tmp_path / "a.out", tmp_path / "b.out"]
    for writer in _race(endpoint, "t7", outputs):
        assert writer.returncode == 0, writer.stderr
    a, b = (json.loads(_evidence(o)) for o in outputs)
    loser = a if a["put_status"] != 200 else b
    loser["local_sha256"] = loser["candidate_sha256"]
    loser["sidecar_sha256"] = "0" * 64
    tampered = [json.dumps(a), json.dumps(b)]
    checked = _step(endpoint, "race-check", "--run-id", "t7", "--evidence", *tampered)
    assert checked.returncode != 0
    assert "the loser did not adopt the winner's bytes" in checked.stderr
    assert "the two writers wrote different sidecars" in checked.stderr


def test_equal_digests_alone_are_not_race_evidence(endpoint: str) -> None:
    same = json.dumps({"sidecar_sha256": "a" * 64})
    checked = _step(endpoint, "race-check", "--run-id", "t8", "--evidence", same, same)
    assert checked.returncode != 0
    assert "G3 race evidence refused" in checked.stderr


def test_no_secret_reaches_any_race_output(endpoint: str, tmp_path: Path) -> None:
    outputs = [tmp_path / "a.out", tmp_path / "b.out"]
    shown = "".join(r.stdout + r.stderr for r in _race(endpoint, "t9", outputs))
    shown += "".join(o.read_text() for o in outputs if o.exists())
    shown += _finish(_writer(endpoint, "t10", "a", tmp_path / "c.out", "1")).stderr
    for secret in (r2_fakes.SECRET, r2_fakes.KEY_ID, r2_fakes.ACCOUNT_ID, "127.0.0.1"):
        assert secret not in shown


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


def test_both_race_writers_wait_for_approval_together_and_feed_race_check() -> None:
    """Neither race job waits for another job, so both are pending as soon as the
    run starts and one environment review releases them together; race-check
    judges their evidence, never digests alone."""
    workflow = _workflow()
    jobs = workflow["jobs"]
    for writer in ("race-a", "race-b"):
        assert "needs" not in jobs[writer], writer
        assert jobs[writer]["outputs"] == {"evidence": "${{ steps.g3.outputs.evidence }}"}
    assert jobs["race-check"]["needs"] == ["race-a", "race-b"]
    check = jobs["race-check"]["steps"][-1]
    assert "--evidence" in check["run"]
    assert "--digests" not in _WORKFLOW.read_text(encoding="utf-8")


def test_the_barrier_deadline_ends_before_the_race_job_is_killed() -> None:
    workflow = _workflow()
    deadline = float(workflow["env"]["LOVSPOR_G3_BARRIER_TIMEOUT_SECONDS"])
    for writer in ("race-a", "race-b"):
        assert 0 < deadline < workflow["jobs"][writer]["timeout-minutes"] * 60 / 2, writer
