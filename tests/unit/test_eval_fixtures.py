import json
import tomllib
from pathlib import Path
from typing import Any, cast

import evals.runner as eval_runner
import numpy as np
import pytest
import yaml
from evals.runner import (
    FIXTURE_PATH,
    SCENARIOS_DIR,
    _build_synthetic_corpus,
    _call_reader,
    _load_embedder,
    _run_scenario,
    _tool_args,
    _write_embeddings,
)

from lovspor.embeddings.store import EMBEDDING_DIM, read_embeddings
from lovspor.mcp import CorpusReader

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


class _FakeEmbedder:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.requests.append(texts)
        matrix = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for index, _text in enumerate(texts):
            matrix[index, index] = 1.0
        return matrix

    def get_dimension(self) -> int:
        return EMBEDDING_DIM


class _FakeReader:
    def __init__(self) -> None:
        self.semantic_calls: list[tuple[str, str | None, int]] = []
        self.verify_quote_calls: list[tuple[str, str, str]] = []

    def semantic_search(
        self,
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.semantic_calls.append((query, dataset, limit))
        return [{"slug": "husleieloven", "section_id": "9-5"}]

    def verify_quote(self, slug: str, section_id: str, quote: str) -> dict[str, Any]:
        self.verify_quote_calls.append((slug, section_id, quote))
        return {"verified": True, "slug": slug, "section_id": section_id}

    def get_law(self, slug: str) -> dict[str, Any]:
        return {"slug": slug}

    def get_section(self, slug: str, section_id: str) -> dict[str, Any]:
        return {"slug": slug, "section_id": section_id}

    def get_law_history(self, slug: str) -> dict[str, Any]:
        return {"slug": slug, "events": []}

    def list_recent_changes(
        self,
        dataset: str | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return [{"dataset": dataset, "since": since, "limit": limit}]

    def search_laws(
        self,
        query: str,
        dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        return [{"query": query, "dataset": dataset}]

    def search_body(
        self,
        query: str,
        dataset: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return [{"query": query, "dataset": dataset, "limit": limit}]

    def validate_citation(self, citation: str) -> dict[str, Any]:
        return {"citation": citation, "valid": True}

    def get_eu_basis(self, slug: str) -> dict[str, Any]:
        return {"slug": slug, "eu_basis": []}

    def search_eu_implementations(self, eu_doc_id: str) -> list[dict[str, Any]]:
        return [{"eu_doc_id": eu_doc_id}]

    def corpus_status(self) -> dict[str, Any]:
        return {"status": "ok"}


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


def test_frida_phase3_persona_has_exact_anti_hallucination_scenarios() -> None:
    personas = eval_runner._load_personas()
    frida_scenarios = [
        scenario for scenario in _load_scenarios() if scenario.get("persona") == "frida"
    ]

    assert personas["frida"]["knowledge_level"] == "legal_adjacent"
    assert [scenario["id"] for scenario in frida_scenarios] == [
        "frida_001",
        "frida_002",
        "frida_003",
        "frida_004",
        "frida_005",
        "frida_006",
        "frida_007",
        "frida_008",
        "frida_009",
        "frida_010",
    ]
    assert {scenario.get("reveals_gap") for scenario in frida_scenarios} == {None}
    assert {
        scenario["id"]: [
            tool_call["tool"]
            for tool_call in cast(list[dict[str, Any]], scenario["expected_tool_calls"])
        ]
        for scenario in frida_scenarios
    } == {
        "frida_001": ["get_section", "verify_quote"],
        "frida_002": ["verify_quote"],
        "frida_003": ["get_section"],
        "frida_004": ["validate_citation"],
        "frida_005": ["verify_quote"],
        "frida_006": ["corpus_status"],
        "frida_007": ["search_eu_implementations", "get_eu_basis"],
        "frida_008": ["search_body"],
        "frida_009": ["get_section"],
        "frida_010": ["semantic_search", "get_section", "verify_quote"],
    }


def test_eval_suite_persona_count_includes_frida() -> None:
    personas = eval_runner._load_personas()
    scenarios = _load_scenarios()

    assert eval_runner.EXPECTED_PERSONAS == 9
    eval_runner._validate_suite(personas, scenarios, filtered_persona=None)

    personas_without_frida = dict(personas)
    personas_without_frida.pop("frida")
    with pytest.raises(ValueError) as exc_info:
        eval_runner._validate_suite(
            personas_without_frida,
            scenarios,
            filtered_persona=None,
        )
    assert str(exc_info.value) == "expected 9 personas, found 8"


def test_frida_phase3_non_semantic_scenarios_pass_against_synthetic_corpus(
    tmp_path: Path,
) -> None:
    fixture = _load_yaml(FIXTURE_PATH)
    _build_synthetic_corpus(tmp_path, fixture)
    reader = CorpusReader(tmp_path)
    frida_scenarios = [
        scenario for scenario in _load_scenarios() if scenario.get("persona") == "frida"
    ]

    results = {
        cast(str, scenario["id"]): _run_scenario(
            reader,
            scenario,
            embedder_available=False,
        )
        for scenario in frida_scenarios
    }

    assert {
        scenario_id: result.status
        for scenario_id, result in results.items()
        if scenario_id != "frida_010"
    } == {
        "frida_001": "pass",
        "frida_002": "pass",
        "frida_003": "pass",
        "frida_004": "pass",
        "frida_005": "pass",
        "frida_006": "pass",
        "frida_007": "pass",
        "frida_008": "pass",
        "frida_009": "pass",
    }
    assert results["frida_010"].status == "gap-revealed"
    assert (
        results["frida_010"].scenario["reveals_gap"]
        == "semantic_search disabled (OPENAI_API_KEY not set)"
    )


def test_eval_runner_tool_args_supports_semantic_search_and_verify_quote() -> None:
    assert _tool_args(
        {
            "tool": "semantic_search",
            "query": "renter rights",
            "dataset": "lover",
            "limit": 3,
        },
    ) == {"query": "renter rights", "dataset": "lover", "limit": 3}

    assert _tool_args(
        {
            "tool": "verify_quote",
            "slug_match": "husleieloven",
            "section_id": "9-5",
            "quote": "Oppsigelse skal være skriftlig.",
        },
    ) == {
        "slug": "husleieloven",
        "section_id": "9-5",
        "quote": "Oppsigelse skal være skriftlig.",
    }
    assert (
        _tool_args(
            {
                "tool": "verify_quote",
                "slug": "avtaleloven",
                "section_id": "36",
                "quote": "urimelig",
            },
        )["slug"]
        == "avtaleloven"
    )


def test_eval_runner_tool_args_preserves_existing_search_tools() -> None:
    assert _tool_args(
        {
            "tool": "search_laws",
            "query_contains": ["arbeidsmiljø"],
            "dataset": "lover",
            "limit": 99,
        },
    ) == {"query": "arbeidsmiljø", "dataset": "lover"}

    assert _tool_args(
        {
            "tool": "search_body",
            "query": "oppsigelse",
            "dataset": "lover",
            "limit": 7,
        },
    ) == {"query": "oppsigelse", "dataset": "lover", "limit": 7}


def test_eval_runner_llm_args_defaults_and_overrides() -> None:
    args = eval_runner._parse_args(["--llm-driven"])
    assert args.llm_driven is True
    assert args.llm_model == "opus"

    args = eval_runner._parse_args(["--llm-driven", "--llm-model", "sonnet"])
    assert args.llm_driven is True
    assert args.llm_model == "sonnet"


def test_load_embedder_accepts_both_openai_env_spellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: list[str] = []

    class FakeOpenAIEmbedder:
        def __init__(self, api_key: str) -> None:
            keys.append(api_key)

    monkeypatch.setattr(eval_runner, "OpenAIEmbedder", FakeOpenAIEmbedder)
    monkeypatch.setenv("OPENAI_API_KEY", "canonical-key")
    monkeypatch.delenv("OPENAI_APIKEY", raising=False)

    assert isinstance(_load_embedder(), FakeOpenAIEmbedder)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_APIKEY", "legacy-key")
    assert isinstance(_load_embedder(), FakeOpenAIEmbedder)
    assert keys == ["canonical-key", "legacy-key"]


def test_load_embedder_without_key_returns_none_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStderr:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.flush_count = 0

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            self.flush_count += 1

    fake_stderr = FakeStderr()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_APIKEY", raising=False)
    monkeypatch.setattr(eval_runner.sys, "stderr", fake_stderr)

    assert _load_embedder() is None

    stderr = "".join(fake_stderr.writes)
    assert (
        "lovspor-eval: OPENAI_API_KEY not set; semantic_search scenarios "
        "will be reported as gap-revealed (skipped). Set the key and rerun "
        "to exercise them against the real OpenAI embedder."
    ) in stderr
    assert fake_stderr.flush_count == 1


def test_run_wires_embedder_into_corpus_reader_and_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = object()
    scenario = {"id": "p_001", "persona": "p", "expected_tool_calls": []}
    fixture = {"generated_at": "2026-05-08T00:00:00+00:00", "documents": []}
    calls: dict[str, Any] = {}

    class FakeCorpusReader:
        def __init__(self, corpus_path: Path, embedder: object | None = None) -> None:
            calls["reader_path"] = corpus_path
            calls["reader_embedder"] = embedder

    def fake_build_synthetic_corpus(
        corpus_path: Path,
        fixture_arg: dict[str, Any],
        embedder: object | None = None,
    ) -> None:
        calls["build_path"] = corpus_path
        calls["build_fixture"] = fixture_arg
        calls["build_embedder"] = embedder

    def fake_run_scenario(
        reader: CorpusReader,
        scenario_arg: dict[str, Any],
        embedder_available: bool = True,
    ) -> eval_runner.ScenarioResult:
        calls["scenario_reader"] = reader
        calls["scenario"] = scenario_arg
        calls["embedder_available"] = embedder_available
        return eval_runner.ScenarioResult(
            scenario=scenario_arg,
            calls=[],
            criteria=[],
            status="pass",
            note="ok",
        )

    def fake_render_report(
        *,
        personas: dict[str, dict[str, Any]],
        results: list[eval_runner.ScenarioResult],
        run_date: str,
        fixture_checksum: str,
    ) -> str:
        assert personas == {"p": {"name": "Persona"}}
        assert len(results) == 1
        assert results[0].status == "pass"
        assert run_date == "2026-05-08"
        assert fixture_checksum
        return "report\n"

    monkeypatch.setattr(eval_runner, "_load_personas", lambda: {"p": {"name": "Persona"}})
    monkeypatch.setattr(eval_runner, "_load_mapping", lambda _path: fixture)
    monkeypatch.setattr(eval_runner, "_load_scenarios", lambda *, persona: [scenario])
    monkeypatch.setattr(eval_runner, "_validate_suite", lambda *args, **kwargs: None)
    monkeypatch.setattr(eval_runner, "_load_embedder", lambda: embedder)
    monkeypatch.setattr(eval_runner, "_build_synthetic_corpus", fake_build_synthetic_corpus)
    monkeypatch.setattr(eval_runner, "CorpusReader", FakeCorpusReader)
    monkeypatch.setattr(eval_runner, "_run_scenario", fake_run_scenario)
    monkeypatch.setattr(eval_runner, "_render_report", fake_render_report)

    output_path = tmp_path / "report.md"
    assert eval_runner.run(["--date", "2026-05-08", "--output", str(output_path)]) == 0

    assert calls["build_embedder"] is embedder
    assert calls["reader_embedder"] is embedder
    assert calls["scenario_reader"].__class__ is FakeCorpusReader
    assert calls["scenario"] is scenario
    assert calls["embedder_available"] is True
    assert output_path.read_text(encoding="utf-8") == "report\n"


def test_eval_runner_skips_semantic_search_when_embedder_unavailable() -> None:
    scenario = {
        "id": "semantic_001",
        "persona": "kari",
        "expected_tool_calls": [{"tool": "semantic_search", "query": "snow from roof"}],
        "success_criteria": [{"kind": "tool_called", "tool": "semantic_search"}],
        "reveals_gap": None,
    }

    result = _run_scenario(
        cast(CorpusReader, object()),
        scenario,
        embedder_available=False,
    )

    assert result.status == "gap-revealed"
    assert result.note == "skipped: semantic_search requires OPENAI_API_KEY"
    assert result.calls == []
    assert result.criteria == []
    assert result.scenario["reveals_gap"] == "semantic_search disabled (OPENAI_API_KEY not set)"
    assert result.scenario["roadmap_class"] == "env-config"
    assert scenario["reveals_gap"] is None


def test_no_key_semantic_search_skip_ranks_env_config_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = [
        {
            "id": "kari_semantic_001",
            "persona": "kari",
            "expected_tool_calls": [{"tool": "semantic_search", "query": "snow from roof"}],
            "success_criteria": [{"kind": "tool_called", "tool": "semantic_search"}],
            "reveals_gap": None,
        },
        {
            "id": "ola_semantic_001",
            "persona": "ola",
            "expected_tool_calls": [{"tool": "semantic_search", "query": "deposit dispute"}],
            "success_criteria": [{"kind": "tool_called", "tool": "semantic_search"}],
            "reveals_gap": None,
        },
    ]
    results = [
        _run_scenario(cast(CorpusReader, object()), scenario, embedder_available=False)
        for scenario in scenarios
    ]

    monkeypatch.setattr(eval_runner, "_lovspor_commit", lambda: "test-commit")
    report = eval_runner._render_report(
        personas={"kari": {"name": "Kari"}, "ola": {"name": "Ola"}},
        results=results,
        run_date="2026-05-08",
        fixture_checksum="abc123",
    )

    assert "**semantic_search disabled (OPENAI_API_KEY not set)**" in report
    assert "roadmap Class env-config" in report
    assert "The top gap to address next is semantic_search disabled" in report
    assert "**None**" not in report
    assert "top gap to address next is None" not in report


def test_eval_runner_invokes_semantic_search_when_embedder_available() -> None:
    reader = _FakeReader()
    scenario = {
        "id": "semantic_001",
        "persona": "ola",
        "expected_tool_calls": [
            {
                "tool": "semantic_search",
                "query": "renter rights",
                "dataset": "lover",
                "limit": 7,
            },
        ],
        "success_criteria": [
            {"kind": "tool_called", "tool": "semantic_search"},
            {"kind": "list_contains_slug", "tool": "semantic_search", "slug": "husleieloven"},
        ],
        "reveals_gap": None,
    }

    result = _run_scenario(cast(CorpusReader, reader), scenario)

    assert result.status == "pass"
    assert reader.semantic_calls == [("renter rights", "lover", 7)]


def test_eval_runner_dispatches_verify_quote() -> None:
    reader = _FakeReader()

    response = _call_reader(
        cast(CorpusReader, reader),
        "verify_quote",
        {
            "slug": "husleieloven",
            "section_id": "9-5",
            "quote": "Oppsigelsen skal være skriftlig.",
        },
    )

    assert response == {"verified": True, "slug": "husleieloven", "section_id": "9-5"}
    assert reader.verify_quote_calls == [
        ("husleieloven", "9-5", "Oppsigelsen skal være skriftlig."),
    ]


def test_eval_runner_dispatches_semantic_search_default_limit() -> None:
    reader = _FakeReader()

    response = _call_reader(
        cast(CorpusReader, reader),
        "semantic_search",
        {"query": "renter rights", "dataset": "lover"},
    )

    assert response == [{"slug": "husleieloven", "section_id": "9-5"}]
    assert reader.semantic_calls == [("renter rights", "lover", 20)]


def test_llm_mcp_config_points_claude_at_synthetic_corpus(tmp_path: Path) -> None:
    corpus_path = tmp_path / "lovverk"

    config_path = eval_runner._write_llm_mcp_config(tmp_path, corpus_path)

    assert config_path == tmp_path / "mcp.json"
    expected_config = {
        "mcpServers": {
            "lovverk": {
                "command": "uv",
                "args": [
                    "run",
                    "--project",
                    str(eval_runner.REPO_ROOT),
                    "lovspor",
                    "mcp",
                    "--corpus-path",
                    str(corpus_path),
                ],
            },
        },
    }
    assert config_path.read_text(encoding="utf-8") == json.dumps(expected_config, indent=2)
    config = cast(dict[str, Any], yaml.safe_load(config_path.read_text(encoding="utf-8")))
    assert config == expected_config


def test_llm_driver_availability_rejects_missing_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str | None:
        return None if name == "claude" else "/bin/not-claude"

    monkeypatch.setattr(eval_runner.shutil, "which", fake_which)

    with pytest.raises(SystemExit) as exc_info:
        eval_runner._ensure_llm_driver_available()

    assert str(exc_info.value) == (
        "lovspor-eval: --llm-driven requires the `claude` CLI on PATH. "
        "Install Claude Code or fall back to the deterministic runner."
    )


def test_llm_driver_availability_rejects_missing_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_runner.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(eval_runner, "REPO_ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        eval_runner._ensure_llm_driver_available()

    assert str(exc_info.value) == (
        f"lovspor-eval: cannot locate the lovspor source tree at {tmp_path} "
        "(needed so the spawned MCP server can resolve `uv run lovspor mcp`)."
    )


def test_llm_driver_availability_accepts_claude_and_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'lovspor'\n", encoding="utf-8")
    monkeypatch.setattr(eval_runner.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(eval_runner, "REPO_ROOT", tmp_path)

    eval_runner._ensure_llm_driver_available()


def test_llm_driver_stream_pairs_tool_calls_and_decodes_payload() -> None:
    stdout = "\n".join(
        [
            "not json",
            "",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "checking corpus"},
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__semantic_search",
                                "input": {"query": "snø fra tak", "limit": 2},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "tool result follows"},
                            {
                                "type": "tool_result",
                                "tool_use_id": "unknown",
                                "content": "ignored",
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(
                                            [{"slug": "grannelova-gl", "section_id": "2"}],
                                        ),
                                    },
                                ],
                            },
                        ],
                    },
                },
            ),
        ],
    )

    calls = eval_runner._parse_llm_driver_stream(stdout)

    assert len(calls) == 1
    assert calls[0].tool == "semantic_search"
    assert calls[0].args == {"query": "snø fra tak", "limit": 2}
    assert calls[0].ok is True
    assert calls[0].response == [{"slug": "grannelova-gl", "section_id": "2"}]


