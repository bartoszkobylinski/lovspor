"""``scripts/quality/check_dependency_policy.py`` (issue #465, #323 Phase F).

Each guard is proved on a tree it must reject, not only on the tree it
passes: an undeclared third-party import, a transitive-only one, and a
staged or tracked backup artefact.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "quality" / "check_dependency_policy.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_dependency_policy", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = _load()


def _tree(root: Path, dependencies: str, source: dict[str, str]) -> Path:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\ndependencies = [{dependencies}]\n'
        '[project.optional-dependencies]\nextra = ["Jinja2>=3"]\n',
        encoding="utf-8",
    )
    for relative, text in source.items():
        path = root / "src" / "lovspor" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, timeout=60)


class TestUndeclaredImports:
    def test_declared_stdlib_local_and_extra_imports_pass(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            '"httpx>=0.27", "Pydantic>=2"',
            {
                "a.py": "import json\nimport httpx\nfrom pydantic import BaseModel\n",
                "b/c.py": "from lovspor.a import x\nfrom . import d\nimport jinja2\n",
            },
        )

        assert policy.undeclared_imports(root) == []

    def test_a_transitive_only_import_is_named_with_its_distribution(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, '"typer>=0.12"', {"a.py": "import click\nimport typer\n"})

        [found] = policy.undeclared_imports(root)

        assert (found.rule, found.path) == ("undeclared-import", "src/lovspor/a.py")
        assert "imports 'click', provided by click" in found.detail
        assert "[project] in pyproject.toml does not declare" in found.detail

    def test_an_import_no_distribution_provides_says_so(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "", {"a.py": "import no_such_package_xyz\n"})

        [found] = policy.undeclared_imports(root)

        assert "which no installed distribution provides" in found.detail

    def test_every_importing_file_is_named_once(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "", {"a.py": "import click\n", "b.py": "from click import x\n"})

        assert [v.path for v in policy.undeclared_imports(root)] == [
            "src/lovspor/a.py",
            "src/lovspor/b.py",
        ]

    def test_dev_only_dependency_does_not_authorise_a_runtime_import(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _tree(tmp_path, "", {"a.py": "import yaml\n"})
        with (root / "pyproject.toml").open("a", encoding="utf-8") as stream:
            stream.write('[dependency-groups]\ndev = ["PyYAML>=6"]\n')
        monkeypatch.setattr(policy, "packages_distributions", lambda: {"yaml": ["PyYAML"]})

        [found] = policy.undeclared_imports(root)

        assert (found.rule, found.path) == ("undeclared-import", "src/lovspor/a.py")
        assert "imports 'yaml', provided by pyyaml" in found.detail

    def test_nested_absolute_imports_are_checked_but_relative_imports_are_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _tree(
            tmp_path,
            "",
            {
                "a.py": (
                    "from ..helpers import local\n"
                    "def load():\n"
                    "    import vendor.client as client\n"
                    "    from vendor.types import Item\n"
                    "if False:\n"
                    "    import vendor\n"
                ),
            },
        )
        monkeypatch.setattr(policy, "packages_distributions", lambda: {"vendor": ["vendor-dist"]})

        [found] = policy.undeclared_imports(root)

        assert (found.rule, found.path) == ("undeclared-import", "src/lovspor/a.py")
        assert "imports 'vendor', provided by vendor-dist" in found.detail

    def test_any_declared_namespace_provider_passes_after_name_normalisation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _tree(tmp_path, '"Vendor.Core>=1"', {"a.py": "import vendor.api\n"})
        monkeypatch.setattr(
            policy,
            "packages_distributions",
            lambda: {"vendor": ["unrelated-provider", "vendor_core"]},
        )

        assert policy.undeclared_imports(root) == []

    @pytest.mark.parametrize(
        ("raw", "expected"), [("PyYAML", "pyyaml"), ("pydantic_core", "pydantic-core")]
    )
    def test_names_normalise_the_pep_503_way(self, raw: str, expected: str) -> None:
        assert policy.normalise(raw) == expected

    def test_the_declared_set_strips_specifiers_and_extras(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, '"pyjwt[crypto]>=2.15.0", "mcp>=1.30.0,<2"', {})

        assert policy.declared_distributions(root / "pyproject.toml") == {"pyjwt", "mcp", "jinja2"}


class TestCommittableArtefacts:
    @pytest.mark.parametrize(
        "path",
        [
            "mutants/x.py",
            "src/mutants/x.py",
            "src/lovspor/a-mutants/b.py",
            ".mutmut-cache",
            "src/lovspor/mcp.py.bak",
            "docs/a.md.orig",
            "patch.rej",
            "src/lovspor/__pycache__/a.cpython-312.pyc",
        ],
    )
    def test_an_artefact_path_is_refused_naming_its_pattern(self, path: str) -> None:
        [found] = policy.artefact_paths([path, "src/lovspor/ok.py"])

        assert (found.rule, found.path) == ("committable-artefact", path)
        assert "matches" in found.detail

    def test_staged_artefacts_are_found_in_a_repository(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "keep.py.bak").write_text("x = 0\n", encoding="utf-8")
        _git(tmp_path, "add", "keep.py", "keep.py.bak")

        assert [v.path for v in policy.committable_artefacts(tmp_path)] == ["keep.py.bak"]

    def test_a_non_ascii_staged_artefact_is_found_unquoted(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "core.quotePath", "true")
        (tmp_path / "lovendring-æ.py.bak").write_text("", encoding="utf-8")
        _git(tmp_path, "add", "--", "lovendring-æ.py.bak")

        assert [v.path for v in policy.committable_artefacts(tmp_path)] == ["lovendring-æ.py.bak"]

    def test_untracked_artefacts_are_ignored_and_staged_paths_are_reported_once(
        self, tmp_path: Path
    ) -> None:
        _git(tmp_path, "init", "-q")
        (tmp_path / "untracked.bak").write_text("backup\n", encoding="utf-8")
        staged = tmp_path / "staged.orig"
        staged.write_text("original\n", encoding="utf-8")
        _git(tmp_path, "add", "--", staged.name)
        staged.unlink()  # The index, rather than the working tree, determines committability.

        found = policy.committable_artefacts(tmp_path)

        assert [(v.rule, v.path) for v in found] == [("committable-artefact", "staged.orig")]
        assert (tmp_path / "untracked.bak").read_text(encoding="utf-8") == "backup\n"
        assert not staged.exists()

    def test_outside_a_repository_there_is_no_verdict(self, tmp_path: Path) -> None:
        assert policy.committable_artefacts(tmp_path) == []


class TestTheCommand:
    def test_it_reports_every_violation_and_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _tree(tmp_path, "", {"a.py": "import click\n"})
        _git(root, "init", "-q")
        (root / "x.orig").write_text("", encoding="utf-8")
        _git(root, "add", "x.orig")

        code = policy.main(["--root", str(root)])

        out = capsys.readouterr().out.splitlines()
        assert code == 1
        assert out[0].startswith("FAIL undeclared-import: src/lovspor/a.py -- ")
        assert out[1].startswith("FAIL committable-artefact: x.orig -- ")
        assert out[-1] == "dependency-policy: 2 violation(s), 2 rules"

    def test_this_repository_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert policy.main(["--root", str(REPO)]) == 0
        assert capsys.readouterr().out.strip() == "dependency-policy: 0 violation(s), 2 rules"
