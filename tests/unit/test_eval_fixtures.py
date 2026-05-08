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
