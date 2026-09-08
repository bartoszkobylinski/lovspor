#!/usr/bin/env python3
"""Convergence verdict for the codex-tests job (issue #248).

The independent test author treats "I can write a failing test" as "the
implementation is wrong". On a fresh PR those are the same thing. On a mature
one they diverge: an author with unlimited rounds can always invent a tighter
contract than any spec or corpus fact requires, every proposal is individually
defensible, and nothing in the loop ever concludes "the contract is satisfied".
Seven rounds on PR #257 and fourteen on #244 are the record.

This script is the fixed point. After the author's tests have run it decides,
from the junit result and the PR's own history, which failures BLOCK and which
are ADVISORY:

* A failing test the author did not add — a pre-existing test broken by the
  PR — always blocks. That is a regression, not a proposal.
* A failing author test carrying ``@pytest.mark.codex_proposal`` is advisory:
  the author itself said "this is a stricter contract I propose", not "this
  violates the PR's stated contract or corpus reality".
* Once the PR has already been blocked ``--cap`` times by this job, EVERY new
  author failure is advisory. The implementation side has converged by then;
  the test side has no notion of diminishing returns, so the cap supplies one.

Advisory failures are not thrown away. With ``--apply`` each one is marked
``xfail(strict=True)`` in place and committed with the rest of the round: the
proposal stays in the tree, visible, and the day the contract IS implemented
the strict xfail turns into a hard failure that says "remove this marker". The
owner decides which proposals to implement; the pipeline no longer decides for
them by staying red.

Round counting reads the pipeline's sticky comment: a blocking round leaves no
commit (the tests were not pushed), so the comment is the only durable record.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

BLOCKED_PHRASE = "Codex-authored tests fail against this head"
PROPOSAL_MARKER = "codex_proposal"


@dataclass(frozen=True)
class TestId:
    """A test by file and name. ``name`` is dotted for a method —
    ``TestFoo.test_bar`` — because two classes may share a method name and an
    xfail marker on the wrong one silences a passing test while the failing one
    keeps failing."""

    file: str
    name: str

    @property
    def node(self) -> str:
        return f"{self.file}::{self.name.replace('.', '::')}"

    @property
    def leaf(self) -> str:
        """The bare function name — what a diff line shows."""
        return self.name.rsplit(".", 1)[-1]


@dataclass
class Verdict:
    round: int
    cap: int
    blocking: list[TestId] = field(default_factory=list)
    foreign: list[TestId] = field(default_factory=list)
    advisory: list[TestId] = field(default_factory=list)
    # pytest itself failed in a way the junit does not account for — a
    # collection error, a crash, an absent report. Not a test verdict at all,
    # and never something to wave through as "no failures parsed".
    pipeline_error: str | None = None

    @property
    def blocks(self) -> bool:
        return bool(self.blocking or self.foreign or self.pipeline_error)

    def as_json(self) -> dict[str, object]:
        return {
            "round": self.round,
            "cap": self.cap,
            "blocks": self.blocks,
            "blocking": [t.node for t in self.blocking],
            "foreign": [t.node for t in self.foreign],
            "advisory": [t.node for t in self.advisory],
            "pipeline_error": self.pipeline_error,
        }


def count_blocking_rounds(sticky_body: str) -> int:
    """How many times this job has already blocked the PR."""
    return sticky_body.count(BLOCKED_PHRASE)


def added_tests(repo: Path, before_sha: str) -> set[TestId]:
    """Test functions that exist now and did not exist at ``before_sha``.

    Computed from the AST of each changed test file at both revisions — not
    from the diff. A diff line shows a bare ``def test_x`` and cannot say which
    class it lives in, so a new ``TestB.test_x`` would let a pre-existing
    ``TestA.test_x`` pass as the author's; the contract says a pre-existing
    failure always blocks, so the names must be exact.
    """
    changed = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", before_sha, "--", "tests/"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    found: set[TestId] = set()
    for file in changed:
        if not file.endswith(".py"):
            continue
        now = repo / file
        after = _test_names(now.read_text(encoding="utf-8")) if now.is_file() else set()
        shown = subprocess.run(  # noqa: S603
            ["git", "show", f"{before_sha}:{file}"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        before = _test_names(shown.stdout) if shown.returncode == 0 else set()
        found.update(TestId(file, name) for name in after - before)
    return found


def _test_names(source: str) -> set[str]:
    """Dotted names of every test function in ``source``; empty if unparseable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()

    def walk(scope: ast.Module | ast.ClassDef, prefix: str) -> None:
        for node in scope.body:
            if isinstance(node, ast.ClassDef):
                walk(node, f"{prefix}{node.name}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                names.add(f"{prefix}{node.name}")

    walk(tree, "")
    return names


def failed_tests(junit_path: Path) -> list[TestId]:
    """Failing and erroring testcases from a pytest junit report."""
    root = ET.parse(junit_path).getroot()  # noqa: S314 - pytest-written file on the runner
    failures: list[TestId] = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        classname = case.get("classname", "")
        name = re.sub(r"\[.*\]$", "", case.get("name", ""))
        file = case.get("file")
        if file:
            # module path = the file's dotted form; whatever follows is classes
            module = file.removesuffix(".py").replace("/", ".")
            classes = classname.removeprefix(module).strip(".")
        else:
            # No file attribute: assume the last capitalised segments are classes
            parts = classname.split(".")
            split = len(parts)
            while split > 1 and parts[split - 1][:1].isupper():
                split -= 1
            file = "/".join(parts[:split]) + ".py"
            classes = ".".join(parts[split:])
        failures.append(TestId(file, f"{classes}.{name}" if classes else name))
    return failures


def is_proposal(repo: Path, test: TestId) -> bool:
    """Whether the test carries ``@pytest.mark.codex_proposal``."""
    path = repo / test.file
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    node = _find_function(tree, test.name)
    return node is not None and any(_is_proposal_decorator(d) for d in node.decorator_list)


def _find_function(tree: ast.Module, dotted: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Resolve ``Class.Inner.test_x`` or ``test_x`` to its definition node."""
    *classes, leaf = dotted.split(".")
    scope: ast.Module | ast.ClassDef = tree
    for name in classes:
        found = next(
            (n for n in scope.body if isinstance(n, ast.ClassDef) and n.name == name), None
        )
        if found is None:
            return None
        scope = found
    for node in scope.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == leaf:
            return node
    return None


def _is_proposal_decorator(node: ast.expr) -> bool:
    """Match ``pytest.mark.codex_proposal`` / ``mark.codex_proposal``, bare or called.

    Only the pytest marker is the contract: a same-named attribute on any other
    object (``@custom.codex_proposal``) is not a proposal.
    """
    target = node.func if isinstance(node, ast.Call) else node
    if not (isinstance(target, ast.Attribute) and target.attr == PROPOSAL_MARKER):
        return False
    mark = target.value
    if isinstance(mark, ast.Name):
        return mark.id == "mark"
    return (
        isinstance(mark, ast.Attribute)
        and mark.attr == "mark"
        and isinstance(mark.value, ast.Name)
        and mark.value.id == "pytest"
    )


def classify(
    *,
    round_number: int,
    cap: int,
    failures: list[TestId],
    added: set[TestId],
    repo: Path,
    pytest_status: int = 0,
) -> Verdict:
    verdict = Verdict(round=round_number, cap=cap)
    for test in failures:
        if test not in added:
            verdict.foreign.append(test)
        elif round_number > cap or is_proposal(repo, test):
            verdict.advisory.append(test)
        else:
            verdict.blocking.append(test)
    # pytest: 0 all passed, 1 tests failed, anything else is not a test verdict —
    # 2 interrupted, 3 internal error, 4 usage error, 5 nothing collected. A
    # partial junit from an interrupted run is exactly what must not be read
    # as "here are all the failures".
    if pytest_status not in (0, 1):
        verdict.pipeline_error = (
            f"pytest exited {pytest_status}: interrupted, internal error, usage error or "
            "nothing collected — a partial junit is not a test verdict"
        )
    elif pytest_status == 1 and not failures:
        verdict.pipeline_error = (
            "pytest exited 1 with no parsable failures — an absent or incomplete junit "
            "report; not a test verdict"
        )
    return verdict


def mark_xfail(repo: Path, test: TestId, reason: str) -> None:
    """Insert a strict xfail above the function, before its own decorators.

    Text insertion at a known line rather than an AST rewrite: the file must
    stay byte-for-byte the author's except for one added line, so review sees
    exactly what the pipeline did.
    """
    path = repo / test.file
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    node = _find_function(tree, test.name)
    if node is None:
        raise LookupError(f"{test.node} not found for xfail marking")
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    indent = re.match(r"\s*", lines[first - 1]).group(0)  # type: ignore[union-attr]
    lines.insert(first - 1, f'{indent}@pytest.mark.xfail(strict=True, reason="{reason}")\n')
    text = "".join(lines)
    if not re.search(r"^import pytest$", text, re.M):
        text = _add_pytest_import(text)
    path.write_text(text, encoding="utf-8")


def _add_pytest_import(text: str) -> str:
    """Place ``import pytest`` after ``from __future__`` and the docstring."""
    lines = text.splitlines(keepends=True)
    insert_at = 0
    tree = ast.parse(text)
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
    ):
        insert_at = tree.body[0].end_lineno or 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            insert_at = max(insert_at, node.end_lineno or 0)
    lines.insert(insert_at, "\nimport pytest\n" if insert_at else "import pytest\n")
    return "".join(lines)


def render_comment(verdict: Verdict, run_url: str) -> str:
    lines: list[str] = []
    if verdict.blocks:
        lines.append(
            f"codex-tests BLOCKED (round {verdict.round} of {verdict.cap} blocking rounds)."
        )
        if verdict.pipeline_error:
            lines.append("")
            lines.append(f"{verdict.pipeline_error}.")
        if verdict.foreign:
            lines.append("")
            lines.append("Pre-existing tests broken by this head — a regression, never advisory:")
            lines.extend(f"- `{t.node}`" for t in verdict.foreign)
        if verdict.blocking:
            lines.append("")
            lines.append(f"{BLOCKED_PHRASE}:")
            lines.extend(f"- `{t.node}`" for t in verdict.blocking)
    else:
        lines.append(
            f"codex-tests ADVISORY (round {verdict.round}; blocking cap {verdict.cap} "
            f"{'reached' if verdict.round > verdict.cap else 'not reached'})."
        )
        lines.append("")
        lines.append(
            "The author's failing tests are stricter-contract proposals, not violations "
            "of the PR's stated contract. Each is committed as `xfail(strict=True)`: "
            "implement it and the marker turns into a failure that says to remove it, "
            "or delete it. Owner's call (#248)."
        )
        lines.extend(f"- `{t.node}`" for t in verdict.advisory)
    if verdict.advisory and verdict.blocks:
        lines.append("")
        lines.append(
            "Also advisory this round (committed as strict xfail once the blockers clear):"
        )
        lines.extend(f"- `{t.node}`" for t in verdict.advisory)
    lines.append("")
    lines.append(f"Run: {run_url}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--before-sha", required=True, help="head before the author ran")
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--sticky-body", type=Path, required=True, help="pipeline sticky comment")
    parser.add_argument("--cap", type=int, default=3, help="blocking rounds before advisory")
    parser.add_argument(
        "--pytest-status", type=int, default=0, help="exit status of the pytest run"
    )
    parser.add_argument("--run-url", default="")
    parser.add_argument("--verdict", type=Path, required=True, help="write verdict JSON here")
    parser.add_argument("--comment", type=Path, required=True, help="write comment markdown here")
    parser.add_argument("--apply", action="store_true", help="mark advisory tests xfail in place")
    args = parser.parse_args(argv)

    # The author's NEW files are untracked and `git diff --name-only` would miss
    # them — intent-to-add makes them visible without staging content (the same
    # trick the workflow's preserve steps use; the push step's `git add -A`
    # supersedes it).
    subprocess.run(["git", "add", "-N", "tests/"], cwd=args.repo, check=True)  # noqa: S607
    sticky = args.sticky_body.read_text(encoding="utf-8") if args.sticky_body.is_file() else ""
    verdict = classify(
        round_number=count_blocking_rounds(sticky) + 1,
        cap=args.cap,
        failures=failed_tests(args.junit) if args.junit.is_file() else [],
        added=added_tests(args.repo, args.before_sha),
        repo=args.repo,
        pytest_status=args.pytest_status,
    )
    if args.apply and not verdict.blocks:
        for test in verdict.advisory:
            mark_xfail(
                args.repo,
                test,
                f"codex proposal, round {verdict.round} — owner decision, see #248",
            )
    args.verdict.write_text(json.dumps(verdict.as_json(), indent=2) + "\n", encoding="utf-8")
    args.comment.write_text(render_comment(verdict, args.run_url), encoding="utf-8")
    print(json.dumps(verdict.as_json()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
