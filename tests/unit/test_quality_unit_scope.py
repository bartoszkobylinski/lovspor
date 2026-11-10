"""``scripts/quality/unit_scope.py``: the deep gate's test selection (issue #864).

Each case builds a scratch repository tree in ``tmp_path`` — a ``src/lovspor``
package and a ``tests/unit`` directory — and asks which unit test files a
push's changed paths reach. Git questions run against real repositories.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "quality" / "unit_scope.py"
ZEROS = "0" * 40


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("unit_scope_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


us = _load()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


BASE_TREE = {
    "src/lovspor/__init__.py": "",
    "src/lovspor/a.py": "from lovspor import b\n",
    "src/lovspor/b.py": "X = 1\n",
    "src/lovspor/c.py": "Y = 2\n",
    "src/lovspor/pkg/__init__.py": "",
    "src/lovspor/pkg/inner.py": "from ..c import Y\n",
    "src/lovspor/pkg/leaf.py": "from . import inner\n",
    "src/lovspor/data/__init__.py": "",
    "src/lovspor/data/loader.py": "Z = 3\n",
    "src/lovspor/data/table.json": "{}\n",
    "tests/__init__.py": "",
    "tests/unit/__init__.py": "",
    "tests/unit/helper.py": "import lovspor.c\n",
    "tests/unit/chain.py": "from tests.unit.helper import *\n",
    "tests/unit/test_a.py": "from lovspor.a import b\n",
    "tests/unit/test_b.py": "import lovspor.b\n",
    "tests/unit/test_leaf.py": "from lovspor.pkg import leaf\n",
    "tests/unit/test_chain.py": "from tests.unit.chain import *\n",
    "tests/unit/test_loader.py": "from lovspor.data.loader import Z\n",
    "tests/unit/test_reads_workflow.py": "PIPELINE = 'pr-pipeline.yml'\n",
    "tests/unit/test_dynamic.py": "import importlib\nM = importlib.import_module('lovspor.c')\n",
    "tests/unit/test_alone.py": "def test_x() -> None:\n    pass\n",
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    _write(tmp_path, BASE_TREE)
    return tmp_path


def _tests(scope: object) -> list[str]:
    assert scope.full is None, scope.full  # type: ignore[attr-defined]
    return [Path(path).name for path in scope.tests]  # type: ignore[attr-defined]


class TestSelection:
    def test_a_changed_test_file_is_selected_directly(self, tree: Path) -> None:
        assert _tests(us.compute_scope(tree, ["tests/unit/test_alone.py"])) == ["test_alone.py"]

    def test_a_deleted_test_file_selects_nothing_and_is_no_python_reason(self, tree: Path) -> None:
        scope = us.compute_scope(tree, ["tests/unit/test_gone.py"])

        assert scope.full is None
        assert scope.tests == ()

    def test_a_change_reaches_the_tests_of_every_transitive_importer(self, tree: Path) -> None:
        """a imports b; b changes; the test of a runs as well as the test of b."""
        assert _tests(us.compute_scope(tree, ["src/lovspor/b.py"])) == ["test_a.py", "test_b.py"]

    def test_relative_imports_are_resolved_against_the_package(self, tree: Path) -> None:
        """pkg.inner does `from ..c import Y`; pkg.leaf does `from . import inner`."""
        selected = _tests(us.compute_scope(tree, ["src/lovspor/c.py"]))

        assert "test_leaf.py" in selected

    def test_from_package_import_submodule_counts_as_importing_the_submodule(
        self, tree: Path
    ) -> None:
        assert _tests(us.compute_scope(tree, ["src/lovspor/pkg/leaf.py"])) == ["test_leaf.py"]

    def test_a_changed_package_init_is_seeded_by_its_own_package_name(self, tree: Path) -> None:
        """``__init__.py`` stands for the package, not a module literally named
        ``__init__``; leaf's `from . import inner` imports exactly that name."""
        assert _tests(us.compute_scope(tree, ["src/lovspor/pkg/__init__.py"])) == ["test_leaf.py"]

    def test_a_helper_module_chain_carries_the_import(self, tree: Path) -> None:
        """test_chain -> tests.unit.chain -> tests.unit.helper -> lovspor.c."""
        assert "test_chain.py" in _tests(us.compute_scope(tree, ["src/lovspor/c.py"]))

    def test_a_dotted_module_string_counts_as_an_import(self, tree: Path) -> None:
        """importlib.import_module and monkeypatch targets name modules as strings."""
        assert "test_dynamic.py" in _tests(us.compute_scope(tree, ["src/lovspor/c.py"]))

    def test_an_unrelated_test_is_not_selected(self, tree: Path) -> None:
        assert "test_alone.py" not in _tests(us.compute_scope(tree, ["src/lovspor/c.py"]))

    def test_a_data_file_reaches_the_tests_of_its_package(self, tree: Path) -> None:
        selected = _tests(us.compute_scope(tree, ["src/lovspor/data/table.json"]))

        assert selected == ["test_loader.py"]

    def test_a_file_elsewhere_selects_the_tests_that_name_it(self, tree: Path) -> None:
        scope = us.compute_scope(tree, [".github/workflows/pr-pipeline.yml"])

        assert _tests(scope) == ["test_reads_workflow.py"]

    def test_a_docs_only_change_selects_nothing(self, tree: Path) -> None:
        scope = us.compute_scope(tree, ["docs/decisions.md", "mutation-equivalents/x.toml"])

        assert scope.full is None
        assert scope.tests == ()
        assert scope.python_changed is False

    def test_selected_paths_are_sorted_repo_relative_test_files(self, tree: Path) -> None:
        scope = us.compute_scope(tree, ["src/lovspor/b.py"])

        assert list(scope.tests) == ["tests/unit/test_a.py", "tests/unit/test_b.py"]
        assert scope.changed == 1

    def test_conftest_imports_reach_every_test(self, tree: Path) -> None:
        _write(tree, {"tests/conftest.py": "from lovspor.data import loader\n"})

        scope = us.compute_scope(tree, ["src/lovspor/data/loader.py"])

        assert len(_tests(scope)) == len([p for p in BASE_TREE if "/test_" in p])


