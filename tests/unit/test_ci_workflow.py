"""Security invariants of the nightly sync workflow.

`.github/workflows/sync.yml` is the only place in this project where a secret
meets the network: it holds `LOVVERK_DEPLOY_KEY` (write access to the corpus
repo) and `OPENAI_API_KEY`, and pushes to github.com over SSH. How that job
decides to trust github.com, and what it installs alongside those secrets, are
security properties of the pipeline rather than formatting details — so they are
pinned here, where a regression shows up as a failing test instead of a silently
weaker nightly run.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync.yml"

# GitHub's published ed25519 host key, fingerprint
# SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU. Authoritative sources:
# https://api.github.com/meta (`.ssh_keys`, `.ssh_key_fingerprints`) and
# https://docs.github.com/en/authentication/keeping-your-account-secure/githubs-ssh-key-fingerprints
GITHUB_ED25519_HOST_KEY = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def _sync_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["sync"]["steps"]
    return steps


def _shell_lines() -> str:
    """Every line the sync job actually executes.

    Parsing as YAML drops the workflow's comments, and shell comments are dropped
    too. That matters: the SSH step's comment names `ssh-keyscan` to explain why
    it is *not* used, and a host key that appeared only inside a comment would
    satisfy a naive substring check over the raw file while trusting nothing.
    """
    lines = [
        line
        for step in _sync_steps()
        for line in str(step.get("run", "")).splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def test_sync_workflow_does_not_trust_github_on_first_use() -> None:
    """No `ssh-keyscan` runs: whatever answers at scan time gets trusted blindly.

    A host key harvested at run time is not a check — it is whatever the network
    handed back. In a job holding a write-capable deploy key, an impostor
    github.com could serve a tampered `lovverk` clone (whose diff a later honest
    run would push to the real repo) or blackhole the push entirely.
    """
    assert "ssh-keyscan" not in _shell_lines()


def test_sync_workflow_pins_githubs_published_host_key() -> None:
    """The key is written to known_hosts, so a mismatch fails closed.

    If GitHub ever rotates the ed25519 key, the clone breaks loudly rather than
    silently trusting the replacement — which is the whole point of pinning.
    Re-pin from https://api.github.com/meta when that happens.
    """
    assert GITHUB_ED25519_HOST_KEY in _shell_lines()


def test_sync_workflow_never_auto_accepts_an_unknown_host_key() -> None:
    """Belt to the pin's braces.

    `StrictHostKeyChecking yes` means that if the pinned entry is ever lost, ssh
    aborts instead of falling back to accepting an unknown key.
    """
    assert "StrictHostKeyChecking yes" in _shell_lines()


def test_sync_job_installs_runtime_dependencies_only() -> None:
    """`--no-dev` has to be on *every* uv command, not just `uv sync`.

    `uv run` re-syncs the environment before executing and re-installs the dev
    group by default — so `uv sync --frozen --no-dev` followed by a bare
    `uv run lovspor sync` puts mutmut, ruff, pytest and pre-commit right back
    (verified: 44 packages, then 76 again). This job holds two live secrets; the
    dev toolchain has no business being installed next to them, and the no-op
    version of this fix is easy to reintroduce without noticing.
    """
    uv_commands = [
        line.strip()
        for line in _shell_lines().splitlines()
        if line.strip().startswith(("uv sync", "uv run"))
    ]

    assert uv_commands, "expected the sync job to invoke uv"
    assert all("--no-dev" in cmd for cmd in uv_commands), uv_commands


def test_host_key_freshness_check_can_never_block_the_sync() -> None:
    """The rotation warning is diagnostics, not a gate.

    The pin itself is what enforces trust. If api.github.com were unreachable and
    this check were allowed to fail the job, a transient network blip at the
    wrong moment would take the nightly corpus sync offline for a reason that has
    nothing to do with the corpus.
    """
    check = next(s for s in _sync_steps() if str(s.get("name", "")).startswith("Warn if GitHub"))

    assert check["continue-on-error"] is True, check


# ------------------------------------------------------------------ freshness check
#
# The tests below execute the freshness check's real shell — the script lifted
# straight out of sync.yml — against a fake $HOME and a stubbed `curl`. Asserting
# that the step merely *exists*, or that it sets continue-on-error, says nothing
# about whether it can actually tell a rotated key from a current one.

_RSA_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6"
_STALE_ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMrqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


def _freshness_script() -> str:
    step = next(s for s in _sync_steps() if str(s.get("name", "")).startswith("Warn if GitHub"))
    return str(step["run"])


def _run_freshness_check(
    tmp_path: Path,
    known_hosts: str,
    published: list[str] | None,
) -> subprocess.CompletedProcess[str]:
    """Run the step's own script. `published=None` simulates an unreachable endpoint."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "known_hosts").write_text(known_hosts, encoding="utf-8")

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    curl = stub_bin / "curl"
    if published is None:
        curl.write_text("#!/bin/sh\nexit 6\n", encoding="utf-8")  # could not resolve host
    else:
        body = json.dumps({"ssh_keys": published})
        curl.write_text(f"#!/bin/sh\ncat <<'JSON'\n{body}\nJSON\n", encoding="utf-8")
    curl.chmod(0o755)

    env = {**os.environ, "HOME": str(home), "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", _freshness_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None,
    reason="the freshness check is shell; it needs bash and jq to execute",
)


