"""#228: a changed legacy function runs only the mutants on its changed lines.

The diffs here are real `mutmut show` output: `get_diff_for_mutant` is the
function `mutmut show` prints, fed the mutated file the installed mutmut
generates for SOURCE.
"""

import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from mutmut.mutation.diff_apply import get_diff_for_mutant
from mutmut.mutation.file_mutation import mutate_file_contents

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "mutation_legacy.py"
PATH = "src/lovspor/example.py"
MODULE = "lovspor.example"

SOURCE = """\
import os


# a comment above
def big(a, b):
    x = a + 1
    y = b * 2
    x = a + 1
    return x - y


class Svc:
    # method comment

    def work(self, n):
        total = n + 1
        return total * 3
"""
LINES = SOURCE.splitlines()


@pytest.fixture(scope="module")
def legacy() -> ModuleType:
    name = "mutation_legacy_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diffs() -> dict[str, str]:
    mutated = mutate_file_contents("x.py", SOURCE)
    return {
        f"{MODULE}.{name}": get_diff_for_mutant(name, source=mutated.code, path=PATH)
        for name in mutated.mutant_names
    }


def _target(legacy: ModuleType, key: str, span: tuple[int, int], changed: set[int]) -> object:
    function = key.removeprefix("x_").replace("xǁSvcǁ", "Svc.")
    return legacy.Target(PATH, function, f"{MODULE}.{key}__mutmut_", *span, tuple(sorted(changed)))


def _of(diffs: dict[str, str], key: str) -> dict[str, str]:
    return {name: diff for name, diff in diffs.items() if f".{key}__mutmut_" in name}


def _numbers(names: list[str]) -> list[int]:
    return [int(name.rsplit("_", 1)[1]) for name in names]


