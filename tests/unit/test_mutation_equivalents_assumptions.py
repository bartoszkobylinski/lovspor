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
    dependencies = _dependency_names()

    for entry in _entries():
        named = sorted(
            dependency
            for dependency in dependencies
            if re.search(rf"\b{re.escape(dependency)}\b", entry["justification"], re.IGNORECASE)
        )
        assert not named or entry.get("assumption_test"), (
            f"{entry['file']} {entry['symbol']}: the justification argues from "
            f"{', '.join(named)} but names no assumption_test"
        )


def test_every_declared_assumption_test_exists() -> None:
    """A node id that no longer resolves is worse than no field at all: it reads
    as covered."""
    for entry in _entries():
        node_id = entry.get("assumption_test")
        if not node_id:
            continue
        path, _, name = str(node_id).partition("::")
        target = _ROOT / path

        assert target.exists(), f"{entry['file']}: assumption test file {path} is missing"
        assert f"def {name}(" in target.read_text(encoding="utf-8"), (
            f"{entry['file']}: {path} has no test named {name}"
        )