def test_llm_driver_stream_preserves_error_flag_and_empty_defaults() -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "",
                                "input": {},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "is_error": True,
                                "content": [{"type": "text"}],
                            },
                        ],
                    },
                },
            ),
        ],
    )

    calls = eval_runner._parse_llm_driver_stream(stdout)

    assert len(calls) == 1
    assert calls[0].tool == ""
    assert calls[0].args == {}
    assert calls[0].ok is False
    assert calls[0].response == ""


def test_llm_driver_payload_decode_preserves_non_json_content() -> None:
    assert eval_runner._decode_tool_result_payload("plain text") == "plain text"
    assert (
        eval_runner._decode_tool_result_payload([{"type": "text", "text": "not json"}])
        == "not json"
    )
    opaque_blocks = [{"type": "image", "source": {"type": "base64"}}]
    assert eval_runner._decode_tool_result_payload(opaque_blocks) == opaque_blocks


def test_run_llm_driven_writes_mcp_config_and_uses_llm_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = object()
    scenario = {"id": "p_001", "persona": "p", "expected_tool_calls": []}
    fixture = {"generated_at": "2026-05-08T00:00:00+00:00", "documents": []}
    calls: dict[str, Any] = {}

    def fake_build_synthetic_corpus(
        corpus_path: Path,
        fixture_arg: dict[str, Any],
        embedder: object | None = None,
    ) -> None:
        calls["build_path"] = corpus_path
        calls["build_fixture"] = fixture_arg
        calls["build_embedder"] = embedder

    def fake_write_llm_mcp_config(workdir: Path, corpus_path: Path) -> Path:
        calls["mcp_workdir"] = workdir
        calls["mcp_corpus_path"] = corpus_path
        return tmp_path / "mcp.json"

    def fake_run_scenario_llm_driven(
        scenario_arg: dict[str, Any],
        mcp_config_path: Path,
        model: str,
    ) -> eval_runner.ScenarioResult:
        calls["scenario"] = scenario_arg
        calls["mcp_config_path"] = mcp_config_path
        calls["model"] = model
        return eval_runner.ScenarioResult(
            scenario=scenario_arg,
            calls=[],
            criteria=[],
            status="pass",
            note="ok",
        )

    def fake_render_report(
        *,
        personas: dict[str, dict[str, Any]],
        results: list[eval_runner.ScenarioResult],
        run_date: str,
        fixture_checksum: str,
    ) -> str:
        assert personas == {"p": {"name": "Persona"}}
        assert len(results) == 1
        assert results[0].status == "pass"
        assert run_date == "2026-05-08"
        assert fixture_checksum
        return "report\n"

    monkeypatch.setattr(eval_runner, "_load_personas", lambda: {"p": {"name": "Persona"}})
    monkeypatch.setattr(eval_runner, "_load_mapping", lambda _path: fixture)
    monkeypatch.setattr(eval_runner, "_load_scenarios", lambda *, persona: [scenario])
    monkeypatch.setattr(eval_runner, "_validate_suite", lambda *args, **kwargs: None)
    monkeypatch.setattr(eval_runner, "_load_embedder", lambda: embedder)
    monkeypatch.setattr(eval_runner, "_build_synthetic_corpus", fake_build_synthetic_corpus)
    monkeypatch.setattr(eval_runner, "_ensure_llm_driver_available", lambda: None)
    monkeypatch.setattr(eval_runner, "_write_llm_mcp_config", fake_write_llm_mcp_config)
    monkeypatch.setattr(eval_runner, "_run_scenario_llm_driven", fake_run_scenario_llm_driven)
    monkeypatch.setattr(eval_runner, "_render_report", fake_render_report)
    monkeypatch.setattr(
        eval_runner,
        "CorpusReader",
        lambda *args, **kwargs: pytest.fail("llm-driven run must not build CorpusReader"),
    )

    output_path = tmp_path / "report.md"
    assert (
        eval_runner.run(
            [
                "--llm-driven",
                "--llm-model",
                "haiku",
                "--date",
                "2026-05-08",
                "--output",
                str(output_path),
            ],
        )
        == 0
    )

    assert calls["build_embedder"] is embedder
    assert calls["build_fixture"] is fixture
    assert calls["mcp_corpus_path"] == calls["build_path"]
    assert calls["scenario"] is scenario
    assert calls["mcp_config_path"] == tmp_path / "mcp.json"
    assert calls["model"] == "haiku"
    assert output_path.read_text(encoding="utf-8") == "report\n"