def test_freshness_check_is_quiet_while_the_pin_is_still_published(tmp_path: Path) -> None:
    result = _run_freshness_check(
        tmp_path,
        known_hosts=f"{GITHUB_ED25519_HOST_KEY}\n",
        published=[GITHUB_ED25519_HOST_KEY.split(" ", 1)[1]],
    )

    assert "still in GitHub's published list" in result.stdout
    assert "::error::" not in result.stdout
    assert result.returncode == 0


def test_freshness_check_reports_a_rotation_when_the_pin_is_no_longer_published(
    tmp_path: Path,
) -> None:
    result = _run_freshness_check(
        tmp_path,
        known_hosts=f"{GITHUB_ED25519_HOST_KEY}\n",
        published=[_RSA_KEY],  # our ed25519 key is gone from the published list
    )

    assert "::error::" in result.stdout
    assert "rotated" in result.stdout
    assert result.returncode == 0


def test_freshness_check_still_sees_a_rotation_when_other_published_keys_are_present(
    tmp_path: Path,
) -> None:
    """The regression this check nearly shipped with.

    `grep -F` treats a multi-line pattern as alternatives. Comparing the whole of
    known_hosts against the published list therefore matched whenever *any* entry
    was published — so a stale ed25519 pin sitting next to GitHub's still-current
    RSA key reported "still published", going silent on precisely the rotation it
    exists to catch. The pinned key must be extracted on its own.
    """
    result = _run_freshness_check(
        tmp_path,
        known_hosts=f"github.com {_STALE_ED25519}\ngithub.com {_RSA_KEY}\n",
        published=[_RSA_KEY],  # RSA still published; our pinned ed25519 is not
    )

    assert "::error::" in result.stdout, result.stdout
    assert "still in GitHub's published list" not in result.stdout
    assert result.returncode == 0


def test_freshness_check_ignores_unrelated_hosts_in_known_hosts(tmp_path: Path) -> None:
    result = _run_freshness_check(
        tmp_path,
        known_hosts=f"gitlab.com {_STALE_ED25519}\n{GITHUB_ED25519_HOST_KEY}\n",
        published=[GITHUB_ED25519_HOST_KEY.split(" ", 1)[1]],
    )

    assert "still in GitHub's published list" in result.stdout, result.stdout
    assert result.returncode == 0


def test_freshness_check_warns_and_continues_when_the_endpoint_is_unreachable(
    tmp_path: Path,
) -> None:
    """A network blip is not a drift signal, and must never stop the corpus sync."""
    result = _run_freshness_check(
        tmp_path,
        known_hosts=f"{GITHUB_ED25519_HOST_KEY}\n",
        published=None,
    )

    assert "::warning::" in result.stdout
    assert "::error::" not in result.stdout
    assert result.returncode == 0


# ------------------------------------------------------------------ repair step
#
# `repair-embeddings` rewrites the corpus manifest and makes the sync spend
# OpenAI budget on documents the change detector considers current. That is
# correct on demand and wrong on a timer, so the guard rails are pinned here
# alongside the other properties of the job that holds the secrets.


def _repair_step() -> dict[str, Any]:
    return next(s for s in _sync_steps() if "repair-embeddings" in str(s.get("run", "")))


def test_the_repair_step_is_opt_in_and_never_fires_on_the_schedule() -> None:
    """Without the condition, every nightly run would re-flag and re-embed.

    Pinned as the WHOLE expression rather than as a substring. The ``inputs``
    context is empty on a scheduled event and a missing property reads as the
    empty string, so a bare dereference is falsy there — but a condition that
    merely mentions the input need not be: ``inputs.repair_embeddings !=
    'false'`` contains the same text and fires on every schedule, because
    '' != 'false'. A substring check calls that safe.
    """
    condition = str(_repair_step()["if"]).strip()

    assert condition == "inputs.repair_embeddings", (
        "the condition must be a bare dereference; anything compound has to be "
        "re-checked against an EMPTY inputs context, which is what a scheduled "
        f"run supplies (got: {condition!r})"
    )


def test_the_repair_input_defaults_to_off() -> None:
    """A manual re-run after a transient upstream failure — the documented
    reason `workflow_dispatch` exists here — must not silently re-embed."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML resolves the bare `on:` key to the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    repair = triggers["workflow_dispatch"]["inputs"]["repair_embeddings"]

    assert repair["default"] is False
    assert repair["type"] == "boolean"


def test_the_repair_step_is_not_handed_the_openai_key() -> None:
    """It reads Markdown and writes the manifest; it never calls OpenAI. The
    re-embed happens in the sync step, which already holds the key."""
    assert "OPENAI_API_KEY" not in str(_repair_step().get("env", {}))


def test_the_repair_step_runs_before_the_sync_that_rebuilds_the_vectors() -> None:
    """Flagging after the sync would defer every rebuild to the next run."""
    names = [str(s.get("name", "")) for s in _sync_steps()]
    runs = [str(s.get("run", "")) for s in _sync_steps()]
    repair_at = next(i for i, r in enumerate(runs) if "repair-embeddings" in r)
    sync_at = next(i for i, n in enumerate(names) if n == "Run lovspor sync")
    author_at = next(i for i, n in enumerate(names) if n.startswith("Configure git author"))

    # The repair commits to the corpus clone, so the author must be set first.
    assert author_at < repair_at < sync_at, names
