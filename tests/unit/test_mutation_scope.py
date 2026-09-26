import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from mutmut.mutation.file_mutation import mutate_file_contents

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


DECORATED_CLASS_SOURCE = """\
from dataclasses import dataclass

@dataclass(frozen=True)
class Record:
    count: int = 1

    def total(self):
        return self.count + 1

    @property
    def doubled(self):
        return self.count * 2

    @staticmethod
    def half(value):
        return value / 2

class Outer:
    @dataclass
    class Inner:
        def inner_method(self):
            return 1 + 1

    def outer_method(self):
        return 2 + 2
"""


METHODLESS_DECLARATION_SOURCE = """\
@register
@dataclass(
    frozen=1 + 1,
)
class Record(
    make_base(2 + 2),
):
    \"\"\"Docstring.\"\"\"
    count: int = 3 + 3
"""


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

    def test_keyed_units_select_methods_of_a_top_level_decorated_class(
        self, mutation_scope: ModuleType
    ) -> None:
        units = mutation_scope.keyed_units(DECORATED_CLASS_SOURCE)

        assert [unit.key for unit in units] == [
            "xǁRecordǁtotal",
            "xǁRecordǁhalf",
            "xǁOuterǁouter_method",
        ]

    def test_keyed_units_match_the_functions_mutmut_mutates(
        self, mutation_scope: ModuleType
    ) -> None:
        # #419: the assumption is pinned against the installed mutmut, so a
        # bump that changes which methods get trampolines turns this red.
        mutated = mutate_file_contents("x.py", DECORATED_CLASS_SOURCE)
        mutated_keys = {name.rsplit("__mutmut_", 1)[0] for name in mutated.mutant_names}

        units = mutation_scope.keyed_units(DECORATED_CLASS_SOURCE)

        assert {unit.key for unit in units} == mutated_keys

    def test_a_changed_decorated_class_method_selects_its_pattern(
        self, mutation_scope: ModuleType
    ) -> None:
        patterns = mutation_scope.patterns_for_file(
            "src/lovspor/example.py", {3, 7, 10}, DECORATED_CLASS_SOURCE
        )

        assert patterns == ["lovspor.example.xǁRecordǁtotal__mutmut_*"]

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


UNMEASURED_SOURCE = '''\
"""Module docstring."""

import re

_RELEASE_ID = re.compile(
    r"\\A[0-9a-f]{64}\\Z"
)


def plain():
    return 1


@app.command("status")
def status(verbose: bool = False):
    # operator-facing branch
    if verbose:
        return 2
    return 3


class Service:
    """Class docstring."""

    LIMIT = 5

    def method(self):
        return 4


@dataclass
class Record:
    def total(self):
        return 6
'''


