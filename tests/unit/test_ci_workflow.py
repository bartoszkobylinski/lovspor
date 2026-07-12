"""Security invariants of the nightly sync workflow.

`.github/workflows/sync.yml` is the only place in this project where a secret
meets the network: it holds `LOVVERK_DEPLOY_KEY` (write access to the corpus
repo) and `OPENAI_API_KEY`, and pushes to github.com over SSH. How that job
decides to trust github.com, and what it installs alongside those secrets, are
security properties of the pipeline rather than formatting details — so they are
pinned here, where a regression shows up as a failing test instead of a silently
weaker nightly run.
"""

from pathlib import Path
from typing import Any

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
    (verified: 46 packages, then 78 again). This job holds two live secrets; the
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
