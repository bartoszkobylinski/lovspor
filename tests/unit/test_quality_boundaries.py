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

    def test_a_similarly_named_module_is_not_exempt(self, tmp_path: Path) -> None:
        lookalike = "src/lovspor/temporal_attestation_backup.py"

        run = _run(_tree(tmp_path, {lookalike: LIFTED_READ}))

        assert run.returncode == 1
        assert f"FAIL attestation-registry: {lookalike}:4 runs `git notes`" in run.stdout

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

    def test_an_argv_built_at_runtime_is_outside_the_rule(self) -> None:
        source = 'command = [git_program, notes_subcommand, "show", sha]\n'

        assert boundaries.violations_in(ELSEWHERE, source) == []

    def test_every_violation_is_reported_in_path_order(self, tmp_path: Path) -> None:
        first = "src/lovspor/a.py"
        second = "src/lovspor/z.py"
        run = _run(
            _tree(
                tmp_path,
                {
                    second: 'args = ["notes", "show", sha]\n',
                    first: 'args = ["git", "notes", "list"]\n',
                },
            )
        )

        assert run.returncode == 1
        failures = [line for line in run.stdout.splitlines() if line.startswith("FAIL")]
        assert failures[0].startswith(f"FAIL attestation-registry: {first}:1 ")
        assert failures[1].startswith(f"FAIL attestation-registry: {second}:1 ")
        assert run.stdout.splitlines()[-1] == "boundaries: 2 violation(s), 2 rules"

    def test_an_unparseable_file_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        run = _run(_tree(tmp_path, {ELSEWHERE: "def broken(:\n"}))
        assert run.returncode == 2
        assert f"ERROR boundaries: {ELSEWHERE}" in run.stdout

    def test_the_repository_holds_to_its_boundaries(self) -> None:
        run = _run(REPO)
        assert run.returncode == 0, run.stdout


PUBLISHER = "src/lovspor/publish/emit.py"
SITE = "src/lovspor/site/build.py"
# The read the S11 guard exists to refuse, as a publisher would spell it.
LOCAL_READ = (
    "def local_documents(snapshot):\n"
    '    return snapshot.read_text("lokale-forskrifter/manifest.json")\n'
)


