#!/usr/bin/env python3
"""Recover what each surviving mutant actually changed (issue #119).

`mutmut results` names a survivor after the rewritten function variant —
`<module>.x_<symbol>__mutmut_<n>` — and nothing else. Mutmut 3 mutates a copy of
the function rather than a source position, so the result artifact used to carry
`file`, `line`, `symbol` and `operator` as bare nulls: whoever picked up a red
gate had to re-run mutmut locally just to read the mutation. That local run is
the expensive step the artifact exists to avoid.

This reads the shadow tree the run already built (`mutants/`) and writes one JSON
object per survivor with the source file, the symbol, the line the symbol is
defined on, and the unified diff of the mutation. Without the shadow tree — a
downloaded artifact, a fresh checkout, a re-run — it degrades to what the id
alone proves and says so in `detail_source`, rather than emitting nulls that
read like missing data.

`line` stays null by construction: a mutmut 3 mutant has no source position.
`symbol_line` locates the function; `diff` shows the change inside it. Mutant
ids renumber whenever the file changes upstream of the mutant, so the diff — not
the id — is what a cross-round comparison must quote.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

# Import failure is a legal state, not a crash: the report is still worth
# writing from the mutant ids alone, and this step must never fail the job.
try:
    from mutmut.__main__ import (
        Config,
        SourceFileMutationData,
        get_diff_for_mutant,
        walk_mutatable_files,
    )
except ImportError as exc:
    IMPORT_ERROR: str | None = f"mutmut is not importable ({exc})"
else:
    IMPORT_ERROR = None

MUTANT_MARKER = "__mutmut_"
# Mutmut mangles a method key as `xǁClassǁmethod`; a plain function is `x_name`.
CLASS_SEPARATOR = "ǁ"
SOURCE_ROOT = Path("src")
# Best-effort by design: mutmut's loaders raise these when the meta files are
# absent, truncated, or were written by another version of the tool. None of
# that is worth failing a step whose whole job is to enrich a report.
_LOAD_ERRORS = (OSError, ValueError, KeyError, AssertionError)


def split_id(mutant_id: str) -> tuple[str, str, str | None]:
    """(dotted module, symbol, class name or None) — string work, no shadow tree."""
    mangled = mutant_id.partition(MUTANT_MARKER)[0]
    module, _, tail = mangled.rpartition(".")
    if CLASS_SEPARATOR in tail:
        parts = tail.split(CLASS_SEPARATOR)
        return module, parts[-1], CLASS_SEPARATOR.join(parts[1:-1]) or None
    return module, tail[2:] if tail.startswith("x_") else tail, None


def module_file(module: str, root: Path = SOURCE_ROOT) -> Path | None:
    """The source file a dotted module name points at, if it is on disk."""
    if not module:
        return None
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _find_def(scope: ast.AST, name: str) -> ast.AST | None:
    definition = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(scope):
        if isinstance(node, definition) and node.name == name:
            return node
    return None


def symbol_line(path: Path | None, symbol: str, class_name: str | None) -> int | None:
    """Line the symbol is defined on, so the artifact points at a real place."""
    if path is None:
        return None
    try:
        scope: ast.AST | None = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    if class_name and scope is not None:
        scope = _find_def(scope, class_name.split(CLASS_SEPARATOR)[-1])
    node = _find_def(scope, symbol) if scope is not None else None
    return getattr(node, "lineno", None)


def shadow_index() -> tuple[dict[str, Path], str | None]:
    """Map every mutant id in `mutants/` to its source file, or say why not.

    Mutmut's own `find_mutant` re-walks and re-loads every mutated file per
    lookup; on the 45-survivor run that motivated this, that is 45 full walks.
    """
    if IMPORT_ERROR is not None:
        return {}, IMPORT_ERROR
    index: dict[str, Path] = {}
    try:
        Config.ensure_loaded()
        for path in walk_mutatable_files():
            data = SourceFileMutationData(path=path)
            data.load()
            index.update(dict.fromkeys(data.exit_code_by_key, Path(path)))
    except _LOAD_ERRORS as e:
        return {}, f"the shadow tree is unreadable ({e})"
    if not index:
        return {}, "no mutants/*.meta — the shadow tree was not kept"
    return index, None


def mutation_diff(mutant_id: str, path: Path) -> str | None:
    """Unified diff of original vs mutant function, read from the shadow tree."""
    try:
        return get_diff_for_mutant(mutant_id, path=path).strip() or None
    except _LOAD_ERRORS:
        return None


def describe(mutant_id: str, index: dict[str, Path], reason: str | None) -> dict[str, object]:
    """One survivor record: everything recoverable, and how it was recovered."""
    module, symbol, class_name = split_id(mutant_id)
    mutated = index.get(mutant_id)
    diff = mutation_diff(mutant_id, mutated) if mutated is not None else None
    path = mutated if mutated is not None else module_file(module)
    missing = reason or "this mutant is not in the shadow tree"
    return {
        "id": mutant_id,
        "file": str(path) if path is not None else None,
        "line": None,
        "symbol": symbol,
        "class": class_name,
        "symbol_line": symbol_line(path, symbol, class_name),
        "operator": None,
        "diff": diff,
        "detail_source": "mutants_shadow_tree" if diff else f"id_only: {missing}",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--survivors-file", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    ids = args.survivors_file.read_text().split() if args.survivors_file.exists() else []
    index, reason = shadow_index()
    entries = [describe(mutant_id, index, reason) for mutant_id in ids]
    args.out.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8"
    )
    with_diff = sum(1 for e in entries if e["diff"])
    print(f"wrote {args.out}: {len(entries)} survivor(s), {with_diff} with a mutation diff")
    if entries and not with_diff:
        print(f"no mutation diff recovered: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
