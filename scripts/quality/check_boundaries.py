#!/usr/bin/env python3
"""Architecture boundaries over production code (issue #323, Phases C/D3).

Each rule names a seam an accepted ADR or an established module already
draws, and fails when code outside that seam reaches across it. There is one
rule so far:

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

Exit status: 0 clean, 1 a boundary violation, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

REGISTRY_MODULE = "src/lovspor/temporal_attestation.py"


@dataclass(frozen=True)
class Violation:
    path: str
    line: int

    @property
    def text(self) -> str:
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


def violations_in(path: str, source: str) -> list[Violation]:
    if path == REGISTRY_MODULE:
        return []
    tree = ast.parse(source, filename=path)
    return [Violation(path, node.lineno) for node in ast.walk(tree) if runs_git_notes(node)]


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
    print(f"boundaries: {len(found)} violation(s), 1 rule")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
