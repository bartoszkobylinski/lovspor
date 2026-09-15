#!/usr/bin/env python3
"""Size and complexity ratchets over production code (issue #323, Phase B).

Enforces the CLAUDE.md code rules on every Python file under ``<root>/src``:

    function-lines        <= 20   code lines in a function body
    function-params       <= 4    parameters
    function-complexity   <= 10   cyclomatic complexity
    file-lines            <= 700  physical lines in a file

Code already above a limit is recorded in the baseline
(``scripts/quality/ratchet-baseline.toml``), keyed by path + qualified name --
never by line number -- with the measured value and a mandatory ``reason``. A
baselined function or file passes while it stays at its recorded value and
fails when it gets worse. An entry that is better than measured (improved,
now within the limit, removed or renamed) fails too, naming the edit, so the
baseline can only shrink.

Definitions
-----------
function
    Every ``def`` and ``async def`` at any depth: module, class body, nested,
    under ``if``/``try``. Lambdas are not functions. Named like
    ``__qualname__``: ``Class.method``, ``outer.<locals>.inner``. A qualified
    name defined more than once in one file (``if``/``else`` branches,
    ``@overload`` stubs, a property's getter and setter) is numbered in source
    order: ``f``, ``f#2``, ``f#3``.
function-lines
    Lines carrying code from the first statement after the docstring to the
    function's last line. Not counted: decorators, the signature, the
    docstring, blank lines, comment-only lines. Counted: every physical line a
    wrapped statement spans, and nested functions and classes, which are part
    of the enclosing body. Why: CLAUDE.md's "max 20 lines" is about how much
    logic one unit holds, and the same file asks for comments that explain
    WHY; a count that charged for comments or blank lines would reward
    deleting both, and a signature ruff format wraps is not logic. Nested
    definitions count so a grandfathered function cannot keep growing by
    gaining closures.
function-params
    Positional-only, positional and keyword-only parameters, plus one each for
    ``*args`` and ``**kwargs``. The receiver (``self``, ``cls``) of a function
    defined directly in a class body is not counted, unless the function is a
    ``@staticmethod``.
function-complexity
    Ruff's mccabe value (C901), not a reimplementation. Ruff runs with
    ``--isolated`` and ``--ignore-noqa``, so no configuration file, per-file
    ignore or ``# noqa`` can waive it.
file-lines
    Physical lines, as ``wc -l`` counts them, plus an unterminated last line.
    The 700 limit was set against that count.

Exit status: 0 clean, 1 a rule or baseline violation, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import io
import json
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from collections.abc import Container, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path("scripts") / "quality" / "ratchet-baseline.toml"
RULES_DOC = "scripts/quality/check_ratchets.py"
FUNCTION_LINES = "function-lines"
FUNCTION_PARAMS = "function-params"
FUNCTION_COMPLEXITY = "function-complexity"
FILE_LINES = "file-lines"
LIMITS = {FUNCTION_LINES: 20, FUNCTION_PARAMS: 4, FUNCTION_COMPLEXITY: 10, FILE_LINES: 700}
FIELDS = frozenset({"rule", "path", "name", "value", "reason"})
NON_CODE = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
RUFF_MESSAGE = re.compile(r"`(?P<name>[^`]+)` is too complex \((?P<value>\d+) > 0\)")

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class RatchetError(Exception):
    """The check could not run. Never reported as a pass."""


@dataclass(frozen=True, order=True)
class Key:
    path: str
    name: str
    rule: str


@dataclass(frozen=True)
class Measure:
    key: Key
    value: int
    line: int


@dataclass(frozen=True)
class Entry:
    key: Key
    value: int
    reason: str


@dataclass(frozen=True, order=True)
class Failure:
    group: int
    path: str
    line: int
    name: str
    rule: str
    text: str


@dataclass(frozen=True)
class Function:
    node: FunctionNode
    qualname: str
    in_class: bool


@dataclass(frozen=True)
class Source:
    path: str
    file: Path
    text: str
    tree: ast.Module


@dataclass(frozen=True)
class Report:
    failures: list[Failure]
    summary: str


DefTable = dict[tuple[Path, int, str], tuple[str, Function]]


def load_sources(root: Path) -> tuple[list[Source], list[Failure]]:
    scope = root / "src"
    if not scope.is_dir():
        raise RatchetError(f"no source directory at {scope}")
    sources: list[Source] = []
    failures: list[Failure] = []
    for file in sorted(scope.rglob("*.py")):
        path = file.relative_to(root).as_posix()
        try:
            text = file.read_text(encoding="utf-8")
            sources.append(Source(path, file, text, ast.parse(text, filename=path)))
        except (SyntaxError, ValueError) as error:
            failures.append(_parse_failure(path, error))
    return sources, failures


def _parse_failure(path: str, error: SyntaxError | ValueError) -> Failure:
    if isinstance(error, SyntaxError):
        line, message = error.lineno or 1, error.msg
    else:
        line, message = 1, str(error)
    return Failure(1, path, line, "", "parse", f"FAIL ratchet parse: {path}:{line} {message}")


def functions(tree: ast.Module) -> list[Function]:
    found: list[Function] = []
    _collect(tree, "", False, found)
    seen: Counter[str] = Counter()
    numbered: list[Function] = []
    for function in found:
        seen[function.qualname] += 1
        suffix = f"#{seen[function.qualname]}" if seen[function.qualname] > 1 else ""
        numbered.append(replace(function, qualname=function.qualname + suffix))
    return numbered


def _collect(node: ast.AST, prefix: str, in_class: bool, found: list[Function]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append(Function(child, prefix + child.name, in_class))
            _collect(child, f"{prefix}{child.name}.<locals>.", False, found)
        elif isinstance(child, ast.ClassDef):
            _collect(child, f"{prefix}{child.name}.", True, found)
        else:
            _collect(child, prefix, in_class, found)


def code_lines(text: str) -> list[int]:
    lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type not in NON_CODE:
            lines.update(range(token.start[0], token.end[0] + 1))
    return sorted(lines)


def physical_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def body_lines(node: FunctionNode, code: list[int]) -> int:
    body = node.body[1:] if _is_docstring(node.body[0]) else node.body
    if not body:
        return 0
    end = node.end_lineno or body[-1].lineno
    return bisect.bisect_right(code, end) - bisect.bisect_left(code, body[0].lineno)


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def param_count(function: Function) -> int:
    args = function.node.args
    positional = len(args.posonlyargs) + len(args.args)
    variadic = (args.vararg is not None) + (args.kwarg is not None)
    receiver = function.in_class and positional > 0 and not _is_static(function.node)
    return positional + len(args.kwonlyargs) + variadic - receiver


def _is_static(node: FunctionNode) -> bool:
    return any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)


def _measure_source(source: Source, table: list[Function]) -> Iterator[Measure]:
    yield Measure(Key(source.path, "", FILE_LINES), physical_lines(source.text), 0)
    code = code_lines(source.text)
    for function in table:
        key, line = Key(source.path, function.qualname, FUNCTION_LINES), function.node.lineno
        yield Measure(key, body_lines(function.node, code), line)
        yield Measure(replace(key, rule=FUNCTION_PARAMS), param_count(function), line)


def measure_tree(root: Path, sources: list[Source]) -> list[Measure]:
    tables = {source.path: functions(source.tree) for source in sources}
    measures = [m for source in sources for m in _measure_source(source, tables[source.path])]
    return measures + _complexity(root, sources, tables)


def _complexity(
    root: Path, sources: list[Source], tables: dict[str, list[Function]]
) -> list[Measure]:
    by_def: DefTable = {
        (source.file.resolve(), function.node.lineno, function.node.name): (source.path, function)
        for source in sources
        for function in tables[source.path]
    }
    measures = [_complexity_measure(item, by_def) for item in run_ruff(root, sources)]
    if len(measures) != len(by_def):
        raise RatchetError(f"ruff measured {len(measures)} functions, the tree has {len(by_def)}")
    return measures


def run_ruff(root: Path, sources: list[Source]) -> list[object]:
    if not sources:
        return []
    command = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--ignore-noqa"]
    command += ["--select", "C901", "--config", "lint.mccabe.max-complexity=0"]
    command += ["--output-format", "json", *(str(source.file) for source in sources)]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode not in (0, 1):
        raise RatchetError(f"ruff exited {result.returncode}: {result.stderr.strip()}")
    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RatchetError(f"ruff output is not JSON: {result.stderr.strip()}") from error
    if not isinstance(items, list):
        raise RatchetError("ruff output is not a list of diagnostics")
    return items


def _complexity_measure(item: object, by_def: DefTable) -> Measure:
    if not isinstance(item, dict) or item.get("code") != "C901":
        raise RatchetError(f"unexpected ruff diagnostic: {item!r}")
    match = RUFF_MESSAGE.fullmatch(str(item.get("message")))
    location = item.get("location")
    row = location.get("row") if isinstance(location, dict) else None
    file = Path(str(item.get("filename"))).resolve()
    found = by_def.get((file, row, match["name"])) if match and isinstance(row, int) else None
    if match is None or found is None:
        raise RatchetError(f"ruff diagnostic matches no function: {item!r}")
    path, function = found
    key = Key(path, function.qualname, FUNCTION_COMPLEXITY)
    return Measure(key, int(match["value"]), function.node.lineno)


def load_baseline(path: Path) -> tuple[dict[Key, Entry], list[Failure]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise RatchetError(f"baseline {path} is unreadable: {error}") from error
    raw_entries = data.pop("entry", [])
    if data or not isinstance(raw_entries, list):
        raise RatchetError(f"baseline {path} may hold only [[entry]] tables")
    entries: dict[Key, Entry] = {}
    failures: list[Failure] = []
    for index, raw in enumerate(raw_entries, start=1):
        parsed = _entry(raw, entries)
        if isinstance(parsed, Entry):
            entries[parsed.key] = parsed
        else:
            text = f"FAIL ratchet baseline: entry {index}{parsed}"
            failures.append(Failure(0, "", index, "", "", text))
    return entries, failures


def _entry(raw: object, known: Container[Key]) -> Entry | str:
    if not isinstance(raw, dict):
        return ": not a table"
    key = _entry_key(raw)
    if isinstance(key, str):
        return f": {key}"
    entry = _entry_value(raw, key, known)
    if isinstance(entry, str):
        return f" ({describe(key)}): {entry}"
    return entry


def _entry_key(raw: dict[str, object]) -> Key | str:
    rule, path, name = raw.get("rule"), raw.get("path"), raw.get("name", "")
    if rule not in LIMITS:
        return f"unknown rule {rule!r}"
    if not isinstance(path, str) or not path:
        return "path must be a non-empty string"
    if rule == FILE_LINES and name != "":
        return f"{rule} takes no name"
    if rule != FILE_LINES and (not isinstance(name, str) or not name):
        return f"{rule} needs a name"
    return Key(path, str(name), str(rule))


def _entry_value(raw: dict[str, object], key: Key, known: Container[Key]) -> Entry | str:
    unknown = sorted(set(raw) - FIELDS)
    value, reason, limit = raw.get("value"), raw.get("reason"), LIMITS[key.rule]
    if unknown:
        return f"unknown field {', '.join(unknown)}"
    if not isinstance(value, int) or isinstance(value, bool):
        return "value must be an integer"
    if value <= limit:
        return f"value {value} does not exceed the limit {limit}"
    if not isinstance(reason, str) or not reason.strip():
        return "missing reason"
    if key in known:
        return "duplicate entry"
    return Entry(key, value, reason)


def describe(key: Key) -> str:
    return f"{key.rule} {key.path} {key.name}".rstrip()


def compare(measures: list[Measure], entries: dict[Key, Entry]) -> list[Failure]:
    measured = {measure.key for measure in measures}
    failures = [_missing(entry) for key, entry in entries.items() if key not in measured]
    for measure in measures:
        verdict = _verdict(measure, entries.get(measure.key))
        if verdict:
            failures.append(_failure(measure, verdict))
    return failures


def _verdict(measure: Measure, entry: Entry | None) -> str:
    limit = LIMITS[measure.key.rule]
    if entry is None:
        return f"> {limit}" if measure.value > limit else ""
    if measure.value > entry.value:
        return f"> baseline {entry.value}"
    if measure.value <= limit:
        return f"<= {limit}: within the limit, remove the baseline entry (--tighten)"
    if measure.value < entry.value:
        return (
            f"< baseline {entry.value}: tighten the baseline entry to {measure.value} (--tighten)"
        )
    return ""


def _failure(measure: Measure, verdict: str) -> Failure:
    key = measure.key
    where = f"{key.path}:{measure.line} {key.name}" if key.name else key.path
    text = f"FAIL ratchet {key.rule}: {where} {measure.value} {verdict}"
    return Failure(2, key.path, measure.line, key.name, key.rule, text)


def _missing(entry: Entry) -> Failure:
    key = entry.key
    where = f"{key.path} {key.name}".rstrip()
    text = f"FAIL ratchet {key.rule}: {where} not found (removed or renamed): "
    text += "remove the baseline entry (--tighten)"
    return Failure(2, key.path, 0, key.name, key.rule, text)


def check(root: Path, baseline: Path) -> Report:
    sources, parse_failures = load_sources(root)
    measures = measure_tree(root, sources)
    entries, problems = load_baseline(baseline)
    unparsed = {failure.path for failure in parse_failures}
    checked = {key: entry for key, entry in entries.items() if key.path not in unparsed}
    failures = sorted([*problems, *parse_failures, *compare(measures, checked)])
    return Report(failures, _summary(failures, measures, len(sources), len(entries)))


def _summary(failures: list[Failure], measures: list[Measure], files: int, entries: int) -> str:
    if failures:
        return f"ratchet: {len(failures)} failure(s); rules and definitions: {RULES_DOC}"
    count = sum(1 for measure in measures if measure.key.rule == FUNCTION_LINES)
    return f"ratchet: OK, {count} functions in {files} files, {entries} baseline entries"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Size and complexity ratchets (issue #323).")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root; scope is <root>/src"
    )
    parser.add_argument("--baseline", type=Path, help=f"default: <root>/{BASELINE.as_posix()}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        report = check(root, args.baseline or root / BASELINE)
    except RatchetError as error:
        print(f"ERROR ratchet: {error}")
        return 2
    for failure in report.failures:
        print(failure.text)
    print(report.summary)
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
