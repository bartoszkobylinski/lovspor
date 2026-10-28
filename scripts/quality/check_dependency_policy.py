#!/usr/bin/env python
"""Dependency and tool policy in the fast harness (issue #465, #323 Phase F).

Two concrete policies, each with a repository reason, each a rule the tree
already draws (no new contract; owner decision 2026-09-15 on #323):

``undeclared-import``
    Every third-party package ``src/lovspor/`` imports is declared in
    ``pyproject.toml`` under ``[project] dependencies`` or an
    ``[project.optional-dependencies]`` extra. ``pyproject.toml`` already
    says why: pyyaml sits in the dev group *because* nothing under
    ``src/lovspor`` imports it, anyio and jsonschema are declared *because*
    the engine imports them directly rather than leaning on a transitive
    pin. A transitive dependency vanishes with its parent's next release, and
    a dev-only one is absent from every ``pip install lovspor``. Import names
    map to distributions through the environment's own metadata
    (``packages_distributions``); a name no installed distribution provides
    is reported as such.

``committable-artefact``
    No tracked or staged path is a generated or backup artefact: mutmut's
    ``mutants/`` and ``*-mutants/`` trees and ``.mutmut-cache``, editor and
    patch leftovers (``*.bak``, ``*.orig``, ``*.rej``), ``__pycache__`` and
    ``*.pyc``. ``.gitignore`` already names ``mutants/``; this is the check
    that a path that slipped past it — staged with ``-f``, or under a name
    the ignore does not cover — is refused before it is committed.

Read-only. Exit 1 on any violation; every line names the rule and the path,
so an agent can repair it. Run by ``scripts/quality/verify-fast.sh``, by
fast-ci, and by ``tests/unit/test_quality_dependency_policy.py``.

Usage::

    uv run python scripts/quality/check_dependency_policy.py [--root PATH]
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import packages_distributions
from pathlib import Path

SOURCE = Path("src") / "lovspor"
LOCAL_PACKAGE = "lovspor"
RULES = 2
_GIT = shutil.which("git") or "git"
_GIT_TIMEOUT = 60
#: Generated or backup artefacts that never belong in a commit.
ARTEFACT_PATTERNS: tuple[str, ...] = (
    "mutants/*",
    "*/mutants/*",
    "*-mutants/*",
    "*/*-mutants/*",
    ".mutmut-cache",
    "*/.mutmut-cache",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.pyc",
    "__pycache__/*",
    "*/__pycache__/*",
)
_NORMALISE = re.compile(r"[-_.]+")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    detail: str

    def line(self) -> str:
        return f"FAIL {self.rule}: {self.path} -- {self.detail}"


def normalise(name: str) -> str:
    """PEP 503 normalisation, so ``PyYAML``, ``pyyaml`` and ``py_yaml`` compare equal."""
    return _NORMALISE.sub("-", name).lower()


def declared_distributions(pyproject: Path) -> frozenset[str]:
    """Every distribution ``[project]`` declares, dependencies and extras alike, normalised."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    requirements: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = (_REQUIREMENT_NAME.match(requirement) for requirement in requirements)
    return frozenset(normalise(match.group(1)) for match in names if match)


def top_level_imports(path: Path) -> set[str]:
    """The top-level package of every absolute import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.partition(".")[0])
    return found


def third_party_imports(root: Path) -> dict[str, list[str]]:
    """``{top-level package: [files importing it]}`` for everything not stdlib or local."""
    found: dict[str, list[str]] = {}
    for path in sorted((root / SOURCE).rglob("*.py")):
        for name in sorted(top_level_imports(path)):
            if name in sys.stdlib_module_names or name == LOCAL_PACKAGE:
                continue
            found.setdefault(name, []).append(path.relative_to(root).as_posix())
    return found


def undeclared_imports(root: Path) -> list[Violation]:
    """``undeclared-import`` violations: one per importing file and package."""
    declared = declared_distributions(root / "pyproject.toml")
    provided = {
        name: set(map(normalise, dists)) for name, dists in packages_distributions().items()
    }
    violations: list[Violation] = []
    for name, paths in third_party_imports(root).items():
        distributions = provided.get(name, set())
        if distributions & declared:
            continue
        if distributions:
            detail = f"imports {name!r}, provided by {', '.join(sorted(distributions))}"
        else:
            detail = f"imports {name!r}, which no installed distribution provides"
        detail += " and [project] in pyproject.toml does not declare"
        violations.extend(Violation("undeclared-import", path, detail) for path in paths)
    return violations


def _git_paths(root: Path, *args: str) -> list[str]:
    """Paths from a ``-z`` git listing, so a non-ASCII name arrives unquoted."""
    done = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [_GIT, "-C", str(root), *args, "-z"],
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    if done.returncode != 0:
        return []
    return [part.decode("utf-8", "surrogateescape") for part in done.stdout.split(b"\0") if part]


def artefact_paths(paths: Iterable[str]) -> list[Violation]:
    """``committable-artefact`` violations among ``paths``, in order, once each."""
    violations: list[Violation] = []
    for path in dict.fromkeys(paths):
        matched = next((p for p in ARTEFACT_PATTERNS if fnmatch.fnmatchcase(path, p)), None)
        if matched is not None:
            detail = f"a generated or backup artefact (matches {matched!r}); never committed"
            violations.append(Violation("committable-artefact", path, detail))
    return violations


def committable_artefacts(root: Path) -> list[Violation]:
    """Tracked and staged paths that are artefacts; an unreadable index is no verdict."""
    tracked = _git_paths(root, "ls-files", "--cached", "--full-name")
    staged = _git_paths(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return artefact_paths([*tracked, *staged])


def check(root: Path) -> list[Violation]:
    return [*undeclared_imports(root), *committable_artefacts(root)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="The repository root.")
    args = parser.parse_args(argv)
    found = check(args.root)
    for violation in found:
        print(violation.line())
    print(f"dependency-policy: {len(found)} violation(s), {RULES} rules")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
