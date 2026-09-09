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

import importlib.metadata
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.types import JSONRPCMessage
from pydantic import ValidationError

from lovspor.release.envelope import CorpusSummary, Marker, ReleaseRecord
from lovspor.site.capabilities import CapabilityDocument, Checkout, Observation, derive_state
from lovspor.site.fingerprint import ReleaseKey
from tests.unit.site_fixtures import (
    available_observation,
    capability_document,
    checkout_expectations,
    throwaway_checkout,
    unobserved_transport,
)

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
        # rpartition, not partition: a class-scoped node id is
        # `file::Class::test_name`, and splitting on the FIRST separator left
        # `name` as "Class::test_name", which no `def` line can ever contain.
        # A valid id then read as broken — the failure mode this check exists
        # to prevent, pointed the wrong way.
        head, _, name = str(node_id).rpartition("::")
        path = head.partition("::")[0]
        target = root / path
        if not target.exists() or f"def {name}(" not in target.read_text(encoding="utf-8"):
            broken.append(str(node_id))
    return broken


def test_httpx_still_normalises_the_request_method() -> None:
    """Pins the argument that waives the ``"GET"`` -> ``"get"`` mutants in
    ``observatory/fetch.py`` and ``site/probe.py`` and the ``"POST"`` ->
    ``"post"`` one in the probe. The day httpx stops upper-casing the method,
    those mutants become real defects — a request going out as ``get`` — and
    this test is what says so."""
    url = "https://www.baerum.kommune.no/"

    assert httpx.Request("get", url).method == "GET"
    assert httpx.Request("gEt", url).method == "GET"
    assert httpx.Request("GET", url).method == "GET"
    assert httpx.Request("post", url).method == "POST"


def test_assumption_httpx_header_lookup_is_case_insensitive() -> None:
    """Pins the argument that waives the ``get("content-type")`` ->
    ``get("CONTENT-TYPE")`` and ``get("www-authenticate")`` ->
    ``get("WWW-AUTHENTICATE")`` mutants in ``site/probe.py``: an httpx
    ``Headers`` lookup lower-cases the requested name, so every spelling reads
    the one stored value, on a response's headers as on any other."""
    response = httpx.Response(
        401,
        headers={"Content-Type": "text/event-stream; charset=utf-8", "WWW-Authenticate": "Bearer"},
    )

    for name in ("content-type", "CONTENT-TYPE", "Content-Type"):
        assert response.headers.get(name) == "text/event-stream; charset=utf-8", name
    for name in ("www-authenticate", "WWW-AUTHENTICATE", "WWW-Authenticate"):
        assert response.headers.get(name) == "Bearer", name
    assert response.headers.get("X-ABSENT", "") == ""


def test_assumption_the_json_rpc_parser_ignores_a_space_at_a_line_margin() -> None:
    """Pins the argument that waives the ``removeprefix(" ")`` ->
    ``removesuffix(" ")`` mutant in ``site/probe.py::_sse_data``: the two rules
    differ by one U+0020 at one margin of a ``data:`` line's value, and the
    JSON-RPC parser — pydantic's, under ``JSONRPCMessage`` — treats a space
    before or after a token as insignificant, on one line and across the LF
    that joins two. A non-breaking space is not whitespace to it, which is why
    the earlier ``lstrip()`` had no such twin."""
    answer = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}'
    reference = JSONRPCMessage.model_validate_json(answer).root

    for padded in (f" {answer}", f"{answer} ", f" {answer} "):
        assert JSONRPCMessage.model_validate_json(padded).root == reference, padded
    head, tail = answer.split(", ", 1)
    for joined in (f"{head},\n {tail}", f"{head}, \n{tail}", f"{head},\n{tail}"):
        assert JSONRPCMessage.model_validate_json(joined).root == reference, joined
    with pytest.raises(ValidationError):
        JSONRPCMessage.model_validate_json(f"\u00a0{answer}")


