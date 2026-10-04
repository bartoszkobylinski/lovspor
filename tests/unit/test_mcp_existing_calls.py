"""Existing tool calls are byte-identical, with or without a local dataset (ADR-0016 S5).

ADR-0016 makes local regulations opt-in: with the new parameters omitted,
every existing call answers exactly as before, and an engine that predates
the local dataset serves a corpus that carries one without noticing it. The
committed snapshot is the wire output of these calls as the engine served
them before the local dataset existed (generated on ``origin/main``
``bbefbba``); a change to it is a change to the served surface, decided
explicitly, never a fixture to refresh.

The calls avoid the clock and git: no ``recorded_at``, no version tools, and
``corpus_status`` (which reports ages) is compared only between the two
corpora of one run.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.mcp import build_server
from tests.unit.local_dataset_fixtures import (
    LOCAL_SLUG,
    add_local_dataset,
    build_central,
    wire,
)

SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "mcp-existing-calls.json"

EXISTING_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_law", {"slug": "proveloven"}),
    ("get_law", {"slug": "finnesikke"}),
    ("get_law", {"slug": LOCAL_SLUG}),
    ("get_section", {"slug": "proveloven", "section_id": "2"}),
    ("get_section", {"slug": "ordensloven", "section_id": "§ 1."}),
    ("get_section", {"slug": "proveloven", "section_id": "99"}),
    ("get_section", {"slug": LOCAL_SLUG, "section_id": "1"}),
    ("list_sections", {"slug": "proveforskriften"}),
    ("get_law_history", {"slug": "ordensloven"}),
    ("list_recent_changes", {}),
    ("list_recent_changes", {"dataset": "forskrifter"}),
    ("search_laws", {"query": "lov"}),
    ("search_laws", {"query": "pr", "dataset": "lover"}),
    ("search_laws", {"query": "lekeplasser"}),
    ("search_laws", {"query": "lov", "dataset": "kommunale"}),
    ("search_body", {"query": "prøve"}),
    ("search_body", {"query": "lekeplass"}),
    ("validate_citation", {"citation": "proveloven § 2"}),
    ("verify_quote", {"slug": "ordensloven", "section_id": "2", "quote": "arkiveres i ti år"}),
    ("get_eu_basis", {"slug": "proveloven"}),
    ("search_eu_implementations", {"eu_doc_id": "32016R0679"}),
)


def outputs(root: Path) -> list[dict[str, Any]]:
    """The wire output of every existing call against the corpus at ``root``."""
    server = build_server(root)
    return [
        {"tool": name, "arguments": arguments, "wire": wire(server, name, arguments)}
        for name, arguments in EXISTING_CALLS
    ]


@pytest.fixture
def central(tmp_path: Path) -> Path:
    root = tmp_path / "central"
    build_central(root)
    return root


def test_existing_calls_match_the_committed_snapshot(central: Path) -> None:
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert outputs(central) == committed


def test_existing_calls_are_unchanged_by_a_local_dataset(central: Path) -> None:
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    status_before = wire(build_server(central), "corpus_status", {})

    add_local_dataset(central)

    assert outputs(central) == committed
    assert wire(build_server(central), "corpus_status", {}) == status_before


def test_the_snapshot_holds_answers_not_only_errors() -> None:
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    errors = [entry for entry in committed if "error" in entry["wire"]]

    assert len(committed) == len(EXISTING_CALLS)
    assert len(errors) == 5
