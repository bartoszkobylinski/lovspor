"""Each preservation contract is enforced by the scope guard."""

import subprocess
from pathlib import Path

import pytest

from tests.unit.test_agentic_ci_scripts import (
    _protected_patterns,
    _run_scope_guard,
    _scope_repo,
    _write,
)


@pytest.mark.parametrize("pattern", _protected_patterns())
def test_each_preservation_pattern_rejects_a_new_file(tmp_path: Path, pattern: str) -> None:
    repo, base = _scope_repo(tmp_path)
    path = pattern.removesuffix("*") + ("nested/new.py" if pattern.endswith("*") else "")
    _write(repo, path)

    result: subprocess.CompletedProcess[str] = _run_scope_guard(repo, base)

    assert result.returncode == 1
    assert "protected preservation contracts" in result.stderr
    assert f"  {path}\n" in result.stderr