def test_llm_driven_scenario_invokes_claude_and_evaluates_tool_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__semantic_search",
                                "input": {"query": "depositum", "limit": 3},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": json.dumps(
                                    [{"slug": "husleieloven", "section_id": "3-5"}],
                                ),
                            },
                        ],
                    },
                },
            ),
        ],
    )
    recorded: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> object:
        recorded["cmd"] = cmd
        recorded.update(kwargs)
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    scenario = {
        "id": "ola_001",
        "persona": "ola",
        "user_query": "Kan jeg holde tilbake depositum?",
        "success_criteria": [
            {"kind": "tool_called", "tool": "semantic_search"},
            {"kind": "list_contains_slug", "tool": "semantic_search", "slug": "husleieloven"},
        ],
    }

    result = eval_runner._run_scenario_llm_driven(scenario, tmp_path / "mcp.json", "haiku")

    assert result.status == "pass"
    assert result.note == "all criteria passed"
    assert result.calls[0].tool == "semantic_search"
    assert recorded["cmd"] == [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--verbose",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--strict-mcp-config",
        "--mcp-config",
        str(tmp_path / "mcp.json"),
        "--permission-mode",
        "bypassPermissions",
        "--model",
        "haiku",
    ]
    assert recorded["input"] == (
        "You are simulating a Norwegian-law user. Use the lovverk MCP tools to research\n"
        "and answer the question below. Stay grounded in the corpus.\n"
        "\n"
        "User question:\n"
        "Kan jeg holde tilbake depositum?\n"
        "\n"
        "Tool usage guidance:\n"
        "- Use semantic_search when the user's wording differs from the law's vocabulary.\n"
        "  If its results are empty, heed the returned notice: report that the corpus has\n"
        "  no strong match instead of answering from memory.\n"
        "- Use search_laws / search_body for keyword discovery.\n"
        "- Use get_section to read the verbatim text of one section. The response includes\n"
        "  a validated cross_references list — heed it.\n"
        "- Before quoting any verbatim text in your final answer, call verify_quote to\n"
        "  confirm it appears in the cited section.\n"
        "- Use validate_citation if you cite a specific 'section N-M slug' reference.\n"
        "\n"
        "Answer the user concisely after grounding your response."
    )
    assert recorded["capture_output"] is True
    assert recorded["text"] is True
    assert recorded["timeout"] == eval_runner._LLM_DRIVER_TIMEOUT_SECONDS
    assert recorded["check"] is False


