"""Register lookup and safe diff handling for the survivor-checking helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/mutant_check.py"
SPEC = importlib.util.spec_from_file_location("mutant_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_find_requires_a_unique_prefix_of_at_least_six_characters() -> None:
    entries = [{"key": "abcdef123"}, {"key": "abcdef456"}, {"key": "short"}]
    assert checker.find(entries, "short") == entries[2]
    assert checker.find(entries, "abcdef1") == entries[0]
    for prefix in ("abcde", "abcdef", "missing"):
        with pytest.raises(SystemExit, match="entries match"):
            checker.find(entries, prefix)


def test_apply_changes_only_the_named_method_and_preserves_indentation() -> None:
    source = (
        "class First:\n"
        "    def value(self):\n"
        "        return 1\n\n"
        "class Second:\n"
        "    def value(self):\n"
        "        return 1\n"
    )
    entry = {
        "class": "Second",
        "symbol": "value",
        "diff": "@@ -1,2 +1,2 @@\n def value(self):\n-    return 1\n+    return 2\n",
    }
    assert checker.apply(source, entry) == source.replace(
        "class Second:\n    def value(self):\n        return 1",
        "class Second:\n    def value(self):\n        return 2",
    )


def test_apply_rejects_stale_or_ambiguous_changes() -> None:
    entry = {"symbol": "value", "diff": "@@ -1 +1 @@\n-    return 1\n+    return 2\n"}
    assert checker.apply("def value():\n    return 3\n", entry) is None
    assert checker.apply("def value():\n    return 1\n    return 1\n", entry) is None
    assert checker.apply("def broken(:\n", entry) is None
    assert checker.apply("def other():\n    return 1\n", entry) is None


@pytest.mark.parametrize(
    ("result", "expected", "message"),
    [(1, 0, "RED"), (0, 1, "GREEN"), (None, 2, "no answer"), (2, 2, "no answer")],
)
def test_verdict_distinguishes_failed_tests_from_errors_and_timeouts(
    result: int | None, expected: int, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checker._verdict({"key": "abcdef123"}, result, 10) == expected
    output = capsys.readouterr().out
    assert message in output
    assert ("Kills-mutant: abcdef123" in output) is (result == 1)