class TestFull:
    @pytest.mark.parametrize(
        "path",
        [
            "pyproject.toml",
            "uv.lock",
            ".pre-commit-config.yaml",
            "tests/conftest.py",
            "tests/unit/conftest.py",
            "tests/unit/helper.py",
            "tests/scope_order.py",
            "tests/fixtures/law.xml",
            "scripts/quality/gate.sh",
            "scripts/quality/unit_scope.py",
        ],
    )
    def test_a_shared_infrastructure_change_runs_everything(self, tree: Path, path: str) -> None:
        scope = us.compute_scope(tree, ["tests/unit/test_alone.py", path])

        assert scope.full is not None
        assert path in scope.full

    def test_an_unparsable_source_file_runs_everything(self, tree: Path) -> None:
        _write(tree, {"src/lovspor/b.py": "def broken(:\n"})

        scope = us.compute_scope(tree, ["src/lovspor/b.py"])

        assert scope.full is not None
        assert "src/lovspor/b.py" in scope.full

    def test_a_python_file_no_test_names_runs_everything(self, tree: Path) -> None:
        scope = us.compute_scope(tree, ["scripts/ci/orphan.py"])

        assert scope.full is not None
        assert "scripts/ci/orphan.py" in scope.full

    def test_a_source_change_no_unit_test_imports_runs_everything(self, tree: Path) -> None:
        _write(tree, {"src/lovspor/lonely.py": "W = 0\n"})

        scope = us.compute_scope(tree, ["src/lovspor/lonely.py"])

        assert scope.full is not None


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tree: Path) -> Path:
    _git(tree, "init", "--quiet")
    _git(tree, "add", "-A")
    _git(tree, "commit", "--quiet", "-m", "base")
    _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(tree, {"src/lovspor/b.py": "X = 2\n"})
    _git(tree, "commit", "--quiet", "-am", "change b")
    return tree


class TestChangedPaths:
    def test_without_pre_commit_refs_the_base_is_the_merge_base_with_origin_main(
        self, repo: Path
    ) -> None:
        assert us.changed_paths(repo, {}) == ["src/lovspor/b.py"]

    def test_an_all_zero_from_ref_falls_back_to_the_merge_base(self, repo: Path) -> None:
        """pre-commit passes zeros as the from-ref when the branch is new on the remote."""
        env = {"PRE_COMMIT_FROM_REF": ZEROS, "PRE_COMMIT_TO_REF": "HEAD"}

        assert us.changed_paths(repo, env) == ["src/lovspor/b.py"]

    def test_the_pre_commit_refs_bound_the_diff(self, repo: Path) -> None:
        head = _git(repo, "rev-parse", "HEAD")
        _write(repo, {"src/lovspor/c.py": "Y = 3\n"})
        _git(repo, "commit", "--quiet", "-am", "change c")

        env = {"PRE_COMMIT_FROM_REF": head, "PRE_COMMIT_TO_REF": "HEAD"}

        assert us.changed_paths(repo, env) == ["src/lovspor/c.py"]

    def test_a_git_failure_is_none(self, tmp_path: Path) -> None:
        assert us.changed_paths(tmp_path, {}) is None


class TestScopeForPush:
    def test_a_git_failure_runs_everything(self, tree: Path) -> None:
        scope = us.scope_for_push(tree, {})

        assert scope.full is not None
        assert "git" in scope.full

    def test_verify_deep_full_forces_everything(self, repo: Path) -> None:
        scope = us.scope_for_push(repo, {"VERIFY_DEEP_FULL": "1"})

        assert scope.full is not None
        assert "VERIFY_DEEP_FULL" in scope.full

    def test_a_push_runs_the_tests_its_diff_reaches(self, repo: Path) -> None:
        assert _tests(us.scope_for_push(repo, {})) == ["test_a.py", "test_b.py"]