def test_llm_driven_scenario_missing_user_query_uses_blank_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> object:
        recorded.update(kwargs)
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)

    result = eval_runner._run_scenario_llm_driven(
        {"id": "ola_001", "persona": "ola"},
        tmp_path / "mcp.json",
        "haiku",
    )

    assert result.status == "pass"
    assert "\nUser question:\n\n\nTool usage guidance:\n" in recorded["input"]


def test_llm_driven_scenario_timeout_is_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> object:
        raise eval_runner.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)

    result = eval_runner._run_scenario_llm_driven(
        {"id": "ola_001", "persona": "ola", "user_query": "Hei"},
        tmp_path / "mcp.json",
        "haiku",
    )

    assert result.status == "fail"
    assert result.calls == []
    assert result.criteria == []
    assert result.note == "llm-driven scenario timed out after 300s"


def test_llm_driven_scenario_nonzero_without_tool_calls_is_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="rate limited by provider",
        )

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)

    result = eval_runner._run_scenario_llm_driven(
        {"id": "ola_001", "persona": "ola", "user_query": "Hei"},
        tmp_path / "mcp.json",
        "haiku",
    )

    assert result.status == "fail"
    assert result.calls == []
    assert result.note == "claude -p exited 1: rate limited by provider"


def test_llm_driven_scenario_truncates_nonzero_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "x" * 250

    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(cmd, 2, stdout="", stderr=stderr)

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)

    result = eval_runner._run_scenario_llm_driven(
        {"id": "ola_001", "persona": "ola", "user_query": "Hei"},
        tmp_path / "mcp.json",
        "haiku",
    )

    assert result.status == "fail"
    assert result.note == f"claude -p exited 2: {'x' * 200}"


