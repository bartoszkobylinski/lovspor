#!/usr/bin/env python3
"""Map changed source lines to Mutmut 3 mutant patterns."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass

SOURCE_PREFIX = "src/lovspor/"
CLASS_NAME_SEPARATOR = "ǁ"
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


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


def module_of(path: str) -> str:
    return path.removeprefix("src/").removesuffix(".py").replace("/", ".")


def patterns_for_file(path: str, lines: set[int], source: str) -> list[str]:
    units = keyed_units(source)
    module_pattern = f"{module_of(path)}.*"
    if not units:
        return [module_pattern]
    hit: set[str] = set()
    for line in lines:
        candidates = [unit for unit in units if unit.contains(line)]
        if candidates:
            hit.add(min(candidates, key=lambda unit: unit.span).key)
            continue
        source_line = (
            source.splitlines()[line - 1].strip() if line <= len(source.splitlines()) else ""
        )
        if source_line and not source_line.startswith("#"):
            return [module_pattern]
    return [f"{module_of(path)}.{key}__mutmut_*" for key in sorted(hit)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()

    patterns: list[str] = []
    for path, lines in sorted(changed_lines(args.base).items()):
        file_patterns = patterns_for_file(path, lines, head_source(path))
        patterns.extend(file_patterns)
        if args.explain:
            suffix = "" if file_patterns else " (no mutatable changed function)"
            print(f"  {path}{suffix}", file=sys.stderr)
            for pattern in file_patterns:
                print(f"      {pattern}", file=sys.stderr)
    print("\n".join(patterns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
