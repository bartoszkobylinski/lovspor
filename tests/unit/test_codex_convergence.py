"""Unit tests for the codex-tests convergence verdict (issue #248)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "codex_convergence.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_convergence", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[module]; an
    # unregistered module makes that lookup return None at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cc = _load()

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.unit.test_thing" name="test_passes" time="0.1"/>
<testcase classname="tests.unit.test_thing" name="test_new_contract" time="0.1">
  <failure message="assert 2 == 1">x</failure></testcase>
<testcase classname="tests.unit.test_thing" name="test_parametrised[a-b]" time="0.1">
  <failure message="boom">x</failure></testcase>
<testcase classname="tests.unit.test_old" name="test_regressed" time="0.1">
  <error message="TypeError">x</error></testcase>
</testsuite></testsuites>
"""

DIFF = """diff --git a/tests/unit/test_thing.py b/tests/unit/test_thing.py
--- a/tests/unit/test_thing.py
+++ b/tests/unit/test_thing.py
@@ -1,0 +1,8 @@
+def test_new_contract() -> None:
+    assert 2 == 1
+
+
+@pytest.mark.parametrize("x", ["a", "b"])
+def test_parametrised(x: str) -> None:
+    assert x == "a"
+async def test_async_added() -> None: ...
"""


# --- reading the inputs -----------------------------------------------------


def test_round_count_comes_from_the_sticky_comment() -> None:
    body = f"{cc.BLOCKED_PHRASE}.\n---\n{cc.BLOCKED_PHRASE}.\n---\nBLOCKED before the tests ran"
    assert cc.count_blocking_rounds(body) == 2
    assert cc.count_blocking_rounds("") == 0


def test_added_tests_are_read_from_the_diff_including_async_and_parametrised() -> None:
    assert cc.added_tests(DIFF) == {
        cc.TestId("tests/unit/test_thing.py", "test_new_contract"),
        cc.TestId("tests/unit/test_thing.py", "test_parametrised"),
        cc.TestId("tests/unit/test_thing.py", "test_async_added"),
    }


def test_failures_are_read_from_junit_with_params_stripped(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT, encoding="utf-8")

    assert cc.failed_tests(junit) == [
        cc.TestId("tests/unit/test_thing.py", "test_new_contract"),
        cc.TestId("tests/unit/test_thing.py", "test_parametrised"),
        cc.TestId("tests/unit/test_old.py", "test_regressed"),
    ]


# --- the verdict ---------------------------------------------------------------


def _repo_with(tmp_path: Path, source: str) -> Path:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_thing.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_a_broken_pre_existing_test_always_blocks_whatever_the_round(tmp_path: Path) -> None:
    """A regression is not a proposal, and no cap makes it advisory."""
    repo = _repo_with(tmp_path, "def test_new_contract(): ...\n")
    failures = [cc.TestId("tests/unit/test_old.py", "test_regressed")]

    verdict = cc.classify(round_number=99, cap=3, failures=failures, added=set(), repo=repo)

    assert verdict.blocks
    assert verdict.foreign == failures
    assert not verdict.advisory


def test_an_untagged_author_failure_blocks_within_the_cap(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, "def test_new_contract(): ...\n")
    test = cc.TestId("tests/unit/test_thing.py", "test_new_contract")

    verdict = cc.classify(round_number=3, cap=3, failures=[test], added={test}, repo=repo)

    assert verdict.blocking == [test]
    assert verdict.blocks


def test_beyond_the_cap_every_author_failure_is_advisory(tmp_path: Path) -> None:
    """The implementation side has converged; the test side gets its notion of
    diminishing returns from the cap."""
    repo = _repo_with(tmp_path, "def test_new_contract(): ...\n")
    test = cc.TestId("tests/unit/test_thing.py", "test_new_contract")

    verdict = cc.classify(round_number=4, cap=3, failures=[test], added={test}, repo=repo)

    assert verdict.advisory == [test]
    assert not verdict.blocks


def test_a_self_declared_proposal_is_advisory_from_round_one(tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        "import pytest\n\n@pytest.mark.codex_proposal\ndef test_new_contract(): ...\n",
    )
    test = cc.TestId("tests/unit/test_thing.py", "test_new_contract")

    verdict = cc.classify(round_number=1, cap=3, failures=[test], added={test}, repo=repo)

    assert verdict.advisory == [test]
    assert not verdict.blocks


def test_a_proposal_marker_with_arguments_still_counts(tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        'import pytest\n\n@pytest.mark.codex_proposal(reason="x")\ndef test_new_contract(): ...\n',
    )
    assert cc.is_proposal(repo, cc.TestId("tests/unit/test_thing.py", "test_new_contract"))


def test_an_unparseable_test_file_is_not_a_proposal(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, "def test_new_contract(: ...\n")
    assert not cc.is_proposal(repo, cc.TestId("tests/unit/test_thing.py", "test_new_contract"))


# --- marking proposals in place --------------------------------------------------