def test_llm_driven_gap_revealed_scenario_keeps_gap_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    scenario = {
        "id": "ola_001",
        "persona": "ola",
        "user_query": "Hei",
        "expected_outcome": "gap_revealed",
        "reveals_gap": "needs time-machine search",
        "success_criteria": [{"kind": "tool_called", "tool": "search_laws"}],
    }

    result = eval_runner._run_scenario_llm_driven(scenario, tmp_path / "mcp.json", "haiku")

    assert result.status == "gap-revealed"
    assert result.criteria[0].passed is False
    assert result.note == "gap revealed: needs time-machine search"


def test_llm_driven_scenario_reports_partial_and_fail_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__search_laws",
                                "input": {"query": "husleie"},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": json.dumps([{"slug": "husleieloven"}]),
                            },
                        ],
                    },
                },
            ),
        ],
    )

    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    partial_result = eval_runner._run_scenario_llm_driven(
        {
            "id": "ola_001",
            "persona": "ola",
            "user_query": "Finn husleieloven",
            "success_criteria": [
                {"kind": "tool_called", "tool": "search_laws"},
                {"kind": "tool_called", "tool": "get_section"},
            ],
        },
        tmp_path / "mcp.json",
        "haiku",
    )
    fail_result = eval_runner._run_scenario_llm_driven(
        {
            "id": "ola_002",
            "persona": "ola",
            "user_query": "Finn en paragraf",
            "success_criteria": [{"kind": "tool_called", "tool": "get_section"}],
        },
        tmp_path / "mcp.json",
        "haiku",
    )

    assert partial_result.status == "partial"
    assert partial_result.note == "1 criteria failed"
    assert fail_result.status == "fail"
    assert fail_result.note == "1 criteria failed"