class TestLocalDatasetBoundary:
    @pytest.mark.parametrize(
        "source",
        [
            "from ...local_corpus import LocalDataset\n",
            "from ... import mcp_local\n",
            "from ...promotion.corpus import LOCAL_DIR\n",
        ],
        ids=["module", "parent-package", "submodule"],
    )
    def test_nested_relative_imports_reach_the_local_dataset(self, source: str) -> None:
        path = "src/lovspor/publish/templates/page.py"
        found = boundaries.violations_in(path, source)
        assert [(v.path, v.line, v.rule) for v in found] == [(path, 1, "local-dataset-unpublished")]

    @pytest.mark.parametrize(
        "source",
        [
            'def render():\n    """lokale-forskrifter; lovspor.mcp_local"""\n    return None\n',
            'async def render():\n    """lokale-forskrifter; lovspor.mcp_local"""\n    return None\n',  # noqa: E501
            'class Page:\n    """lokale-forskrifter; lovspor.mcp_local"""\n    pass\n',
        ],
        ids=["function", "async-function", "class"],
    )
    def test_documentation_in_each_python_docstring_scope_is_exempt(self, source: str) -> None:
        assert boundaries.violations_in(PUBLISHER, source) == []

    def test_both_rules_report_in_source_order_in_one_publisher(self, tmp_path: Path) -> None:
        source = (
            'args = ["git", "notes", "show", sha]\n'
            'LOCAL = "lokale-forskrifter/manifest.json"\n'
            'other_args = ["notes", "list"]\n'
        )
        run = _run(_tree(tmp_path, {PUBLISHER: source}))

        assert run.returncode == 1
        failures = [line for line in run.stdout.splitlines() if line.startswith("FAIL")]
        assert len(failures) == 3
        assert failures[0].startswith(f"FAIL attestation-registry: {PUBLISHER}:1 ")
        assert failures[1].startswith(f"FAIL local-dataset-unpublished: {PUBLISHER}:2 ")
        assert failures[2].startswith(f"FAIL attestation-registry: {PUBLISHER}:3 ")
        assert run.stdout.splitlines()[-1] == "boundaries: 3 violation(s), 2 rules"

    def test_a_publisher_reading_the_local_manifest_fails(self, tmp_path: Path) -> None:
        run = _run(_tree(tmp_path, {PUBLISHER: LOCAL_READ}))

        assert run.returncode == 1
        assert f"FAIL local-dataset-unpublished: {PUBLISHER}:2 reaches the local" in run.stdout
        assert "ADR-0016" in run.stdout
        assert run.stdout.splitlines()[-1] == "boundaries: 1 violation(s), 2 rules"

    @pytest.mark.parametrize("path", [PUBLISHER, SITE, "src/lovspor/publish/templates/x.py"])
    @pytest.mark.parametrize(
        "source",
        [
            "import lovspor.local_corpus\n",
            "import lovspor.mcp_local as served\n",
            "from lovspor.observation_history import observation_history\n",
            "from lovspor.promotion.corpus import LOCAL_DIR\n",
            "from lovspor.promotion import corpus\n",
            "from lovspor import local_corpus\n",
            'MODULE = importlib.import_module("lovspor.mcp_local")\n',
            'DATASET = "lokale-forskrifter"\n',
        ],
        ids=[
            "import",
            "import-as",
            "from-import",
            "from-submodule",
            "from-package",
            "from-lovspor",
            "importlib-literal",
            "dataset-literal",
        ],
    )
    def test_every_route_to_the_local_dataset_is_caught(self, path: str, source: str) -> None:
        found = boundaries.violations_in(path, source)
        assert [(v.path, v.line, v.rule) for v in found] == [(path, 1, "local-dataset-unpublished")]

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            ("src/lovspor/publish/__init__.py", "from ..promotion import corpus\n"),
            (PUBLISHER, "from .. import mcp_local\n"),
            (PUBLISHER, "from ..local_corpus import LocalDataset\n"),
        ],
        ids=["package-init", "parent-package", "parent-module"],
    )
    def test_a_relative_import_is_resolved_before_it_is_judged(
        self, path: str, source: str
    ) -> None:
        assert [v.rule for v in boundaries.violations_in(path, source)] == [
            "local-dataset-unpublished"
        ]

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            ("src/lovspor/mcp.py", "from lovspor.mcp_local import ServedCorpus\n"),
            ("src/lovspor/cli.py", 'LOCAL = "lokale-forskrifter"\n'),
            ("src/lovspor/publisher.py", "import lovspor.local_corpus\n"),
            (PUBLISHER, "from lovspor.promotional import banner\n"),
            (PUBLISHER, "from lovspor.publish.pages import layout\n"),
            (PUBLISHER, "from . import pages\n"),
            (PUBLISHER, '"""Local regulations (lokale-forskrifter) are never emitted."""\n'),
            (SITE, 'LEAD = "lokale forskrifter fra kommunenes nettsider"\n'),
        ],
        ids=[
            "mcp-may-serve-local",
            "cli-may-name-the-dataset",
            "lookalike-module-path",
            "lookalike-package-name",
            "central-import",
            "relative-sibling",
            "docstring-prose",
            "site-prose-without-hyphen",
        ],
    )
    def test_what_is_not_a_route_to_the_local_dataset_passes(self, path: str, source: str) -> None:
        assert boundaries.violations_in(path, source) == []

    @pytest.mark.parametrize(
        "source",
        [
            '"""Publisher documentation."""\n"lokale-forskrifter"\n',
            'def render():\n    """Doc."""\n    "lovspor.mcp_local"\n',
            'class Page:\n    x = 1\n    "lokale-forskrifter"\n',
        ],
        ids=["module-second-statement", "function-second-statement", "class-not-first"],
    )
    def test_only_the_first_statement_string_is_a_docstring(self, source: str) -> None:
        """A bare string anywhere else is a literal like any other (Codex test on PR #578)."""
        assert [v.rule for v in boundaries.violations_in(PUBLISHER, source)] == [
            "local-dataset-unpublished"
        ]