def test_xfail_is_inserted_above_the_functions_own_decorators(tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        "import pytest\n\n\n"
        '@pytest.mark.parametrize("x", [1])\n'
        "def test_new_contract(x: int) -> None:\n"
        "    assert x == 2\n",
    )

    cc.mark_xfail(repo, cc.TestId("tests/unit/test_thing.py", "test_new_contract"), "why")

    text = (repo / "tests/unit/test_thing.py").read_text(encoding="utf-8")
    assert text == (
        "import pytest\n\n\n"
        '@pytest.mark.xfail(strict=True, reason="why")\n'
        '@pytest.mark.parametrize("x", [1])\n'
        "def test_new_contract(x: int) -> None:\n"
        "    assert x == 2\n"
    )


def test_xfail_marking_adds_the_pytest_import_when_missing(tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        '"""Module docstring."""\n\nfrom __future__ import annotations\n\n\n'
        "def test_new_contract() -> None:\n    assert 1 == 2\n",
    )

    cc.mark_xfail(repo, cc.TestId("tests/unit/test_thing.py", "test_new_contract"), "why")

    text = (repo / "tests/unit/test_thing.py").read_text(encoding="utf-8")
    assert "\nimport pytest\n" in text
    assert text.index("from __future__") < text.index("import pytest")
    assert text.index("import pytest") < text.index("@pytest.mark.xfail")
    compile(text, "test_thing.py", "exec")  # still valid Python


def test_marked_proposal_is_reported_as_xfail_not_failure(tmp_path: Path) -> None:
    """The point of strict xfail: green today, and a hard failure the day the
    proposal is implemented, so the marker cannot silently outlive its reason."""
    repo = _repo_with(
        tmp_path,
        "def test_new_contract() -> None:\n    assert 1 == 2\n",
    )
    cc.mark_xfail(repo, cc.TestId("tests/unit/test_thing.py", "test_new_contract"), "why")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_thing.py",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 xfailed" in result.stdout


def test_marking_a_missing_function_is_an_error_not_a_silent_no_op(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, "def test_other(): ...\n")
    with pytest.raises(LookupError):
        cc.mark_xfail(repo, cc.TestId("tests/unit/test_thing.py", "test_gone"), "why")


# --- the comment -------------------------------------------------------------------


def test_blocking_comment_carries_the_phrase_the_round_counter_reads() -> None:
    """The counter and the comment are one contract: change the phrase in one
    place and every PR's round count silently resets to zero."""
    verdict = cc.Verdict(round=2, cap=3, blocking=[cc.TestId("tests/unit/t.py", "test_x")])
    text = cc.render_comment(verdict, "https://run")
    assert cc.BLOCKED_PHRASE in text
    assert "round 2 of 3" in text


def test_advisory_comment_never_carries_the_blocking_phrase() -> None:
    verdict = cc.Verdict(round=4, cap=3, advisory=[cc.TestId("tests/unit/t.py", "test_x")])
    text = cc.render_comment(verdict, "https://run")
    assert cc.BLOCKED_PHRASE not in text
    assert "ADVISORY" in text
    assert "xfail(strict=True)" in text


# --- end to end ----------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


def test_cli_marks_advisory_proposals_and_writes_the_verdict(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, "def test_existing(): ...\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "tests/unit/test_thing.py").write_text(
        "def test_existing(): ...\n\n\ndef test_new_contract() -> None:\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="tests.unit.test_thing" '
        'name="test_new_contract"><failure message="x">x</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    sticky = tmp_path / "sticky.md"
    sticky.write_text("\n".join([cc.BLOCKED_PHRASE] * 3), encoding="utf-8")  # cap reached

    code = cc.main(
        [
            "--repo",
            str(repo),
            "--before-sha",
            before,
            "--junit",
            str(junit),
            "--sticky-body",
            str(sticky),
            "--cap",
            "3",
            "--verdict",
            str(tmp_path / "v.json"),
            "--comment",
            str(tmp_path / "c.md"),
            "--apply",
        ]
    )

    assert code == 0
    verdict = json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))
    assert verdict == {
        "round": 4,
        "cap": 3,
        "blocks": False,
        "blocking": [],
        "foreign": [],
        "advisory": ["tests/unit/test_thing.py::test_new_contract"],
    }
    assert "@pytest.mark.xfail(strict=True" in (repo / "tests/unit/test_thing.py").read_text()
    assert "ADVISORY" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_cli_sees_tests_in_a_brand_new_untracked_file(tmp_path: Path) -> None:
    """The author's new files are untracked; a plain `git diff` would miss every
    test in them and misclassify each failure as a pre-existing regression —
    blocking, forever, with no author test to mark."""
    repo = _repo_with(tmp_path, "def test_existing(): ...\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "tests/unit/test_brand_new.py").write_text(
        "def test_new_contract() -> None:\n    assert 1 == 2\n", encoding="utf-8"
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="tests.unit.test_brand_new" '
        'name="test_new_contract"><failure message="x">x</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    sticky = tmp_path / "sticky.md"
    sticky.write_text("", encoding="utf-8")

    cc.main(
        [
            "--repo",
            str(repo),
            "--before-sha",
            before,
            "--junit",
            str(junit),
            "--sticky-body",
            str(sticky),
            "--verdict",
            str(tmp_path / "v.json"),
            "--comment",
            str(tmp_path / "c.md"),
        ]
    )

    verdict = json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))
    assert verdict["foreign"] == []
    assert verdict["blocking"] == ["tests/unit/test_brand_new.py::test_new_contract"]