def test_llm_driven_nonzero_exit_fails_even_after_tool_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__search_laws",
                                "input": {"query": "husleie"},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": json.dumps([{"slug": "husleieloven"}]),
                            },
                        ],
                    },
                },
            ),
        ],
    )

    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(
            cmd,
            2,
            stdout=stdout,
            stderr="provider error after partial stream",
        )

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    scenario = {
        "id": "ola_001",
        "persona": "ola",
        "user_query": "Finn husleieloven",
        "success_criteria": [{"kind": "tool_called", "tool": "search_laws"}],
    }

    result = eval_runner._run_scenario_llm_driven(scenario, tmp_path / "mcp.json", "haiku")

    assert result.status == "fail"
    assert [call.tool for call in result.calls] == ["search_laws"]
    assert result.criteria == []
    assert result.note == "claude -p exited 2: provider error after partial stream"


def test_llm_driven_unexpected_tool_error_fails_like_deterministic_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__get_section",
                                "input": {"slug": "missing", "section_id": "1"},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "is_error": True,
                                "content": "section not found",
                            },
                        ],
                    },
                },
            ),
        ],
    )

    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    scenario = {
        "id": "ola_001",
        "persona": "ola",
        "user_query": "Les manglende paragraf",
        "success_criteria": [{"kind": "tool_called", "tool": "get_section"}],
    }

    result = eval_runner._run_scenario_llm_driven(scenario, tmp_path / "mcp.json", "haiku")

    assert result.status == "fail"
    assert result.note == "unexpected get_section error: section not found"


