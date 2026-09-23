"""The whole-tree secret scan `/security-check` runs must be clean (issue #329).

`gitleaks detect --no-git --source .` reported the probe refusal reason
``probe_credential_rejected`` in ``tests/unit/test_site_sources.py`` as a
``generic-api-key``: the rule keys on the word ``credential`` and takes what
follows it as the secret. The commit-time gate scans the staged index, so it
never saw it, and the skill's verdict was red on every run for a benign reason
— a gate people learn to read past. The literal now carries an inline
``gitleaks:allow``; this test runs the real scanner on that one file so the
next edit that moves or drops the marker turns the scan red here, not in the
skill's output.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCANNED = _REPO / "tests" / "unit" / "test_site_sources.py"

pytestmark = pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")


def test_the_probe_refusal_reason_is_not_a_secret_to_gitleaks(tmp_path: Path) -> None:
    shutil.copy(_SCANNED, tmp_path / _SCANNED.name)

    scan = subprocess.run(
        ["gitleaks", "detect", "--no-git", "--source", str(tmp_path), "--redact", "--no-banner"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert scan.returncode == 0, scan.stdout + scan.stderr
    assert "no leaks found" in scan.stderr


def test_the_allow_marker_sits_on_the_line_the_rule_matched() -> None:
    """A marker on the wrong line, or a blanket ignore, is not the fix the
    issue asked for: the allow must be as narrow as the finding."""
    lines = _SCANNED.read_text(encoding="utf-8").splitlines()
    marked = [line for line in lines if "gitleaks:allow" in line]

    assert len(marked) == 1, marked
    assert 'reason="probe_credential_rejected"' in marked[0]
