"""The assumptions mutation-equivalents.toml entries stand on (issue #132).

A registered equivalent mutant waives a required check for good. Four of the
register's entries argue from Python's own semantics — a falsy ``None``, the
character-set argument of ``str.rstrip`` — and those cannot rot. The fifth
argues from httpx normalising the request method, and ``pyproject.toml`` pins
httpx by a floor, not an exact version: a bump could make that justification
false while the gate kept waiving the survivor, with nothing re-reading the
argument.

So an entry that argues from a dependency names the test that pins its
assumption, and the tests live here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_REGISTER = _ROOT / "mutation-equivalents.toml"
_PYPROJECT = _ROOT / "pyproject.toml"


def _entries() -> list[dict[str, Any]]:
    data = tomllib.loads(_REGISTER.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data.get("equivalent", [])
    return entries


def _dependency_names() -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    requirements: list[str] = data["project"]["dependencies"]
    return {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip() for requirement in requirements
    }


def _entries_missing_assumption_test(
    entries: list[dict[str, Any]], dependencies: set[str]
) -> list[str]:
    """Same rule `test_an_entry_arguing_from_a_dependency_names_the_test_that_pins_it`
    enforces, factored out so it can be exercised against synthetic violations
    too — a check that has only ever run against an already-clean register
    could be silently broken and nobody would see it fail."""
    violations = []
    for entry in entries:
        named = [
            dependency
            for dependency in dependencies
            if re.search(rf"\b{re.escape(dependency)}\b", entry["justification"], re.IGNORECASE)
        ]
        if named and not entry.get("assumption_test"):
            violations.append(entry.get("symbol", "<no symbol>"))
    return violations


def _broken_assumption_tests(entries: list[dict[str, Any]], root: Path) -> list[str]:
    """Same rule `test_every_declared_assumption_test_exists` enforces, factored
    out for the same reason: a node id that no longer resolves must fail this
    check, and that path needs its own proof, not just the clean-register one."""
    broken = []
    for entry in entries:
        node_id = entry.get("assumption_test")
        if not node_id:
            continue
        path, _, name = str(node_id).partition("::")
        target = root / path
        if not target.exists() or f"def {name}(" not in target.read_text(encoding="utf-8"):
            broken.append(str(node_id))
    return broken


def test_httpx_still_normalises_the_request_method() -> None:
    """Pins the argument that waives the ``"GET"`` -> ``"get"`` mutant in
    ``observatory/fetch.py``. The day httpx stops upper-casing the method, that
    mutant becomes a real defect — a request going out as ``get`` — and this
    test is what says so."""
    url = "https://www.baerum.kommune.no/"

    assert httpx.Request("get", url).method == "GET"
    assert httpx.Request("gEt", url).method == "GET"
    assert httpx.Request("GET", url).method == "GET"


def test_an_entry_arguing_from_a_dependency_names_the_test_that_pins_it() -> None:
    """A justification resting on a package that `pyproject.toml` can bump is
    an assumption with an expiry date nobody is watching."""
    violations = _entries_missing_assumption_test(_entries(), _dependency_names())

    assert violations == []


def test_every_declared_assumption_test_exists() -> None:
    """A node id that no longer resolves is worse than no field at all: it reads
    as covered."""
    broken = _broken_assumption_tests(_entries(), _ROOT)

    assert broken == []


def test_an_entry_naming_a_dependency_without_an_assumption_test_is_caught() -> None:
    """Proves the checker actually fires: run against the real register it can
    only ever see the already-clean state, which would pass even if the rule
    were a no-op."""
    entries = [
        {
            "file": "src/lovspor/observatory/fetch.py",
            "symbol": "_request",
            "justification": "httpx normalises the method before the request exists.",
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == ["_request"]


def test_an_entry_with_no_dependency_in_its_justification_is_not_flagged() -> None:
    entries = [
        {
            "file": "src/lovspor/observatory/model.py",
            "symbol": "record_to_json_line",
            "justification": "json.dumps tests ensure_ascii for truth, and None is falsy.",
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == []


def test_an_entry_naming_a_dependency_with_an_assumption_test_is_not_flagged() -> None:
    entries = [
        {
            "file": "src/lovspor/observatory/fetch.py",
            "symbol": "_request",
            "justification": "httpx normalises the method before the request exists.",
            "assumption_test": (
                "tests/unit/test_mutation_equivalents_assumptions.py"
                "::test_httpx_still_normalises_the_request_method"
            ),
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == []


def test_an_assumption_test_pointing_at_a_missing_file_is_caught(tmp_path: Path) -> None:
    entries = [{"symbol": "_request", "assumption_test": "tests/unit/does_not_exist.py::test_x"}]

    broken = _broken_assumption_tests(entries, tmp_path)

    assert broken == ["tests/unit/does_not_exist.py::test_x"]


def test_an_assumption_test_naming_a_missing_function_is_caught(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "unit" / "test_pinned.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_other():\n    assert True\n")
    entries = [{"symbol": "_request", "assumption_test": "tests/unit/test_pinned.py::test_missing"}]

    broken = _broken_assumption_tests(entries, tmp_path)

    assert broken == ["tests/unit/test_pinned.py::test_missing"]


def test_an_entry_with_no_assumption_test_field_is_not_a_broken_one(tmp_path: Path) -> None:
    """Absence is legal for entries that never named a dependency; only a
    present-but-dangling node id is a defect."""
    entries = [{"symbol": "record_to_json_line"}]

    assert _broken_assumption_tests(entries, tmp_path) == []
