#!/usr/bin/env python3
"""Map changed source lines to Mutmut 3 mutant patterns."""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass

SOURCE_PREFIX = "src/lovspor/"
CLASS_NAME_SEPARATOR = "ǁ"
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
UNMEASURED_PREFIX = "unmeasured changed lines: "
# Tokens that carry no code: a changed line made only of these is not a
# change any mutant could measure, wherever it sits.
_NON_CODE_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
)


@dataclass(frozen=True)
class Unit:
    key: str
    start: int
    end: int

    def contains(self, line: int) -> bool:
        return self.start <= line <= self.end

    @property
    def span(self) -> int:
        return self.end - self.start


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def head_source(path: str) -> str:
    try:
        return _git("show", f"HEAD:{path}")
    except subprocess.CalledProcessError:
        return ""


def changed_lines(base: str) -> dict[str, set[int]]:
    diff = _git(
        "diff",
        "--unified=0",
        "--diff-filter=ACMR",
        f"{base}...HEAD",
        "--",
        SOURCE_PREFIX,
    )
    out: dict[str, set[int]] = {}
    current: str | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[len("+++ b/") :]
            current = path if path.endswith(".py") else None
            if current:
                out.setdefault(current, set())
            continue
        if current is None:
            continue
        if match := HUNK_RE.match(raw):
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            if count == 0:
                out[current].add(max(start, 1))
            else:
                out[current].update(range(start, start + count))
    return {path: lines for path, lines in out.items() if lines}


def is_mutatable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not node.decorator_list:
        return True
    if len(node.decorator_list) != 1:
        return False
    decorator = node.decorator_list[0]
    return isinstance(decorator, ast.Name) and decorator.id in {
        "classmethod",
        "staticmethod",
    }


def keyed_units(source: str) -> list[Unit]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    units: list[Unit] = []

    def visit(body: list[ast.stmt], class_name: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not is_mutatable(node):
                    continue
                prefix = (
                    f"x{CLASS_NAME_SEPARATOR}{class_name}{CLASS_NAME_SEPARATOR}"
                    if class_name
                    else "x_"
                )
                start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
                units.append(Unit(f"{prefix}{node.name}", start, node.end_lineno or node.lineno))
            elif isinstance(node, ast.ClassDef):
                if node.decorator_list:
                    continue
                nested_name = node.name if not class_name else f"{class_name}.{node.name}"
                visit(node.body, nested_name)

    visit(tree.body)
    return units


def _qualified(class_name: str, name: str) -> str:
    return f"{class_name}.{name}" if class_name else name


_BODY_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_ATTRIBUTE_OWNERS = (ast.Module, ast.ClassDef)


def _is_string(stmt: ast.stmt) -> bool:
    """A plain string statement; an f-string or bytes literal is not one."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _docstrings(owner: ast.AST) -> list[ast.stmt]:
    """The owner's docstrings: a string as its FIRST statement, and in a
    module or class body a string right after an assignment (the attribute
    docstring Sphinx and PEP 257 recognise). Any other string statement is
    an expression like any other and stays code.
    """
    if not isinstance(owner, _BODY_OWNERS) or not owner.body:
        return []
    found = [owner.body[0]] if _is_string(owner.body[0]) else []
    if isinstance(owner, _ATTRIBUTE_OWNERS):
        pairs = zip(owner.body, owner.body[1:], strict=False)
        found += [s for prev, s in pairs if isinstance(prev, (ast.Assign, ast.AnnAssign))]
    return [s for s in found if _is_string(s)]


def _region_of(node: ast.stmt, class_name: str) -> Unit | None:
    """The unmeasured region a statement outside every mutatable function forms."""
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    end = node.end_lineno or node.lineno
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if is_mutatable(node):
            return None
        return Unit(f"decorated function {_qualified(class_name, node.name)}", start, end)
    if isinstance(node, ast.ClassDef):
        return Unit(f"decorated class {_qualified(class_name, node.name)}", start, end)
    return Unit(f"class body {class_name}" if class_name else "module level", start, end)


def unmeasured_regions(tree: ast.Module) -> list[Unit]:
    """Code mutmut 3.8.0 never mutates, or this scope never selects.

    Mutmut copies module-level and class-body statements unmutated and skips
    every decorated function but a lone `@staticmethod`/`@classmethod`
    (`mutmut/mutation/file_mutation.py`, `_skip_node_and_children`); the
    methods of a decorated class are skipped by `keyed_units`. A change there
    cannot be brought into scope, so it is reported instead (#289, #292).
    """
    regions: list[Unit] = []

    def visit(body: list[ast.stmt], class_name: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef) and not node.decorator_list:
                visit(node.body, _qualified(class_name, node.name))
            elif region := _region_of(node, class_name):
                regions.append(region)

    visit(tree.body)
    return regions


def code_lines(source: str) -> set[int]:
    """Lines carrying at least one code token: not blank, not comment-only."""
    lines: set[int] = set()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type not in _NON_CODE_TOKENS:
            lines.update(range(token.start[0], token.end[0] + 1))
    return lines


def inert_lines(tree: ast.Module) -> set[int]:
    """Lines of every import and every docstring, at any depth, even inside a
    decorated region: no mutant could measure them there either."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        imports = [node] if isinstance(node, (ast.Import, ast.ImportFrom)) else []
        for inert in [*imports, *_docstrings(node)]:
            lines.update(range(inert.lineno, (inert.end_lineno or inert.lineno) + 1))
    return lines


def _ranges(lines: list[int]) -> str:
    spans: list[list[int]] = []
    for line in sorted(lines):
        if spans and line == spans[-1][1] + 1:
            spans[-1][1] = line
        else:
            spans.append([line, line])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def unmeasured_notices(path: str, lines: set[int], source: str) -> list[str]:
    """One notice per region label whose changed code lines no mutant
    measures: every module-level statement shares `module level`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    changed = (lines & code_lines(source)) - inert_lines(tree)
    hits: dict[str, list[int]] = {}
    for region in unmeasured_regions(tree):
        hit = [line for line in changed if region.contains(line)]
        if hit:
            hits.setdefault(region.key, []).extend(hit)
    return [f"{UNMEASURED_PREFIX}{path}:{_ranges(hit)} ({key})" for key, hit in hits.items()]


def module_of(path: str) -> str:
    return path.removeprefix("src/").removesuffix(".py").replace("/", ".")


def patterns_for_file(path: str, lines: set[int], source: str) -> list[str]:
    try:
        ast.parse(source)
    except SyntaxError:
        return [f"{module_of(path)}.*"]

    units = keyed_units(source)
    if not units:
        return []
    hit: set[str] = set()
    for line in lines:
        candidates = [unit for unit in units if unit.contains(line)]
        if candidates:
            hit.add(min(candidates, key=lambda unit: unit.span).key)
    return [f"{module_of(path)}.{key}__mutmut_*" for key in sorted(hit)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()

    patterns: list[str] = []
    for path, lines in sorted(changed_lines(args.base).items()):
        source = head_source(path)
        file_patterns = patterns_for_file(path, lines, source)
        patterns.extend(file_patterns)
        if args.explain:
            suffix = "" if file_patterns else " (no mutatable changed function)"
            print(f"  {path}{suffix}", file=sys.stderr)
            for pattern in file_patterns:
                print(f"      {pattern}", file=sys.stderr)
            for notice in unmeasured_notices(path, lines, source):
                print(notice, file=sys.stderr)
    print("\n".join(patterns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