def _release_record() -> ReleaseRecord:
    return ReleaseRecord(
        schema_version="1",
        release_content_id="a" * 64,
        release_key=ReleaseKey(
            corpus_commit="c" * 40,
            lovspor_commit="d" * 40,
            state_sha256="e" * 64,
            toolchain_fingerprint="f" * 64,
        ),
        site_manifest_sha256="1" * 64,
        capability_sha256="2" * 64,
        observed_at="2026-01-01T00:00:00Z",
        observer="release-probe",
        corpus=CorpusSummary(
            corpus_commit="c" * 40,
            corpus_commit_time="2026-01-01T00:00:00+00:00",
            engine_version="0.0.0",
            documents=1,
        ),
    )


def test_assumption_a_python_mode_dump_serialises_like_json_mode() -> None:
    """Pins the argument that waives the ``mode="json"`` -> ``None`` / other
    string mutants in ``site/capabilities.py::state_sha256``,
    ``site/fixture.py::document_bytes`` and ``release/envelope.py::record_bytes``:
    pydantic takes the JSON serialiser only for the exact string "json" and the
    Python one otherwise, and these models hold nothing the Python path renders
    differently under ``json.dumps`` — the one non-JSON-native type is
    ``tuple[CredentialMode, ...]``, which dumps as a list either way. A field
    type that the Python path leaves as an object (a datetime, an enum, bytes)
    would make this test fail, and the mutants real."""
    checkout = Checkout.model_validate(checkout_expectations())
    models: list[Any] = [
        derive_state(Observation.model_validate(available_observation()), checkout),
        derive_state(Observation.model_validate(unobserved_transport("network")), checkout),
        CapabilityDocument.model_validate(capability_document()),
        _release_record(),
        Marker(active="b" * 64, previous="a" * 64),
        Marker(active="a" * 64, previous=None),
    ]

    for model in models:
        reference = json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        for mode in ("python", None, "JSON", "XXjsonXX"):
            dumped = json.dumps(model.model_dump(mode=mode), sort_keys=True, ensure_ascii=False)
            assert dumped == reference, mode


def test_assumption_importlib_metadata_lookup_is_case_insensitive() -> None:
    """Pins the argument that waives the ``version("jinja2")`` -> ``"JINJA2"``
    mutant in ``site/fingerprint.py``: ``importlib.metadata`` normalises the
    requested name (PEP 503: case-folded, runs of ``-_.`` as one ``-``) before
    it searches for a distribution, so every spelling finds the same one."""
    assert importlib.metadata.version("JINJA2") == importlib.metadata.version("jinja2")
    assert importlib.metadata.version("Jinja2") == importlib.metadata.version("jinja2")


def test_assumption_git_rev_parse_fails_on_an_unborn_head_without_verify(tmp_path: Path) -> None:
    """Pins the argument that waives the dropped ``--verify`` in
    ``site/build.py::require_clean_work_tree``: with one revision argument,
    ``git rev-parse`` exits non-zero when it does not resolve — ``--verify``
    or not (it also echoes the argument to stdout, which ``_git`` discards on
    failure) — and prints the same sha when it does."""
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    subprocess.run(["git", "-C", str(unborn), "init", "-q"], check=True)
    without = subprocess.run(
        ["git", "-C", str(unborn), "rev-parse", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert without.returncode != 0

    root, head = throwaway_checkout(tmp_path / "committed")
    resolved = [
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", *flags, "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for flags in ((), ("--verify",))
    ]
    assert resolved == [head, head]


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


def test_a_class_scoped_node_id_resolves(tmp_path: Path) -> None:
    """`file::Class::test_name` is an ordinary pytest id and must not read as
    broken merely for having two separators."""
    module = tmp_path / "tests" / "unit" / "test_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("class TestThing:\n    def test_holds(self) -> None:\n        pass\n")
    entry = [{"assumption_test": "tests/unit/test_x.py::TestThing::test_holds"}]

    assert _broken_assumption_tests(entry, tmp_path) == []


def test_a_node_id_naming_a_missing_test_is_still_broken(tmp_path: Path) -> None:
    """The widened split must not widen what passes: a name that is not there
    still fails, class-scoped or not."""
    module = tmp_path / "tests" / "unit" / "test_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("class TestThing:\n    def test_holds(self) -> None:\n        pass\n")
    entry = [{"assumption_test": "tests/unit/test_x.py::TestThing::test_gone"}]

    assert _broken_assumption_tests(entry, tmp_path) == [
        "tests/unit/test_x.py::TestThing::test_gone"
    ]


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
