"""Architecture boundaries over production code (issue #323, Phases C/D3).

Every test builds a real tree under ``tmp_path/src`` and runs the real checker
on it: a boundary is only as good as the rejection it produces.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "quality" / "check_boundaries.py"
REGISTRY = "src/lovspor/temporal_attestation.py"
ELSEWHERE = "src/lovspor/mcp.py"
# The registry read as _read_entries spells it, pasted into another module.
LIFTED_READ = (
    "import subprocess\n"
    "def notes_for(repo, sha):\n"
    "    return subprocess.run(\n"
    '        ["git", "notes", "--ref=refs/notes/temporal-attestations", "show", sha],\n'
    "        cwd=repo, capture_output=True, text=True, check=False,\n"
    "    )\n"
)


def _load() -> ModuleType:
    name = "check_boundaries_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


boundaries = _load()


def _tree(root: Path, files: dict[str, str]) -> Path:
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestAttestationRegistryBoundary:
    def test_the_registry_module_may_run_git_notes(self, tmp_path: Path) -> None:
        run = _run(_tree(tmp_path, {REGISTRY: LIFTED_READ}))
        assert run.returncode == 0, run.stdout
        assert "0 violation(s)" in run.stdout

    def test_a_registry_read_lifted_into_another_module_fails(self, tmp_path: Path) -> None:
        run = _run(_tree(tmp_path, {REGISTRY: "", ELSEWHERE: LIFTED_READ}))
        assert run.returncode == 1
        assert f"FAIL attestation-registry: {ELSEWHERE}:4 runs `git notes`" in run.stdout
        assert REGISTRY in run.stdout
        assert "ADR-0012 point 2c" in run.stdout

    @pytest.mark.parametrize(
        "argv",
        [
            '["git", "notes", "add", "-f", "-m", payload, sha]',
            '("git", "-C", str(repo), "notes", "show", sha)',
            '["notes", "--ref=refs/notes/temporal-attestations", "show", sha]',
            '["git", f"--git-dir={d}", "notes", "list"]',
        ],
        ids=["list", "tuple-with-C", "helper-prepends-git", "fstring-option"],
    )
    def test_every_argv_shape_that_runs_git_notes_is_caught(self, argv: str) -> None:
        found = boundaries.violations_in(ELSEWHERE, f"x = {argv}\n")
        assert [(v.path, v.line) for v in found] == [(ELSEWHERE, 1)]

    @pytest.mark.parametrize(
        "source",
        [
            'MAY_DIFFER = frozenset({"run_id", "notes", "started_at"})\n',
            'FIELDS = ["title", "notes"]\n',
            'args = ["git", "log", "--format=%N", sha]\n',
            'REF = "refs/notes/temporal-attestations"\n',
        ],
        ids=["set-of-field-names", "notes-not-a-subcommand", "other-git-command", "ref-name-only"],
    )
    def test_notes_that_is_not_a_git_notes_call_passes(self, source: str) -> None:
        assert boundaries.violations_in(ELSEWHERE, source) == []

    def test_an_unparseable_file_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        run = _run(_tree(tmp_path, {ELSEWHERE: "def broken(:\n"}))
        assert run.returncode == 2
        assert f"ERROR boundaries: {ELSEWHERE}" in run.stdout

    def test_the_repository_holds_to_its_boundaries(self) -> None:
        run = _run(REPO)
        assert run.returncode == 0, run.stdout
