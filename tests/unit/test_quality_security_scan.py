"""The fail-closed security scan (issue #323 Phase E, issue #253).

Every test builds a real tree under ``tmp_path/src`` and runs the real checker
over it, bandit included: a gate is only as good as the rejection it produces,
and the defect this checker exists for was bandit reporting success while it
had read less than it was asked to.

The one exception is the crashing-scanner test, which points ``--bandit-module``
at a module that fails on purpose. Nothing else is stubbed.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "quality" / "check_security_scan.py"

CLEAN = "def add(a: int, b: int) -> int:\n    return a + b\n"
MEDIUM = 'def where() -> str:\n    return "/tmp/hardcoded"\n'  # B108
HIGH = "import hashlib\n\n\ndef digest(b: bytes) -> str:\n    return hashlib.md5(b).hexdigest()\n"
LOW = "import subprocess\n\nsubprocess.run('ls', shell=True)\n"  # B602 on a literal
UNPARSEABLE = "def broken(:\n"


def _load() -> ModuleType:
    name = "check_security_scan_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scan = _load()


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    return root


def _check(root: Path, *extra: str) -> tuple[int, list[str]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = scan.main(["--root", str(root), *extra])
    failures = [line for line in out.getvalue().splitlines() if line.startswith("FAIL")]
    return code, failures


def _output(root: Path, *extra: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        scan.main(["--root", str(root), *extra])
    return out.getvalue()


class TestFindings:
    def test_a_medium_finding_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": MEDIUM})

        code, failures = _check(root)

        assert code == 1
        assert len(failures) == 1
        assert failures[0].startswith("FAIL security B108: src/mod.py:2 MEDIUM ")

    def test_a_high_finding_fails(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": HIGH})

        code, failures = _check(root)

        assert code == 1
        assert len(failures) == 1
        assert failures[0].startswith("FAIL security B324: src/mod.py:5 HIGH ")

    def test_a_low_finding_alone_passes(self, tmp_path: Path) -> None:
        """LOW is noise at gate level; ruff's flake8-bandit rules carry that
        weight repo-wide on every commit. This gate is about MEDIUM and above."""
        root = _tree(tmp_path, {"mod.py": LOW})

        assert _check(root) == (0, [])

    def test_a_clean_tree_passes(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": CLEAN, "pkg/other.py": CLEAN})

        assert _check(root) == (0, [])

    def test_a_passing_tree_reports_what_it_scanned(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"a.py": CLEAN, "b.py": CLEAN})

        assert _output(root) == "security: OK, 2 files scanned, no findings at MEDIUM or above\n"


class TestCoverageIsNotSilentlyNarrowed:
    """Issue #253: bandit skipped three modules and still exited 0."""

    def test_an_unparseable_file_fails_even_with_no_findings(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"ok.py": CLEAN, "bad.py": UNPARSEABLE})

        code, failures = _check(root)

        assert code == 1
        assert failures == [
            "FAIL security unreadable: src/bad.py syntax error while parsing AST from file"
        ]

    def test_an_unreadable_file_fails_even_with_no_findings(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"ok.py": CLEAN, "secret.py": CLEAN})
        secret = root / "src" / "secret.py"
        secret.chmod(0o000)
        try:
            if os.access(secret, os.R_OK):  # root can read anything; the gate is not the test
                pytest.skip("the current user can read a mode-000 file")

            code, failures = _check(root)
        finally:
            secret.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert code == 1
        assert failures == ["FAIL security unreadable: src/secret.py Permission denied"]

    def test_a_file_the_scan_never_visited_fails(self, tmp_path: Path) -> None:
        """A file present on disk and absent from the report is the narrowing
        `errors[]` cannot see: bandit's own default excludes drop a tree without
        reporting an error for it."""
        root = _tree(tmp_path, {"ok.py": CLEAN, ".tox/hidden.py": CLEAN})

        code, failures = _check(root)

        assert code == 1
        assert failures == ["FAIL security unscanned: src/.tox/hidden.py not in the scan report"]

    def test_bytecode_caches_are_not_reported_as_unscanned(self, tmp_path: Path) -> None:
        """bandit skips __pycache__ by default and so must the file census, or
        every run with a warm cache fails for a reason nobody can repair."""
        root = _tree(tmp_path, {"ok.py": CLEAN, "__pycache__/ok.cpython-312.py": CLEAN})

        assert _check(root) == (0, [])

    def test_findings_and_narrowed_coverage_are_reported_together(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"bad.py": UNPARSEABLE, "mod.py": MEDIUM})

        code, failures = _check(root)

        assert code == 1
        assert [line.split(":")[0] for line in failures] == [
            "FAIL security unreadable",
            "FAIL security B108",
        ]


class TestCouldNotRun:
    def test_a_crashing_scanner_is_exit_2_not_a_pass(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": CLEAN})
        (root / "crash.py").write_text("import sys\n\nsys.exit(3)\n", encoding="utf-8")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scan.main(["--root", str(root), "--bandit-module", "crash"])

        assert code == 2
        assert out.getvalue().startswith("ERROR security: ")
        assert "FAIL" not in out.getvalue()

    def test_a_scanner_that_writes_no_report_is_exit_2(self, tmp_path: Path) -> None:
        """Exit 1 means 'findings'. A missing module also exits 1, so the report,
        not the exit status, is what decides the run happened."""
        root = _tree(tmp_path, {"mod.py": CLEAN})

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scan.main(["--root", str(root), "--bandit-module", "no_such_module_here"])

        assert code == 2
        assert out.getvalue().startswith("ERROR security: ")

    def test_a_missing_scope_is_exit_2_not_a_clean_scan(self, tmp_path: Path) -> None:
        """bandit answers a nonexistent path with exit 0 and an empty result."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scan.main(["--root", str(tmp_path)])

        assert code == 2
        assert out.getvalue().startswith("ERROR security: ")

    def test_an_old_interpreter_is_refused_before_anything_is_scanned(self) -> None:
        """The cause of #253: bandit's AST parse under 3.11 could not read
        PEP 695 syntax, so it skipped the file and reported success."""
        with pytest.raises(scan.SecurityScanError, match="3.12"):
            scan.require_interpreter((3, 11, 9))

        assert scan.require_interpreter((3, 12, 0)) is None


class TestTheCheckedInTree:
    def test_the_repository_source_scans_clean(self) -> None:
        """The real run, on real code. It is also the standing proof that #253's
        three modules are read: an unreadable file here is a failure, not a
        footnote."""
        if "MUTANT_UNDER_TEST" in os.environ:
            pytest.skip("mutmut rewrites src/ into trampolines; normal runs check the real tree")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scan.main([])

        assert code == 0, out.getvalue()

    @pytest.mark.parametrize(
        "module", ["retry.py", "temporal.py", "temporal_events.py"]
    )  # the three files of #253
    def test_the_modules_of_issue_253_are_actually_scanned(self, module: str) -> None:
        if "MUTANT_UNDER_TEST" in os.environ:
            pytest.skip("mutmut rewrites src/ into trampolines; normal runs check the real tree")

        report = scan.run_bandit(scan.ROOT, scan.SCOPE, scan.BANDIT_MODULE)

        assert f"src/lovspor/{module}" in scan.scanned_files(report)

    def test_runs_standalone_as_one_command(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, {"mod.py": MEDIUM})

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "FAIL security B108: src/mod.py:2 MEDIUM " in result.stdout
