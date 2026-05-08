from pathlib import Path
from typing import Any, cast

import yaml
from evals.runner import FIXTURE_PATH, SCENARIOS_DIR

NEIGHBOR_DOC_ID = "lov-1961-06-16-015"
NEIGHBOR_PRODUCTION_SLUG = "grannelova-gl"
NABOLOVEN_SCENARIO_IDS = {
    "kari_001",
    "kari_002",
    "kari_003",
    "kari_005",
    "lars_001",
    "lars_002",
    "lars_003",
    "lars_007",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return cast(dict[str, Any], yaml.safe_load(file))


def _load_fixture_documents() -> list[dict[str, Any]]:
    fixture = _load_yaml(FIXTURE_PATH)
    return cast(list[dict[str, Any]], fixture["documents"])


def _load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        data = _load_yaml(path)
        scenarios.extend(cast(list[dict[str, Any]], data["scenarios"]))
    return scenarios


def _collect_mapping_values(node: Any, keys: set[str]) -> list[str]:
    if isinstance(node, dict):
        values = [str(value) for key, value in node.items() if key in keys]
        for value in node.values():
            values.extend(_collect_mapping_values(value, keys))
        return values
    if isinstance(node, list):
        values: list[str] = []
        for item in node:
            values.extend(_collect_mapping_values(item, keys))
        return values
    return []


def test_synthetic_neighbor_law_uses_production_slug() -> None:
    documents = {document["doc_id"]: document for document in _load_fixture_documents()}
    neighbor_law = documents[NEIGHBOR_DOC_ID]

    assert neighbor_law["slug"] == NEIGHBOR_PRODUCTION_SLUG
    assert neighbor_law["title"] == "Lov om rettshøve mellom grannar (grannelova)"
    assert "naboloven" not in neighbor_law["title"].lower()
    assert [event["subject"] for event in neighbor_law["history"]] == [
        "update(lov): grannelova-gl",
        "add(lov): grannelova-gl",
    ]


def test_scenario_slug_references_exist_in_synthetic_fixture() -> None:
    slugs = {document["slug"] for document in _load_fixture_documents()}
    missing: list[tuple[str, str]] = []
    for scenario in _load_scenarios():
        values = _collect_mapping_values(
            [scenario.get("expected_tool_calls", []), scenario.get("success_criteria", [])],
            {"slug", "slug_match"},
        )
        missing.extend((cast(str, scenario["id"]), value) for value in values if value not in slugs)

    assert missing == []


def test_naboloven_scenarios_use_production_canonical_slug() -> None:
    scenarios_by_id = {scenario["id"]: scenario for scenario in _load_scenarios()}

    for scenario_id in NABOLOVEN_SCENARIO_IDS:
        scenario = scenarios_by_id[scenario_id]
        values = _collect_mapping_values(
            [scenario.get("expected_tool_calls", []), scenario.get("success_criteria", [])],
            {"slug", "slug_match", "citation", "target"},
        )
        canonical_references = "\n".join(values)

        assert NEIGHBOR_PRODUCTION_SLUG in canonical_references
        assert "naboloven" not in canonical_references
