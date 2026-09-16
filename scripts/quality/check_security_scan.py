#!/usr/bin/env python3
"""A security scan that fails closed on narrowed coverage (issue #323, Phase E).

Runs bandit over ``<root>/src`` and refuses to report success unless the scan
actually read everything it was asked to. The check fails when:

    a MEDIUM or HIGH finding exists
    a file was unreadable or unparseable (bandit's ``errors[]``)
    a file on disk never appears in the report at all
    bandit itself could not run

Why the middle two exist
------------------------
Bandit reports a file it could not read in a footer and still exits 0. Issue
#253: three modules under ``src/lovspor`` -- ``retry.py``, ``temporal.py`` and
``temporal_events.py`` -- were skipped with "syntax error while parsing AST from
file" because the interpreter running bandit was older than the PEP 695 syntax
in them, and every run reported a clean scan. The skip list grew from two files
to three across sessions without the gate ever changing colour. A permission
error behaves the same way: ``errors[]`` gains an entry, the exit status does
not move.

So the exit status of bandit is not the verdict here; the report is. A finding
and a file that was never read are both failures, because "no findings" from a
scan that read less than the tree is a weaker claim than it looks.

The file census is the part ``errors[]`` cannot give. A file dropped by an
exclusion -- a glob that stopped matching, a tree bandit skips by default --
produces no error and no result; it simply is not in the report. Comparing the
report against the ``.py`` files on disk is what turns that silence into a
failure.

The interpreter is pinned mechanically rather than by habit: the run is refused
below Python 3.12 instead of trusting a caller to remember ``--python 3.12``.
That is #253's cause removed, not its symptom suppressed.

Scope
-----
``src/`` only, which is the repository's declared bandit posture
(``docs/decisions.md`` §8) and where #253 happened. ``tests/`` and ``scripts/``
are covered by ruff's flake8-bandit (``S``) rules, which run repo-wide in the
fast gate on every commit and whose waivers are the ``# noqa`` comments already
reviewed in those trees. Bandit does not read ``# noqa``, so scanning them here
would re-raise four already-adjudicated findings under a second waiver
vocabulary.

``--bandit-module`` exists so the tests can point the check at a scanner that
fails on purpose. The gate's real invocation is pinned in
``scripts/quality/verify-deep.sh`` and in fast-ci, and by the tests over both.

Exit status: 0 clean, 1 a finding or narrowed coverage, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "src"
BANDIT_MODULE = "bandit"
MIN_PYTHON = (3, 12)
SEVERITIES = frozenset({"MEDIUM", "HIGH"})
# The one bandit default exclusion that occurs inside a source tree. Anything
# else bandit drops has no entry here and so surfaces as `unscanned`, which is
# the safe direction: a spurious red is repairable, a silent green is not.
EXCLUDED_DIRS = frozenset({"__pycache__"})
UNREADABLE, UNSCANNED, FINDING = 0, 1, 2


class SecurityScanError(Exception):
    """The scan could not run. Never reported as a pass."""


@dataclass(frozen=True, order=True)
class Failure:
    group: int
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class Report:
    failures: list[Failure]
    summary: str


def require_interpreter(version: tuple[int, int, int]) -> None:
    """Refuse the run below 3.12 rather than silently scanning less (#253)."""
    if version < MIN_PYTHON:
        got = ".".join(str(part) for part in version)
        raise SecurityScanError(
            f"bandit must run on Python 3.12 or newer to parse this source, got {got}; "
            "an older parser skips files and still exits 0 (issue #253)"
        )


def _command(scope: str, report: Path, module: str) -> list[str]:
    """`-o` matters: bandit writes progress to stdout, so piping JSON breaks."""
    command = [sys.executable, "-m", module, "-r", scope]
    return [*command, "-ll", "-f", "json", "-o", str(report), "-q"]


def run_bandit(root: Path, scope: str, module: str) -> dict[str, object]:
    if not (root / scope).is_dir():
        raise SecurityScanError(f"no scan scope at {root / scope}")
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "bandit.json"
        result = subprocess.run(  # noqa: S603
            _command(scope, report, module),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return _read_report(report, result)


def _read_report(report: Path, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The report decides whether a scan happened. Exit 1 means 'findings', but
    a missing module exits 1 too, so the status alone cannot tell them apart."""
    if not report.is_file():
        detail = result.stderr.strip().splitlines()[-1:] or ["no output"]
        raise SecurityScanError(f"bandit wrote no report (exit {result.returncode}): {detail[0]}")
    if result.returncode not in (0, 1):
        raise SecurityScanError(f"bandit exited {result.returncode}: {result.stderr.strip()[:200]}")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SecurityScanError(f"bandit report is not readable JSON: {error}") from error
    if not isinstance(data, dict):
        raise SecurityScanError("bandit report is not a JSON object")
    return data


def _relative(root: Path, raw: str) -> str:
    path = Path(raw)
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scanned_files(report: dict[str, object]) -> set[str]:
    """Every file the report accounts for. `_totals` is the summary row."""
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise SecurityScanError("bandit report carries no metrics")
    return {str(key) for key in metrics if key != "_totals"}


def disk_files(root: Path, scope: str) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in (root / scope).rglob("*.py")
        if path.is_file() and not EXCLUDED_DIRS & set(path.relative_to(root).parts)
    }


def _errors(root: Path, report: dict[str, object]) -> list[tuple[str, str]]:
    raw = report.get("errors")
    if not isinstance(raw, list):
        raise SecurityScanError("bandit report carries no errors list")
    return [
        (_relative(root, str(item.get("filename"))), str(item.get("reason")))
        for item in raw
        if isinstance(item, dict)
    ]


def _error_failures(root: Path, report: dict[str, object]) -> list[Failure]:
    return [
        Failure(UNREADABLE, path, 0, f"FAIL security unreadable: {path} {reason}")
        for path, reason in _errors(root, report)
    ]


def _unscanned_failures(root: Path, scope: str, report: dict[str, object]) -> list[Failure]:
    """A file bandit never visited leaves no error and no result, only a gap."""
    known = scanned_files(report) | {path for path, _ in _errors(root, report)}
    return [
        Failure(UNSCANNED, path, 0, f"FAIL security unscanned: {path} not in the scan report")
        for path in sorted(disk_files(root, scope) - known)
    ]


def _finding_failures(root: Path, report: dict[str, object]) -> list[Failure]:
    raw = report.get("results")
    if not isinstance(raw, list):
        raise SecurityScanError("bandit report carries no results list")
    items = [item for item in raw if isinstance(item, dict)]
    return [_finding(root, item) for item in items if str(item.get("issue_severity")) in SEVERITIES]


def _finding(root: Path, item: dict[str, object]) -> Failure:
    path = _relative(root, str(item.get("filename")))
    line = int(str(item.get("line_number") or 0))
    text = (
        f"FAIL security {item.get('test_id')}: {path}:{line} "
        f"{item.get('issue_severity')} {item.get('issue_text')}"
    )
    return Failure(FINDING, path, line, text)


def check(root: Path, scope: str = SCOPE, module: str = BANDIT_MODULE) -> Report:
    require_interpreter(sys.version_info[:3])
    report = run_bandit(root, scope, module)
    failures = sorted(
        [
            *_error_failures(root, report),
            *_unscanned_failures(root, scope, report),
            *_finding_failures(root, report),
        ]
    )
    return Report(failures, _summary(failures, scanned_files(report)))


def _summary(failures: list[Failure], scanned: set[str]) -> str:
    if failures:
        return f"security: {len(failures)} failure(s) over {len(scanned)} scanned file(s)"
    return f"security: OK, {len(scanned)} files scanned, no findings at MEDIUM or above"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed security scan (issue #323).")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root; scope is <root>/src"
    )
    parser.add_argument("--scope", default=SCOPE, help=f"directory under the root, default {SCOPE}")
    parser.add_argument(
        "--bandit-module",
        default=BANDIT_MODULE,
        help="module run with -m as the scanner; a test seam, not a waiver",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = check(args.root.resolve(), args.scope, args.bandit_module)
    except SecurityScanError as error:
        print(f"ERROR security: {error}")
        return 2
    for failure in report.failures:
        print(failure.text)
    print(report.summary)
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
