#!/usr/bin/env python3
"""Architecture boundaries over production code (issue #323, Phases C/D3).

Each rule names a seam an accepted ADR or an established module already
draws, and fails when code outside that seam reaches across it. There is one
are two rules:

    attestation-registry
        Only ``lovspor/temporal_attestation.py`` runs ``git notes``. ADR-0012
        point 2c makes the attestation registry contracted state: entries are
        immutable and append-only, and a registry that cannot be read is a
        typed failure, never an ``unattested``. That module is where both
        promises are kept (``record_attestation`` refuses a rewrite,
        ``_read_entries`` proves the commit before it may answer "absent").
        A second caller of ``git notes`` would read or write the registry
        without either guarantee, and every test of the module would still
        pass.

The registry is reached through a ``git`` subprocess, not through an import,
so the rule reads argument vectors: a list or tuple display holding both the
literal ``"git"`` and the literal ``"notes"``, or starting with ``"notes"``
(the shape handed to a helper that prepends ``git``). Sets are not argument
vectors and are not read.

    local-dataset-unpublished
        Nothing under ``lovspor/publish/`` or ``lovspor/site/`` reaches the
        local dataset. ADR-0016 publishes ``lokale-forskrifter/`` through
        ``lovverk`` and MCP only, under åndsverkloven § 14; its publication on
        the static site is guarded off (Out of Scope, slice S11). The site
        enumerates documents from the root ``manifest.json`` alone, so a
        publisher that read the local manifest, or imported the modules that
        read it (``local_corpus``, ``mcp_local``, ``observation_history``,
        ``promotion``), would put observed-not-asserted local text on
        lovspor.no under the corpus pages' NLOD banner, and every emitter test
        built on a central-only corpus would still pass.

The second rule reads imports (absolute and relative) and string literals:
the dataset name ``lokale-forskrifter`` in any literal but a docstring, and a
local module's dotted name in any literal (an ``importlib`` route). Like the
first rule it catches the literal shape, not a name assembled at runtime;
``tests/unit/test_publish_local_guard.py`` is the behavioural half that
does not depend on the shape.

Exit status: 0 clean, 1 a boundary violation, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

REGISTRY_MODULE = "src/lovspor/temporal_attestation.py"
PUBLISHER_PACKAGES = ("src/lovspor/publish/", "src/lovspor/site/")
LOCAL_DATASET = "lokale-forskrifter"
LOCAL_MODULES = (
    "lovspor.local_corpus",
    "lovspor.mcp_local",
    "lovspor.observation_history",
    "lovspor.promotion",
)
RULES = 2


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str = "attestation-registry"

    @property
    def text(self) -> str:
        if self.rule == "local-dataset-unpublished":
            return (
                f"FAIL local-dataset-unpublished: {self.path}:{self.line} reaches the local "
                f"dataset ({LOCAL_DATASET}/ or a module that reads it) from the site "
                "publisher -- the site publishes the root manifest.json only (ADR-0016: local "
                "regulations reach lovverk and MCP under åndsverkloven § 14; static-site "
                "publication is guarded off, slice S11)"
            )
        return (
            f"FAIL attestation-registry: {self.path}:{self.line} runs `git notes` outside "
            f"{REGISTRY_MODULE} -- read or record attestations through that module "
            "(ADR-0012 point 2c: append-only entries; an unreadable registry is a typed "
            "failure, never `unattested`)"
        )


class BoundaryError(Exception):
    """The tree could not be read, so no verdict about it can be given."""


def _literals(node: ast.List | ast.Tuple) -> list[str | None]:
    return [
        e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
        for e in node.elts
    ]


def runs_git_notes(node: ast.AST) -> bool:
    """True for an argument vector that invokes ``git notes``."""
    if not isinstance(node, ast.List | ast.Tuple):
        return False
    literals = _literals(node)
    return ("git" in literals and "notes" in literals) or literals[:1] == ["notes"]


def _is_local_module(name: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in LOCAL_MODULES)


def _import_base(path: str, node: ast.ImportFrom) -> str:
    """The absolute module an ``ImportFrom`` names, relative levels resolved."""
    if node.level == 0:
        return node.module or ""
    package = path.removeprefix("src/").removesuffix(".py").split("/")[:-1]
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, *([node.module] if node.module else [])])


def imported_modules(path: str, node: ast.AST) -> list[str]:
    """The dotted name of everything an import statement binds.

    ``from a.b import c`` binds ``a.b.c`` whether ``c`` is a submodule or an
    attribute; either way ``a.b`` was loaded, so the bound names alone decide
    whether a local module was reached.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = _import_base(path, node)
    return [f"{base}.{alias.name}" for alias in node.names]


def reaches_local_dataset(path: str, node: ast.AST) -> bool:
    """True for an import of a local module or a literal naming the dataset or one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return LOCAL_DATASET in node.value or _is_local_module(node.value)
    return any(_is_local_module(name) for name in imported_modules(path, node))


_DOCUMENTED = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _docstrings(tree: ast.AST) -> set[int]:
    """The string constants Python itself treats as docstrings: a body's first statement."""
    return {
        id(first.value)
        for node in ast.walk(tree)
        if isinstance(node, _DOCUMENTED) and node.body
        for first in node.body[:1]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
    }


def _local_violations(path: str, tree: ast.AST) -> list[Violation]:
    if not path.startswith(PUBLISHER_PACKAGES):
        return []
    prose = _docstrings(tree)
    return [
        Violation(path, getattr(node, "lineno", 0), "local-dataset-unpublished")
        for node in ast.walk(tree)
        if id(node) not in prose and reaches_local_dataset(path, node)
    ]


def violations_in(path: str, source: str) -> list[Violation]:
    tree = ast.parse(source, filename=path)
    found = _local_violations(path, tree)
    if path != REGISTRY_MODULE:
        found += [Violation(path, node.lineno) for node in ast.walk(tree) if runs_git_notes(node)]
    return sorted(found, key=lambda violation: violation.line)


def check(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for file in sorted((root / "src").rglob("*.py")):
        path = file.relative_to(root).as_posix()
        try:
            found += violations_in(path, file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as error:
            raise BoundaryError(f"{path}: {error}") from error
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    root = parser.parse_args(argv).root.resolve()
    try:
        found = check(root)
    except BoundaryError as error:
        print(f"ERROR boundaries: {error}")
        return 2
    for violation in found:
        print(violation.text)
    print(f"boundaries: {len(found)} violation(s), {RULES} rules")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