def test_llm_driven_expected_tool_error_can_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "mcp__lovverk__get_section",
                                "input": {"slug": "missing", "section_id": "1"},
                            },
                        ],
                    },
                },
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "is_error": True,
                                "content": "section not found",
                            },
                        ],
                    },
                },
            ),
        ],
    )

    def fake_run(cmd: list[str], **_kwargs: Any) -> object:
        return eval_runner.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    scenario = {
        "id": "ola_001",
        "persona": "ola",
        "user_query": "Les manglende paragraf",
        "success_criteria": [
            {"kind": "tool_called", "tool": "get_section"},
            {
                "kind": "tool_error_contains",
                "tool": "get_section",
                "target": "section not found",
            },
        ],
    }

    result = eval_runner._run_scenario_llm_driven(scenario, tmp_path / "mcp.json", "haiku")

    assert result.status == "pass"
    assert result.note == "all criteria passed"
    assert [criterion.passed for criterion in result.criteria] == [True, True]


def test_build_synthetic_corpus_writes_embeddings_when_embedder_is_present(
    tmp_path: Path,
) -> None:
    fixture = {
        "generated_at": "2026-05-08T00:00:00+00:00",
        "documents": [
            {
                "doc_id": "lov-test",
                "dataset": "lover",
                "slug": "synthetic-lov",
                "title": "Synthetic lov",
                "last_changed": "2026-05-08",
                "total_changes": 1,
                "body": "### § 1. Første regel\n\nFørste tekst.",
                "history": [],
            },
        ],
    }

    _build_synthetic_corpus(tmp_path, fixture, embedder=_FakeEmbedder())

    assert (tmp_path / "lover" / "synthetic-lov.md").exists()
    assert (tmp_path / "lover" / "history" / "synthetic-lov.json").exists()
    assert (tmp_path / "lover" / "embeddings" / "synthetic-lov.bin").exists()


