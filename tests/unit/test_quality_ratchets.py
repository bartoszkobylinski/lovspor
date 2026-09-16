"""Size and complexity ratchets over production code (issue #323, Phase B).

Every test builds a real tree under ``tmp_path/src`` and runs the real checker
on it, ruff included: the rules are only as good as the rejection they produce.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "quality" / "check_ratchets.py"
LEGACY = "legacy: over the limit when the ratchet landed (#323, 2026-09-15)"
DUPLICATES = (
    "if FLAG:\n    def f(a, b, c, d, e): return 0\nelse:\n    def f(a, b, c, d, e, g): return 0\n"
)


def _load() -> ModuleType:
    name = "check_ratchets_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ratchets = _load()


def _statements(count: int, indent: str = "    ") -> str:
    return "".join(f"{indent}x{i} = {i}\n" for i in range(count))


def _function(name: str, lines: int) -> str:
    return f"def {name}():\n{_statements(lines)}"


def _complex(name: str, branches: int) -> str:
    """One line per branch, so complexity (1 + branches) moves without the length."""
    body = "".join(f"    if x == {i}: return {i}\n" for i in range(branches))
    return f"def {name}(x):\n{body}    return -1\n"


def _entry(rule: str, path: str, value: int, name: str = "") -> str:
    name_line = f'name = "{name}"\n' if name else ""
    return (
        f'[[entry]]\nrule = "{rule}"\npath = "{path}"\n{name_line}'
        f'value = {value}\nreason = "{LEGACY}"\n\n'
    )


def _tree(root: Path, files: dict[str, str], baseline: str = "") -> Path:
    for relative, text in files.items():
        path = root / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "baseline.toml").write_text(baseline, encoding="utf-8")
    return root


def _check(root: Path, *extra: str) -> tuple[int, list[str]]:
    out = io.StringIO()
    argv = ["--root", str(root), "--baseline", str(root / "baseline.toml"), *extra]
    with contextlib.redirect_stdout(out):
        code = ratchets.main(argv)
    failures = [line for line in out.getvalue().splitlines() if line.startswith("FAIL")]
    return code, failures


class TestFileLines:
    def test_new_file_over_the_limit_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 701})

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet file-lines: src/big.py 701 > 700"]

    def test_new_file_at_the_limit_passes(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 700})

        assert _check(root) == (0, [])

    def test_file_lines_are_physical_lines(self, tmp_path: Path) -> None:
        text = '"""Module docstring."""\n' + "# comment\n\n" * 350
        root = _tree(tmp_path, {"notes.py": text})

        assert _check(root)[1] == ["FAIL ratchet file-lines: src/notes.py 701 > 700"]

    def test_grandfathered_file_passes_unchanged(self, tmp_path: Path) -> None:
        baseline = _entry("file-lines", "src/big.py", 701)
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 701}, baseline)

        assert _check(root) == (0, [])

    def test_grandfathered_file_fails_when_grown(self, tmp_path: Path) -> None:
        baseline = _entry("file-lines", "src/big.py", 701)
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 702}, baseline)

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet file-lines: src/big.py 702 > baseline 701"]


class TestFunctionLines:
    def test_new_function_over_the_limit_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"pkg/mod.py": _function("long", 21)})

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet function-lines: src/pkg/mod.py:1 long 21 > 20"]

    def test_docstring_comments_and_blank_lines_are_not_counted(self, tmp_path: Path) -> None:
        source = (
            "def documented():\n"
            '    """A docstring\n\n    over several lines.\n    """\n'
            "    # why this is done\n\n"
            + _statements(10)
            + "\n    # and why this\n"
            + _statements(10)
        )
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root) == (0, [])

    def test_a_statement_wrapped_over_lines_counts_every_line(self, tmp_path: Path) -> None:
        source = "def wrapped():\n    x = [\n" + "        1,\n" * 18 + "    ]\n    return x\n"
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root)[1] == ["FAIL ratchet function-lines: src/mod.py:1 wrapped 21 > 20"]

    def test_nested_definitions_count_toward_the_enclosing_function(self, tmp_path: Path) -> None:
        source = (
            "def outer():\n    def inner():\n" + _statements(19, "        ") + "    return inner\n"
        )
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root)[1] == ["FAIL ratchet function-lines: src/mod.py:1 outer 21 > 20"]

    def test_grandfathered_function_passes_unchanged(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 30)}, baseline)

        assert _check(root) == (0, [])

    def test_grandfathered_function_fails_when_longer(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 31)}, baseline)

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet function-lines: src/mod.py:1 long 31 > baseline 30"]


class TestFunctionParams:
    def test_new_function_with_too_many_params_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": "def wide(a, b, c, d, e):\n    return 0\n"})

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet function-params: src/mod.py:1 wide 5 > 4"]

    def test_the_receiver_of_a_method_is_not_counted(self, tmp_path: Path) -> None:
        source = (
            "class Service:\n"
            "    def handle(self, a, b, c, d):\n        return 0\n\n"
            "    @classmethod\n    def build(cls, a, b, c, d):\n        return 0\n"
        )
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root) == (0, [])

    def test_a_staticmethod_has_no_receiver(self, tmp_path: Path) -> None:
        source = (
            "class Service:\n    @staticmethod\n    def make(a, b, c, d, e):\n        return 0\n"
        )
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root)[1] == ["FAIL ratchet function-params: src/mod.py:3 Service.make 5 > 4"]

    def test_star_args_keyword_only_and_star_kwargs_each_count(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path, {"mod.py": "def spread(a, /, b, *args, c, **kwargs):\n    return 0\n"}
        )

        assert _check(root)[1] == ["FAIL ratchet function-params: src/mod.py:1 spread 5 > 4"]

    def test_grandfathered_function_fails_when_given_another_param(self, tmp_path: Path) -> None:
        baseline = _entry("function-params", "src/mod.py", 5, "wide")
        root = _tree(tmp_path, {"mod.py": "def wide(a, b, c, d, e, f):\n    return 0\n"}, baseline)

        assert _check(root) == (
            1,
            ["FAIL ratchet function-params: src/mod.py:1 wide 6 > baseline 5"],
        )


class TestFunctionComplexity:
    def test_new_over_complex_function_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": _complex("branchy", 10)})

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL ratchet function-complexity: src/mod.py:1 branchy 11 > 10"]

    def test_complexity_at_the_limit_passes(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": _complex("branchy", 9)})

        assert _check(root) == (0, [])

    def test_noqa_and_ruff_configuration_cannot_waive_complexity(self, tmp_path: Path) -> None:
        source = _complex("branchy", 10).replace("(x):", "(x):  # noqa: C901", 1)
        root = _tree(tmp_path, {"mod.py": "# ruff: noqa\n" + source})
        (root / "pyproject.toml").write_text(
            '[tool.ruff.lint]\nignore = ["C901"]\n'
            "[tool.ruff.lint.mccabe]\nmax-complexity = 50\n"
            '[tool.ruff.lint.per-file-ignores]\n"src/*" = ["C901"]\n',
            encoding="utf-8",
        )

        assert _check(root)[1] == ["FAIL ratchet function-complexity: src/mod.py:2 branchy 11 > 10"]

    def test_grandfathered_function_fails_when_more_complex(self, tmp_path: Path) -> None:
        baseline = _entry("function-complexity", "src/mod.py", 11, "branchy")
        root = _tree(tmp_path, {"mod.py": _complex("branchy", 11)}, baseline)

        assert _check(root) == (
            1,
            ["FAIL ratchet function-complexity: src/mod.py:1 branchy 12 > baseline 11"],
        )


class TestQualifiedNames:
    def test_methods_and_nested_functions_are_qualified(self, tmp_path: Path) -> None:
        source = (
            "class Outer:\n    class Inner:\n        def method(self, a, b, c, d, e):\n"
            "            return 0\n\n\n"
            "def outer():\n    def inner(a, b, c, d, e):\n        return 0\n    return inner\n"
        )
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root)[1] == [
            "FAIL ratchet function-params: src/mod.py:3 Outer.Inner.method 5 > 4",
            "FAIL ratchet function-params: src/mod.py:8 outer.<locals>.inner 5 > 4",
        ]

    def test_duplicate_names_are_numbered_in_source_order(self, tmp_path: Path) -> None:
        source = DUPLICATES
        root = _tree(tmp_path, {"mod.py": source})

        assert _check(root)[1] == [
            "FAIL ratchet function-params: src/mod.py:2 f 5 > 4",
            "FAIL ratchet function-params: src/mod.py:4 f#2 6 > 4",
        ]

    def test_duplicate_names_are_baselined_separately(self, tmp_path: Path) -> None:
        source = DUPLICATES
        baseline = _entry("function-params", "src/mod.py", 5, "f") + _entry(
            "function-params", "src/mod.py", 6, "f#2"
        )
        root = _tree(tmp_path, {"mod.py": source}, baseline)

        assert _check(root) == (0, [])


class TestBaselineEntries:
    def test_entry_without_a_reason_is_refused_and_waives_nothing(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long").replace(
            f'reason = "{LEGACY}"', 'reason = "  "'
        )
        root = _tree(tmp_path, {"mod.py": _function("long", 30)}, baseline)

        code, failures = _check(root)

        assert code == 1
        assert failures == [
            "FAIL ratchet baseline: entry 1 (function-lines src/mod.py long): missing reason",
            "FAIL ratchet function-lines: src/mod.py:1 long 30 > 20",
        ]

    def test_entry_missing_the_reason_field_is_refused(self, tmp_path: Path) -> None:
        baseline = _entry("file-lines", "src/big.py", 701).replace(f'reason = "{LEGACY}"\n', "")
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 701}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet baseline: entry 1 (file-lines src/big.py): missing reason",
            "FAIL ratchet file-lines: src/big.py 701 > 700",
        ]

    def test_entry_at_or_below_the_limit_is_refused(self, tmp_path: Path) -> None:
        baseline = _entry("function-params", "src/mod.py", 4, "fine")
        root = _tree(tmp_path, {"mod.py": "def fine(a):\n    return 0\n"}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet baseline: entry 1 (function-params src/mod.py fine): "
            "value 4 does not exceed the limit 4"
        ]

    def test_unknown_fields_and_rules_are_refused(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long").replace(
            "[[entry]]", "[[entry]]\nwaive = true"
        ) + _entry("function-size", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": "x = 1\n"}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet baseline: entry 1 (function-lines src/mod.py long): unknown field waive",
            "FAIL ratchet baseline: entry 2: unknown rule 'function-size'",
        ]

    def test_a_function_rule_needs_a_name_and_a_file_rule_has_none(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30) + _entry(
            "file-lines", "src/mod.py", 701, "mod"
        )
        root = _tree(tmp_path, {"mod.py": "x = 1\n"}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet baseline: entry 1: function-lines needs a name",
            "FAIL ratchet baseline: entry 2: file-lines takes no name",
        ]

    def test_duplicate_entry_is_refused(self, tmp_path: Path) -> None:
        entry = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 30)}, entry + entry)

        assert _check(root)[1] == [
            "FAIL ratchet baseline: entry 2 (function-lines src/mod.py long): duplicate entry"
        ]

    def test_unreadable_baseline_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": "x = 1\n"}, "[[entry]\n")
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = ratchets.main(["--root", str(root), "--baseline", str(root / "baseline.toml")])

        assert code == 2
        assert out.getvalue().startswith("ERROR ratchet: baseline")

    def test_missing_baseline_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": "x = 1\n"})
        (root / "baseline.toml").unlink()

        with contextlib.redirect_stdout(io.StringIO()):
            code = ratchets.main(["--root", str(root), "--baseline", str(root / "baseline.toml")])

        assert code == 2


class TestStaleEntries:
    def test_entry_for_a_function_now_within_the_limit_fails(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 20)}, baseline)

        assert _check(root) == (
            1,
            [
                "FAIL ratchet function-lines: src/mod.py:1 long 20 <= 20: "
                "within the limit, remove the baseline entry (--tighten)"
            ],
        )

    def test_entry_for_a_removed_or_renamed_function_fails(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("longer", 30)}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet function-lines: src/mod.py long not found (removed or renamed): "
            "remove the baseline entry (--tighten)",
            "FAIL ratchet function-lines: src/mod.py:1 longer 30 > 20",
        ]

    def test_entry_for_a_removed_file_fails(self, tmp_path: Path) -> None:
        baseline = _entry("file-lines", "src/gone.py", 701)
        root = _tree(tmp_path, {"mod.py": "x = 1\n"}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet file-lines: src/gone.py not found (removed or renamed): "
            "remove the baseline entry (--tighten)"
        ]

    def test_improved_function_still_over_the_limit_must_tighten(self, tmp_path: Path) -> None:
        baseline = _entry("function-complexity", "src/mod.py", 14, "branchy")
        root = _tree(tmp_path, {"mod.py": _complex("branchy", 11)}, baseline)

        assert _check(root)[1] == [
            "FAIL ratchet function-complexity: src/mod.py:1 branchy 12 < baseline 14: "
            "tighten the baseline entry to 12 (--tighten)"
        ]


class TestTree:
    def test_unparseable_source_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"broken.py": "def broken(:\n", "mod.py": "x = 1\n"})

        code, failures = _check(root)

        assert code == 1
        assert len(failures) == 1
        assert failures[0].startswith("FAIL ratchet parse: src/broken.py:1 ")

    def test_source_that_is_not_utf8_is_reported_as_an_error(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": "x = 1\n"})
        (root / "src" / "broken.py").write_bytes(b"x = '" + bytes([0xFF]) + b"'\n")
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = ratchets.main(["--root", str(root), "--baseline", str(root / "baseline.toml")])

        assert code == 1
        assert out.getvalue().startswith(
            "FAIL ratchet parse: src/broken.py:1 'utf-8' codec can't decode byte 0xff"
        )

    def test_passing_tree_reports_what_it_checked(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"a.py": _function("f", 3), "b/c.py": _function("g", 4)})
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = ratchets.main(["--root", str(root), "--baseline", str(root / "baseline.toml")])

        assert code == 0
        assert out.getvalue() == "ratchet: OK, 2 functions in 2 files, 0 baseline entries\n"

    def test_runs_standalone_as_one_command(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": _function("long", 21)})

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--baseline",
                str(root / "baseline.toml"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "FAIL ratchet function-lines: src/mod.py:1 long 21 > 20\n" in result.stdout


class TestTheCheckedInTree:
    def test_the_checked_in_baseline_matches_src(self) -> None:
        if "MUTANT_UNDER_TEST" in os.environ:
            pytest.skip("mutmut rewrites src/ into trampolines; normal runs check the real tree")
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = ratchets.main([])

        assert code == 0, out.getvalue()


def _baseline_entries(root: Path) -> list[tuple[str, str, str, int, str]]:
    data = tomllib.loads((root / "baseline.toml").read_text(encoding="utf-8"))
    return [
        (entry["rule"], entry["path"], entry.get("name", ""), entry["value"], entry["reason"])
        for entry in data.get("entry", [])
    ]


class TestTighten:
    def test_lowers_improved_entries_and_removes_stale_ones(self, tmp_path: Path) -> None:
        baseline = (
            _entry("file-lines", "src/big.py", 701)
            + _entry("file-lines", "src/gone.py", 900)
            + _entry("function-lines", "src/mod.py", 30, "long")
            + _entry("function-params", "src/mod.py", 6, "wide")
        )
        source = _function("long", 25) + "def wide(a, b):\n    return 0\n"
        root = _tree(tmp_path, {"big.py": "x = 1\n" * 701, "mod.py": source}, baseline)

        assert _check(root, "--tighten") == (0, [])

        assert _baseline_entries(root) == [
            ("file-lines", "src/big.py", "", 701, LEGACY),
            ("function-lines", "src/mod.py", "long", 25, LEGACY),
        ]
        assert (root / "baseline.toml").read_text(encoding="utf-8").startswith(ratchets.HEADER)
        assert _check(root) == (0, [])

    def test_cannot_add_an_entry_for_a_new_violation(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": _function("long", 21)})

        code, failures = _check(root, "--tighten")

        assert code == 1
        assert failures == ["FAIL ratchet function-lines: src/mod.py:1 long 21 > 20"]
        assert (root / "baseline.toml").read_text(encoding="utf-8") == ""

    def test_cannot_raise_the_entry_of_a_worse_function(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 31)}, baseline)

        code, failures = _check(root, "--tighten")

        assert code == 1
        assert failures == ["FAIL ratchet function-lines: src/mod.py:1 long 31 > baseline 30"]
        assert (root / "baseline.toml").read_text(encoding="utf-8") == baseline

    def test_a_rewrite_carries_no_addition_and_no_raise(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "worse") + _entry(
            "function-lines", "src/mod.py", 30, "better"
        )
        source = _function("worse", 31) + _function("better", 25) + _function("new", 22)
        root = _tree(tmp_path, {"mod.py": source}, baseline)

        code, failures = _check(root, "--tighten")

        assert code == 1
        assert _baseline_entries(root) == [
            ("function-lines", "src/mod.py", "better", 25, LEGACY),
            ("function-lines", "src/mod.py", "worse", 30, LEGACY),
        ]
        assert failures == [
            "FAIL ratchet function-lines: src/mod.py:1 worse 31 > baseline 30",
            "FAIL ratchet function-lines: src/mod.py:59 new 22 > 20",
        ]

    def test_refuses_a_baseline_with_refused_entries(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long").replace(
            f'reason = "{LEGACY}"\n', ""
        ) + _entry("file-lines", "src/gone.py", 900)
        root = _tree(tmp_path, {"mod.py": _function("long", 30)}, baseline)
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = ratchets.main(
                ["--root", str(root), "--baseline", str(root / "baseline.toml"), "--tighten"]
            )

        assert code == 2
        assert out.getvalue().startswith("ERROR ratchet: --tighten refused")
        assert (root / "baseline.toml").read_text(encoding="utf-8") == baseline

    def test_refuses_while_a_source_file_does_not_parse(self, tmp_path: Path) -> None:
        baseline = _entry("function-lines", "src/mod.py", 30, "long")
        root = _tree(tmp_path, {"mod.py": _function("long", 30) + "def broken(:\n"}, baseline)

        code, _ = _check(root, "--tighten")

        assert code == 2
        assert (root / "baseline.toml").read_text(encoding="utf-8") == baseline


@given(name=st.text(min_size=1), reason=st.text(min_size=1))
def test_a_rendered_baseline_round_trips_any_text(name: str, reason: str) -> None:
    key = ratchets.Key("src/mod.py", name, "function-lines")

    text = ratchets.render_baseline([ratchets.Entry(key, 21, reason)])

    assert tomllib.loads(text)["entry"] == [
        {
            "rule": "function-lines",
            "path": "src/mod.py",
            "name": name,
            "value": 21,
            "reason": reason,
        }
    ]
