"""Security invariants of the nightly sync workflow.

`.github/workflows/sync.yml` is the only place in this project where a secret
meets the network: it holds `LOVVERK_DEPLOY_KEY` (write access to the corpus
repo) and `OPENAI_API_KEY`, and pushes to github.com over SSH. How that job
decides to trust github.com is therefore a security property of the pipeline,
not a formatting detail — these tests pin it so a future edit cannot quietly
regress to trust-on-first-use.
"""

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync.yml"

# GitHub's published ed25519 host key, fingerprint
# SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU. Authoritative sources:
# https://api.github.com/meta (`.ssh_keys`, `.ssh_key_fingerprints`) and
# https://docs.github.com/en/authentication/keeping-your-account-secure/githubs-ssh-key-fingerprints
GITHUB_ED25519_HOST_KEY = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def _executable_workflow() -> str:
    """The workflow with comment lines stripped.

    Asserting against the raw file would test the prose: the step's comment names
    `ssh-keyscan` to explain why it is *not* used, and a pinned key that appeared
    only inside a comment would satisfy a naive substring check while trusting
    nothing. Both invariants below are about what the job *executes*.
    """
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_sync_workflow_does_not_trust_github_on_first_use() -> None:
    """No `ssh-keyscan` runs: whatever answers at scan time gets trusted blindly.

    A host key harvested at run time is not a check — it is whatever the network
    handed back. In a job holding a write-capable deploy key, an impostor
    github.com could serve a tampered `lovverk` clone (whose diff a later honest
    run would push to the real repo) or blackhole the push entirely.
    """
    assert "ssh-keyscan" not in _executable_workflow()


def test_sync_workflow_pins_githubs_published_host_key() -> None:
    """The key is written to known_hosts, so a mismatch fails closed.

    If GitHub ever rotates the ed25519 key, this job breaks loudly rather than
    silently trusting the replacement — which is the whole point of pinning.
    Re-pin from https://api.github.com/meta when that happens.
    """
    assert GITHUB_ED25519_HOST_KEY in _executable_workflow()


def test_sync_workflow_never_auto_accepts_an_unknown_host_key() -> None:
    """Belt to the pin's braces.

    `StrictHostKeyChecking yes` means that if the pinned entry is ever lost, ssh
    aborts instead of falling back to accepting an unknown key.
    """
    assert "StrictHostKeyChecking yes" in _executable_workflow()