def test_write_embeddings_creates_synthetic_sidecar_with_section_ids(tmp_path: Path) -> None:
    embedder = _FakeEmbedder()
    first_document = {
        "dataset": "lover",
        "slug": "synthetic-lov",
        "body": (
            "## Kapittel 1\n\n"
            "### § 1. Første regel\n\n"
            "Første tekst.\n\n"
            "### § 2. Andre regel\n\n"
            "Andre tekst.\n"
        ),
    }
    second_document = {
        "dataset": "lover",
        "slug": "synthetic-lov-2",
        "body": "### § 3. Tredje regel\n\nTredje tekst.",
    }

    _write_embeddings(tmp_path, first_document, embedder)
    _write_embeddings(tmp_path, second_document, embedder)

    embedding_file = read_embeddings(tmp_path / "lover" / "embeddings" / "synthetic-lov.bin")
    assert embedder.requests[0] == [
        "### § 1. Første regel\n\nFørste tekst.",
        "### § 2. Andre regel\n\nAndre tekst.",
    ]
    assert embedder.requests[1] == ["### § 3. Tredje regel\n\nTredje tekst."]
    assert embedding_file.dim == EMBEDDING_DIM
    assert [section_id for section_id, _vector in embedding_file.sections] == ["1", "2"]


def test_write_embeddings_empty_doc_writes_header_only_file(tmp_path: Path) -> None:
    document = {
        "dataset": "lover",
        "slug": "empty-lov",
        "body": "## Kapittel uten paragrafer\n\nBare innledning.",
    }

    _write_embeddings(tmp_path, document, _FakeEmbedder())

    embedding_file = read_embeddings(tmp_path / "lover" / "embeddings" / "empty-lov.bin")
    assert embedding_file.dim == EMBEDDING_DIM
    assert embedding_file.scale == 1.0
    assert embedding_file.sections == []


def _pyproject() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_wheel_does_not_ship_a_top_level_evals_package() -> None:
    """The PyPI import-name collision guard.

    A top-level ``evals/`` in the wheel lands in site-packages under a
    generic name that OpenAI's ``evals`` distribution also claims — co-install
    both and one shadows the other. lovspor is published, so this was live,
    not hypothetical.
    """
    packages = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["src/lovspor"], packages


def test_no_console_script_resolves_into_evals() -> None:
    """``evals`` is repo-only tooling and cannot run from an installed wheel.

    ``evals/runner.py`` resolves scenarios, personas and fixtures relative to
    the source tree, and ``--llm-driven`` looks for the repo's pyproject.toml —
    so a shipped entry point pointing into it is broken by construction. Run it
    from a checkout: ``uv run python -m evals.runner``.
    """
    scripts = _pyproject()["project"]["scripts"]

    assert not [name for name, target in scripts.items() if target.startswith("evals")], scripts


def test_embeddings_extra_exists_for_the_benchmark() -> None:
    """The command the benchmark tells you to run must actually work.

    ``benchmarks/embedding_comparison/README.md``, run.py's module docstring and
    the ImportError it raises when sentence-transformers is missing all print
    ``uv sync --extra embeddings``. With no extra declared that fails from a
    clean clone — and the "+24% Recall@5" model choice in ``docs/embeddings.md``
    rests on a benchmark nobody else can re-run.
    """
    extras = _pyproject()["project"]["optional-dependencies"]
    declared = [d for d in extras["embeddings"] if d.lower().startswith("sentence-transformers")]

    assert declared, extras


def test_embeddings_extra_is_not_forced_on_installers() -> None:
    """The extra exists precisely so torch stays optional.

    sentence-transformers pulls torch — gigabytes — and only the benchmark's two
    local NbAiLab models need it. Production embeds through the OpenAI API over
    httpx and imports neither, so a ``pip install lovspor`` must not drag them in.
    """
    runtime = _pyproject()["project"]["dependencies"]
    heavy = [d for d in runtime if d.lower().startswith(("sentence-transformers", "torch"))]

    assert not heavy, runtime


def test_pyyaml_is_not_forced_on_installers() -> None:
    """pyyaml is imported by evals/ only, never by src/lovspor.

    With evals out of the wheel, keeping pyyaml as a runtime dependency would
    force it on every ``pip install lovspor`` for code the wheel no longer
    contains. It belongs in the dev group.
    """
    data = _pyproject()
    runtime = data["project"]["dependencies"]
    dev = data["dependency-groups"]["dev"]

    assert not [d for d in runtime if d.lower().startswith("pyyaml")], runtime
    assert [d for d in dev if d.lower().startswith("pyyaml")], dev
