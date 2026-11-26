"""Contract checks for the temporary deploy-key verification workflow."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/verify-lovverk-key.yml"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(name: str) -> str:
    steps = _workflow()["jobs"]["verify"]["steps"]
    return next(step["run"] for step in steps if step["name"] == name)


def test_verification_is_manual_main_only_and_environment_scoped() -> None:
    workflow = _workflow()
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["verify"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["strategy"]["matrix"]["environment"] == ["lovverk-sync", "lovverk-seal"]
    assert job["environment"] == "${{ matrix.environment }}"
    assert all("uses" not in step for step in job["steps"])
    assert job["steps"][-1]["if"] == "always()"


@pytest.mark.parametrize("matches", [True, False])
def test_fingerprint_check_rejects_the_wrong_key(tmp_path: Path, matches: bool) -> None:
    # The second call reads the key from its stdin, as `ssh-keygen -lf -` does.
    # A fake that answered without reading could exit before the first call
    # wrote: the writer then died of SIGPIPE and `pipefail` made the step exit
    # 141, in about 2 runs in 100 (#977).
    fake = tmp_path / "ssh-keygen"
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "-y" ]; then echo public-key; '
        'else cat >/dev/null; echo "256 SHA256:actual public-key"; fi\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = _step("Check it is the environment-scoped key").replace(
        "${{ matrix.environment }}", "lovverk-sync"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "EXPECTED_FINGERPRINT": "SHA256:actual" if matches else "SHA256:other",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is matches


@pytest.mark.parametrize("changed,probe_exists", [(False, False), (True, False), (False, True)])
def test_final_check_rejects_changed_refs_or_existing_probe(
    tmp_path: Path, changed: bool, probe_exists: bool
) -> None:
    (tmp_path / "refs-before").write_text("abc\trefs/heads/main\n", encoding="utf-8")
    fake = tmp_path / "git"
    fake.write_text(
        '#!/bin/sh\ncase "$3" in\n'
        '  refs/heads/probe) [ "$PROBE_EXISTS" = yes ] && printf "abc\\trefs/heads/probe\\n" ;;\n'
        '  *) printf "%s\\trefs/heads/main\\n" "$REMOTE_HASH" ;;\n'
        "esac\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", _step("Confirm no remote ref changed")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "LOVVERK_URL": "fake",
            "PROBE_REF": "refs/heads/probe",
            "REMOTE_HASH": "def" if changed else "abc",
            "PROBE_EXISTS": "yes" if probe_exists else "no",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (not changed and not probe_exists)