class TestMutantLines:
    def test_each_real_mutant_resolves_to_the_line_it_changes(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        resolved = {
            int(name.rsplit("_", 1)[1]): legacy.mutant_lines(diff, LINES, (5, 9))
            for name, diff in _of(diffs, "x_big").items()
        }

        assert resolved == {
            1: {6}, 2: {6}, 3: {6},
            4: {7}, 5: {7}, 6: {7},
            7: {8}, 8: {8}, 9: {8},
            10: {9},
        }  # fmt: skip

    def test_a_method_resolves_past_its_comment_and_indentation(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        resolved = [
            legacy.mutant_lines(d, LINES, (15, 17)) for d in _of(diffs, "xǁSvcǁwork").values()
        ]

        assert resolved == [{16}, {16}, {16}, {17}, {17}]

    def test_a_diff_that_matches_no_source_is_unresolved(self, legacy: ModuleType) -> None:
        diff = "--- a\n+++ a\n@@ -1,2 +1,2 @@\n def gone():\n-    return 1\n+    return 2"

        assert legacy.mutant_lines(diff, LINES, (1, len(LINES))) is None

    def test_a_diff_without_a_removed_line_is_unresolved(self, legacy: ModuleType) -> None:
        assert (
            legacy.mutant_lines("--- a\n+++ a\n@@ -1 +1,2 @@\n import os\n+x", LINES, (1, 3))
            is None
        )

    def test_a_match_outside_the_function_span_does_not_count(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        # the block of big's first mutant exists only inside big (5-9)
        assert legacy.mutant_lines(next(iter(diffs.values())), LINES, (12, 17)) is None


class TestSelect:
    def test_only_the_changed_lines_mutants_run(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        selection = legacy.select(_of(diffs, "x_big"), _target(legacy, "x_big", (5, 9), {7}), LINES)

        assert _numbers(selection.run) == [4, 5, 6]
        assert selection.skipped == {
            f"{MODULE}.x_big__mutmut_{n}": {line}
            for n, line in [(1, 6), (2, 6), (3, 6), (7, 8), (8, 8), (9, 8), (10, 9)]
        }

    def test_a_repeated_line_is_told_apart_by_its_context(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        # lines 6 and 8 are both `x = a + 1`; only line 8 changed
        selection = legacy.select(_of(diffs, "x_big"), _target(legacy, "x_big", (5, 9), {8}), LINES)

        assert _numbers(selection.run) == [7, 8, 9]

    def test_an_unresolved_mutant_runs_rather_than_being_skipped(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        broken = {**_of(diffs, "x_big"), f"{MODULE}.x_big__mutmut_1": ""}

        selection = legacy.select(broken, _target(legacy, "x_big", (5, 9), {9}), LINES)

        assert _numbers(selection.run) == [1, 10]

    def test_run_order_follows_the_mutant_number_not_the_name(self, legacy: ModuleType) -> None:
        names = {f"{MODULE}.x_big__mutmut_{n}": "" for n in (10, 2, 1)}

        selection = legacy.select(names, _target(legacy, "x_big", (5, 9), {6}), LINES)

        assert _numbers(selection.run) == [1, 2, 10]


class TestRemainderNotice:
    def test_the_skipped_mutants_lines_are_reported_as_legacy_remainder(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        target = _target(legacy, "x_big", (5, 9), {7})

        notice = legacy.remainder_notice(target, legacy.select(_of(diffs, "x_big"), target, LINES))

        assert notice == (
            "unmeasured changed lines: src/lovspor/example.py:6,8-9 "
            "(legacy remainder big: 7 of 10 mutants not run, #228)"
        )

    def test_nothing_skipped_means_no_notice(
        self, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        target = _target(legacy, "x_big", (5, 9), {6, 7, 8, 9})

        assert (
            legacy.remainder_notice(target, legacy.select(_of(diffs, "x_big"), target, LINES))
            is None
        )


class TestMutantNames:
    def test_only_the_targets_mutants_are_taken_from_mutmut_results(
        self, legacy: ModuleType
    ) -> None:
        results = (
            "    lovspor.example.x_big__mutmut_1: not checked\n"
            "    lovspor.example.x_big__mutmut_12: survived\n"
            "    lovspor.example.x_bigger__mutmut_1: not checked\n"
            "    lovspor.other.x_big__mutmut_1: not checked\n"
        )

        names = legacy.names_of(results, "lovspor.example.x_big__mutmut_")

        assert names == ["lovspor.example.x_big__mutmut_1", "lovspor.example.x_big__mutmut_12"]


FAKE_MUTMUT = """\
#!/bin/sh
here="$(dirname "$0")"
case "$1" in
  results) cat "$here/results.txt" ;;
  show) cat "$here/diffs/$2" ;;
  *) exit 64 ;;
esac
"""


def _fake_mutmut(tmp_path: Path, diffs: dict[str, str]) -> Path:
    stub = tmp_path / "stub"
    (stub / "diffs").mkdir(parents=True)
    results = "".join(f"    {name}: not checked\n" for name in diffs)
    (stub / "results.txt").write_text(results, encoding="utf-8")
    for name, diff in diffs.items():
        (stub / "diffs" / name).write_text(f"# {name}: not checked\n{diff}\n", encoding="utf-8")
    (stub / "mutmut").write_text(FAKE_MUTMUT, encoding="utf-8")
    (stub / "mutmut").chmod(0o755)
    return stub / "mutmut"


def _plan(tmp_path: Path, legacy: ModuleType, changed: set[int]) -> Path:
    target = _target(legacy, "x_big", (5, 9), changed)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([dataclasses.asdict(target)]), encoding="utf-8")
    return plan


class TestSelectCommand:
    def _select(self, tmp_path: Path, mutmut: Path, plan: Path) -> subprocess.CompletedProcess[str]:
        (tmp_path / "src" / "lovspor").mkdir(parents=True, exist_ok=True)
        (tmp_path / PATH).write_text(SOURCE, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "select", "--plan", str(plan), "--mutmut", str(mutmut)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )

    def test_prints_the_selected_names_and_reports_the_remainder(
        self, tmp_path: Path, legacy: ModuleType, diffs: dict[str, str]
    ) -> None:
        mutmut = _fake_mutmut(tmp_path, diffs)

        result = self._select(tmp_path, mutmut, _plan(tmp_path, legacy, {9}))

        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == [f"{MODULE}.x_big__mutmut_10"]
        assert (
            "\nunmeasured changed lines: src/lovspor/example.py:6-8 "
            "(legacy remainder big: 9 of 10 mutants not run, #228)\n" in f"\n{result.stderr}"
        )
        assert "legacy function src/lovspor/example.py big: 1 of 10 mutants" in result.stderr

    def test_a_failing_results_listing_fails_the_command(
        self, tmp_path: Path, legacy: ModuleType
    ) -> None:
        broken = tmp_path / "mutmut"
        broken.write_text("#!/bin/sh\nexit 5\n", encoding="utf-8")
        broken.chmod(0o755)

        result = self._select(tmp_path, broken, _plan(tmp_path, legacy, {9}))

        assert result.returncode != 0
        assert result.stdout == ""