class TestUnmeasuredNotices:
    """#289 / #292: a changed line no mutant can measure is named, not implied covered."""

    def test_a_changed_module_level_constant_is_named(self, mutation_scope: ModuleType) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", {5, 6, 7}, UNMEASURED_SOURCE
        )

        assert notices == ["unmeasured changed lines: src/lovspor/example.py:5-7 (module level)"]

    def test_a_changed_decorated_function_body_names_the_function(
        self, mutation_scope: ModuleType
    ) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/cli.py", {15, 17, 18}, UNMEASURED_SOURCE
        )

        assert notices == [
            "unmeasured changed lines: src/lovspor/cli.py:15,17-18 (decorated function status)"
        ]

    def test_a_changed_decorator_line_counts_as_the_decorated_function(
        self, mutation_scope: ModuleType
    ) -> None:
        notices = mutation_scope.unmeasured_notices("src/lovspor/cli.py", {14}, UNMEASURED_SOURCE)

        assert notices == [
            "unmeasured changed lines: src/lovspor/cli.py:14 (decorated function status)"
        ]

    def test_class_attributes_and_decorated_classes_are_named(
        self, mutation_scope: ModuleType
    ) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", {25, 31}, UNMEASURED_SOURCE
        )

        assert notices == [
            "unmeasured changed lines: src/lovspor/example.py:25 (class body Service)",
            "unmeasured changed lines: src/lovspor/example.py:31 (class declaration Record)",
        ]

    def test_a_decorated_class_notices_its_decorator_and_body_not_its_methods(
        self, mutation_scope: ModuleType
    ) -> None:
        # #419: mutmut measures the methods of a top-level decorated class, so
        # only the decorator and the field lines stay unmeasured.
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", {3, 5, 7, 8, 10, 12}, DECORATED_CLASS_SOURCE
        )

        assert notices == [
            "unmeasured changed lines: src/lovspor/example.py:3 (class declaration Record)",
            "unmeasured changed lines: src/lovspor/example.py:5 (class body Record)",
            "unmeasured changed lines: src/lovspor/example.py:10,12"
            " (decorated function Record.doubled)",
        ]

    def test_a_decorated_class_notices_its_unmeasured_declaration(
        self, mutation_scope: ModuleType
    ) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", {4}, DECORATED_CLASS_SOURCE
        )

        assert notices == [
            "unmeasured changed lines: src/lovspor/example.py:4 (class declaration Record)"
        ]

    @pytest.mark.parametrize(
        ("changed_lines", "expected"),
        [
            pytest.param({1}, ["1 (class declaration Record)"], id="first-of-two-decorators"),
            pytest.param({3}, ["3 (class declaration Record)"], id="decorator-argument-line"),
            pytest.param({6}, ["6 (class declaration Record)"], id="multi-line-header-base"),
            pytest.param({9}, ["9 (class body Record)"], id="field-of-a-class-without-methods"),
            pytest.param(
                {2, 7, 9},
                ["2,7 (class declaration Record)", "9 (class body Record)"],
                id="declaration-lines-share-one-notice",
            ),
        ],
    )
    def test_a_decorated_class_declaration_spans_every_decorator_and_its_header(
        self, mutation_scope: ModuleType, changed_lines: set[int], expected: list[str]
    ) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", changed_lines, METHODLESS_DECLARATION_SOURCE
        )

        assert notices == [
            f"unmeasured changed lines: src/lovspor/example.py:{e}" for e in expected
        ]

    def test_mutmut_mutates_nothing_in_a_decorated_class_without_methods(
        self, mutation_scope: ModuleType
    ) -> None:
        # the declaration and field lines really are unmeasured: the installed
        # mutmut creates no mutant for decorator arguments, bases or defaults
        assert mutate_file_contents("x.py", METHODLESS_DECLARATION_SOURCE).mutant_names == []
        assert mutation_scope.keyed_units(METHODLESS_DECLARATION_SOURCE) == []

    def test_a_nested_decorated_class_stays_unmeasured_as_a_whole(
        self, mutation_scope: ModuleType
    ) -> None:
        notices = mutation_scope.unmeasured_notices(
            "src/lovspor/example.py", {19, 22}, DECORATED_CLASS_SOURCE
        )

        assert notices == [
            "unmeasured changed lines: src/lovspor/example.py:19,22 (decorated class Outer.Inner)"
        ]

    def test_nested_class_and_async_method_notices_keep_their_qualified_owner(
        self, mutation_scope: ModuleType
    ) -> None:
        source = """\
class Outer:
    @dataclass
    class Record:
        value: int

    @route.get("/value")
    async def value(self):
        return 1
"""

        notices = mutation_scope.unmeasured_notices("src/lovspor/example.py", {2, 4, 6, 8}, source)

        assert notices == [
            "unmeasured changed lines: src/lovspor/example.py:2,4 (decorated class Outer.Record)",
            "unmeasured changed lines: src/lovspor/example.py:6,8 (decorated function Outer.value)",
        ]

    def test_measured_inert_and_comment_lines_raise_no_notice(
        self, mutation_scope: ModuleType
    ) -> None:
        # docstrings, an import, blank lines, a comment inside a decorated
        # body, a mutatable function and a mutatable method
        lines = {1, 2, 3, 4, 10, 11, 16, 23, 27, 28, 33, 34}

        assert mutation_scope.unmeasured_notices("src/lovspor/x.py", lines, UNMEASURED_SOURCE) == []

    @pytest.mark.parametrize(
        ("source", "changed_lines"),
        [
            (
                '''\
@command()
def decorated():
    """Changed function docstring."""
    return 1
''',
                {3},
            ),
            (
                '''\
@dataclass
class Decorated:
    """Changed class docstring."""

    def value(self):
        return 1
''',
                {3},
            ),
            (
                """\
@command()
def decorated():
    import changed_dependency

    return changed_dependency.VALUE
""",
                {3},
            ),
        ],
    )
    def test_inert_lines_nested_in_skipped_regions_raise_no_notice(
        self,
        mutation_scope: ModuleType,
        source: str,
        changed_lines: set[int],
    ) -> None:
        # _is_inert defines imports and docstrings as operator-free "anywhere";
        # enclosing them in a decorated region must not turn them into code.
        assert mutation_scope.unmeasured_notices("src/lovspor/x.py", changed_lines, source) == []

    @pytest.mark.parametrize(
        ("source", "changed_line", "region"),
        [
            (
                "'module docstring'\n\n'executable string expression'\n",
                3,
                "module level",
            ),
            (
                "class Service:\n    'class docstring'\n    'executable string expression'\n",
                3,
                "class body Service",
            ),
            (
                "@command()\ndef decorated():\n    'function docstring'\n    'executable string expression'\n",  # noqa: E501
                4,
                "decorated function decorated",
            ),
        ],
    )
    def test_only_actual_docstrings_are_inert(
        self,
        mutation_scope: ModuleType,
        source: str,
        changed_line: int,
        region: str,
    ) -> None:
        notices = mutation_scope.unmeasured_notices("src/lovspor/x.py", {changed_line}, source)

        assert notices == [f"unmeasured changed lines: src/lovspor/x.py:{changed_line} ({region})"]

    @pytest.mark.parametrize(
        ("source", "changed_lines", "expected"),
        [
            pytest.param(
                '@command()\ndef decorated():\n    f"not a {docstring}"\n    return 1\n',
                {3},
                "3 (decorated function decorated)",
                id="f-string-in-docstring-position-is-code",
            ),
            pytest.param(
                "from __future__ import annotations\n\n'not a docstring'\n",
                {1, 3},
                "3 (module level)",
                id="future-import-inert-string-after-it-is-not",
            ),
            pytest.param(
                "@command()\ndef outer():\n    def inner():\n"
                "        'inner docstring'\n        return 1\n    return inner()\n",
                {3, 4, 5},
                "3,5 (decorated function outer)",
                id="nested-def-docstring-inert-its-code-not",
            ),
            pytest.param(
                "class Service:\n    'class docstring'\n\n    @property\n"
                "    def value(self):\n        'method docstring'\n"
                "        x = 1\n        'after first statement'\n        return x\n",
                {2, 6, 7, 8},
                "7-8 (decorated function Service.value)",
                id="class-and-method-docstrings-inert-later-string-not",
            ),
            pytest.param(
                "@dataclass\nclass Record:\n    'class docstring'\n\n"
                "    def total(self):\n        'method docstring'\n        return 6\n",
                {1, 3, 6},
                "1 (class declaration Record)",
                id="decorated-class-docstrings-inert",
            ),
            pytest.param(
                '"""multi\nline\ndocstring"""\nimport os\nLIMIT = (\n    "a"\n)\n',
                {1, 2, 3, 4, 5, 6, 7},
                "5-7 (module level)",
                id="multiline-docstring-and-string-inside-statement",
            ),
            pytest.param(
                "LIMIT = 1\n'attribute docstring'\n",
                {1, 2},
                "1 (module level)",
                id="module-attribute-docstring-inert",
            ),
            pytest.param(
                "class Service:\n    LIMIT: int = 1\n    'attribute docstring'\n",
                {2, 3},
                "2 (class body Service)",
                id="class-attribute-docstring-inert",
            ),
            pytest.param(
                "@command()\ndef decorated():\n    x = 1\n    'no attribute here'\n    return x\n",
                {3, 4},
                "3-4 (decorated function decorated)",
                id="string-after-assignment-in-a-function-is-code",
            ),
            pytest.param(
                "def f():\n    return 1\n'after a def'\n",
                {3},
                "3 (module level)",
                id="string-after-a-def-is-code",
            ),
            pytest.param(
                "A = 1\nB = 2\n\nC = 3\n",
                {1, 2, 4},
                "1-2,4 (module level)",
                id="one-notice-per-region-across-statements",
            ),
        ],
    )
    def test_docstring_position_decides_what_is_inert(
        self,
        mutation_scope: ModuleType,
        source: str,
        changed_lines: set[int],
        expected: str,
    ) -> None:
        assert mutation_scope.unmeasured_notices("src/lovspor/x.py", changed_lines, source) == [
            f"unmeasured changed lines: src/lovspor/x.py:{expected}"
        ]

    def test_unparseable_source_raises_no_notice(self, mutation_scope: ModuleType) -> None:
        # the whole module is in scope then (`lovspor.x.*`), so nothing is unmeasured
        assert mutation_scope.unmeasured_notices("src/lovspor/x.py", {1}, "def broken(:\n") == []

    def test_explain_prints_the_notice_on_its_own_line(
        self,
        mutation_scope: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            mutation_scope, "changed_lines", lambda base: {"src/lovspor/example.py": {5, 11}}
        )
        monkeypatch.setattr(mutation_scope, "head_source", lambda path: UNMEASURED_SOURCE)
        monkeypatch.setattr("sys.argv", ["mutation_scope.py", "--base", "main", "--explain"])

        assert mutation_scope.main() == 0

        captured = capsys.readouterr()
        assert captured.out.strip() == "lovspor.example.x_plain__mutmut_*"
        assert (
            "\nunmeasured changed lines: src/lovspor/example.py:5 (module level)\n" in captured.err
        )

    def test_without_explain_nothing_but_patterns_is_printed(
        self,
        mutation_scope: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            mutation_scope, "changed_lines", lambda base: {"src/lovspor/example.py": {5}}
        )
        monkeypatch.setattr(mutation_scope, "head_source", lambda path: UNMEASURED_SOURCE)
        monkeypatch.setattr("sys.argv", ["mutation_scope.py", "--base", "main"])

        assert mutation_scope.main() == 0

        assert capsys.readouterr() == ("\n", "")
