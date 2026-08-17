import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "mutation_scope.py"


@pytest.fixture(scope="module")
def mutation_scope() -> ModuleType:
    name = "mutation_scope_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestMutationFunctionScope:
    def test_keyed_units_excludes_decorated_code(self, mutation_scope: ModuleType) -> None:
        source = """\
def plain():
    return 1

@route.get("/items")
def endpoint():
    return 2

class Service:
    def method(self):
        return 3

    @staticmethod
    def static_method():
        return 4

    @property
    def value(self):
        return 5
"""

        units = mutation_scope.keyed_units(source)

        assert [unit.key for unit in units] == [
            "x_plain",
            "xǁServiceǁmethod",
            "xǁServiceǁstatic_method",
        ]

    def test_patterns_include_only_functions_with_changed_lines(
        self, mutation_scope: ModuleType
    ) -> None:
        source = """\
def first():
    return 1

def second():
    def nested():
        return 2
    return nested()
"""

        patterns = mutation_scope.patterns_for_file(
            "src/lovspor/example.py",
            {2, 6, 7},
            source,
        )

        assert patterns == [
            "lovspor.example.x_first__mutmut_*",
            "lovspor.example.x_second__mutmut_*",
        ]

    def test_module_level_changes_without_mutants_are_ignored(
        self, mutation_scope: ModuleType
    ) -> None:
        assert (
            mutation_scope.patterns_for_file("src/lovspor/example.py", {1}, "SETTING = 1\n") == []
        )

    def test_invalid_source_falls_back_to_module(self, mutation_scope: ModuleType) -> None:
        assert mutation_scope.patterns_for_file(
            "src/lovspor/example.py", {1}, "def broken(:\n"
        ) == ["lovspor.example.*"]

    def test_module_level_changes_do_not_expand_changed_function_scope(
        self, mutation_scope: ModuleType
    ) -> None:
        source = """\
from enum import StrEnum

SETTING = 2

class Mode(StrEnum):
    STRICT = "strict"

def changed():
    return 2

def untouched():
    return 1
"""

        patterns = mutation_scope.patterns_for_file(
            "src/lovspor/example.py",
            {1, 3, 5, 6, 9},
            source,
        )

        assert patterns == ["lovspor.example.x_changed__mutmut_*"]

    def test_blank_or_comment_only_changes_do_not_expand_scope(
        self, mutation_scope: ModuleType
    ) -> None:
        source = "# changed\n\ndef kept():\n    return 1\n"

        assert mutation_scope.patterns_for_file("src/lovspor/example.py", {1, 2}, source) == []

    def test_changed_lines_use_post_image_and_ignore_deletions(
        self,
        mutation_scope: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_dir = tmp_path / "src" / "lovspor"
        source_dir.mkdir(parents=True)
        source_file = source_dir / "example.py"
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.invalid"],
            cwd=tmp_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Engineer"],
            cwd=tmp_path,
            check=True,
        )
        source_file.write_text(
            "def kept():\n    old = 1\n    return old\n\ndef removed():\n    return 2\n"
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        source_file.write_text("def kept():\n    new = 1\n    extra = 2\n    return new + extra\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "change"], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        changed = mutation_scope.changed_lines(base)

        assert changed == {"src/lovspor/example.py": {2, 3, 4}}

    def test_changed_lines_anchor_pure_deletions_to_post_image(
        self,
        mutation_scope: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_dir = tmp_path / "src" / "lovspor"
        source_dir.mkdir(parents=True)
        source_file = source_dir / "example.py"
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.invalid"],
            cwd=tmp_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Engineer"],
            cwd=tmp_path,
            check=True,
        )
        source_file.write_text("def kept():\n    old = 1\n    return old\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        source_file.write_text("def kept():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "change"], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        changed = mutation_scope.changed_lines(base)

        assert changed == {"src/lovspor/example.py": {2}}
