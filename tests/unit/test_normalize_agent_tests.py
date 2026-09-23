"""Contract tests for scripts/ci/normalize_agent_tests.sh (issues #232, #256).

The normalize step of both agent lanes ran `ruff format` + `ruff check --fix`
and hard-failed on whatever ruff could not fix. A lint that has no safe fix —
RUF001 on a test whose subject IS the ambiguous character, SIM105 on a
`try/except/pass` — then killed the round before the tests ran, and the
pipeline reported that as a pipeline failure: a correct test discarded, and
the cause named was not the cause. Both rounds were recovered by hand with the
same edit — a `# noqa` on the line. The script now makes that edit itself,
announces every suppression, and only fails on a draft ruff cannot read.

The real ruff binary runs here, against a scratch ruff config: the decisions
under test are ruff's own (which fix is safe, what `--add-noqa` can reach),
and a stub would test the stub.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "ci" / "normalize_agent_tests.sh"
_RUFF = Path(sys.executable).with_name("ruff")

pytestmark = pytest.mark.skipif(not _RUFF.is_file(), reason="ruff not in the interpreter's bin")

_CONFIG = 'line-length = 100\n[lint]\nselect = ["E", "F", "SIM", "RUF"]\n'
# Built, not written: a fullwidth L in this file's own source is the RUF001 of
# issue #256, and this module is under the same lint gate as the drafts it tests.
_FULLWIDTH_L = chr(0xFF2C)
_UNFIXABLE_DRAFT = (
    "import contextlib\n"
    "\n\n"
    "def test_unicode_and_suppress():\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        pass\n"
    f'    assert "{_FULLWIDTH_L}" != "L"\n'
)


def _run(tree: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    summary = tree / "summary.md"
    return subprocess.run(
        ["bash", str(_SCRIPT), *paths],
        cwd=tree,
        env={**os.environ, "RUFF": str(_RUFF), "GITHUB_STEP_SUMMARY": str(summary)},
        text=True,
        capture_output=True,
        check=False,
    )


def _draft(tmp_path: Path, source: str) -> Path:
    (tmp_path / "ruff.toml").write_text(_CONFIG, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    draft = tmp_path / "tests" / "test_draft.py"
    draft.write_text(source, encoding="utf-8")
    return draft


class TestALintRuffCannotFixDoesNotEndTheRound:
    def test_the_round_continues_with_the_finding_suppressed_on_its_line(
        self, tmp_path: Path
    ) -> None:
        draft = _draft(tmp_path, _UNFIXABLE_DRAFT)

        result = _run(tmp_path, "tests/")

        assert result.returncode == 0, result.stdout + result.stderr
        text = draft.read_text(encoding="utf-8")
        assert "    try:  # noqa: SIM105\n" in text
        assert f'    assert "{_FULLWIDTH_L}" != "L"  # noqa: RUF001\n' in text

    def test_safe_fixes_are_applied_and_unsafe_ones_are_not(self, tmp_path: Path) -> None:
        """The unused import goes (F401 is a safe fix). The try/except stays
        as written (the SIM105 rewrite is unsafe: it can change what a test
        asserts), suppressed rather than rewritten."""
        draft = _draft(tmp_path, _UNFIXABLE_DRAFT)

        _run(tmp_path, "tests/")

        text = draft.read_text(encoding="utf-8")
        assert "import contextlib" not in text
        assert "contextlib.suppress" not in text

    def test_the_tree_is_clean_for_the_lint_gate_afterwards(self, tmp_path: Path) -> None:
        """Issue #66 stands: what gets pushed must pass the pipeline's own
        `ruff check`, or the agent's commit turns fast-ci red on the next run."""
        _draft(tmp_path, _UNFIXABLE_DRAFT)

        _run(tmp_path, "tests/")
        check = subprocess.run(
            [str(_RUFF), "check", "tests/"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert check.returncode == 0, check.stdout

    def test_every_suppression_is_announced_as_an_annotation(self, tmp_path: Path) -> None:
        """A suppression the owner never sees is a rule silently switched
        off. Each one is a warning annotation on its file and line — the line
        of the file that is pushed, after the safe fixes moved things, not of
        the draft as the agent left it."""
        draft = _draft(tmp_path, _UNFIXABLE_DRAFT)

        result = _run(tmp_path, "tests/")

        pushed = draft.read_text(encoding="utf-8").splitlines()
        try_line = 1 + next(i for i, line in enumerate(pushed) if "noqa: SIM105" in line)
        assert_line = 1 + next(i for i, line in enumerate(pushed) if "noqa: RUF001" in line)
        warnings = [line for line in result.stdout.splitlines() if line.startswith("::warning ")]
        assert len(warnings) == 2, result.stdout
        assert any(
            f"file=tests/test_draft.py,line={try_line}," in w and "SIM105" in w for w in warnings
        )
        assert any(
            f"file=tests/test_draft.py,line={assert_line}," in w and "RUF001" in w for w in warnings
        )

    def test_every_suppression_is_listed_in_the_step_summary(self, tmp_path: Path) -> None:
        _draft(tmp_path, _UNFIXABLE_DRAFT)

        _run(tmp_path, "tests/")
        summary = (tmp_path / "summary.md").read_text(encoding="utf-8")

        assert "# noqa" in summary
        assert "SIM105" in summary
        assert "RUF001" in summary


class TestACleanDraftIsLeftAlone:
    def test_no_annotation_and_no_summary_when_nothing_was_suppressed(self, tmp_path: Path) -> None:
        _draft(tmp_path, "def test_clean():\n    assert True\n")

        result = _run(tmp_path, "tests/")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning" not in result.stdout
        assert not (tmp_path / "summary.md").exists()

    def test_the_default_path_is_the_tests_tree(self, tmp_path: Path) -> None:
        draft = _draft(tmp_path, "import os\n\n\ndef test_clean():\n    assert True\n")

        result = _run(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "import os" not in draft.read_text(encoding="utf-8")


class TestADraftRuffCannotReadStillFails:
    def test_a_syntax_error_fails_the_step(self, tmp_path: Path) -> None:
        """A `# noqa` cannot suppress a parse error, and a draft that does not
        parse is not a test. This is the case the old hard-fail was right
        about, and it keeps failing here — into the existing pipeline-failure
        escalation, with the work preserved."""
        _draft(tmp_path, "def test_broken(:\n    pass\n")

        result = _run(tmp_path, "tests/")

        assert result.returncode != 0
        assert "::warning" not in result.stdout
