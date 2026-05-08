"""Deterministic persona eval runner for the lovspor MCP tool surface."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from lovspor.embeddings.model import EmbeddingModel, OpenAIEmbedder
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.sections import iter_sections
from lovspor.embeddings.store import write_embeddings
from lovspor.mcp import CorpusReader
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
SCENARIOS_DIR = ROOT / "scenarios"
FIXTURE_PATH = ROOT / "fixtures" / "synthetic_corpus.yaml"
RESULTS_DIR = ROOT / "results"

SOURCE_DATASET_BY_DIR = {
    "lover": "gjeldende-lover",
    "forskrifter": "gjeldende-sentrale-forskrifter",
}
DOC_TYPE_BY_DIR = {
    "lover": "lov",
    "forskrifter": "forskrift",
}
Status = Literal["pass", "fail", "partial", "gap-revealed"]
EXPECTED_PERSONAS = 9
EXPECTED_SCENARIOS_PER_PERSONA = 10
MIN_GAP_SCENARIOS = 16
CriterionHandler = Callable[[str, dict[str, Any], list["ToolCallResult"]], "CriterionResult"]


@dataclass(frozen=True)
class ToolCallResult:
    tool: str
    args: dict[str, Any]
    ok: bool
    response: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "response": self.response,
        }


@dataclass(frozen=True)
class CriterionResult:
    kind: str
    passed: bool
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "passed": self.passed,
            "note": self.note,
        }


@dataclass(frozen=True)
class ScenarioResult:
    scenario: dict[str, Any]
    calls: list[ToolCallResult]
    criteria: list[CriterionResult]
    status: Status
    note: str

    @property
    def persona(self) -> str:
        return cast(str, self.scenario["persona"])

    @property
    def scenario_id(self) -> str:
        return cast(str, self.scenario["id"])


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    personas = _load_personas()
    fixture = _load_mapping(FIXTURE_PATH)
    scenarios = _load_scenarios(persona=args.persona)
    _validate_suite(personas, scenarios, filtered_persona=args.persona)

    embedder = _load_embedder()
    with tempfile.TemporaryDirectory(prefix="lovspor-eval-") as tmp:
        corpus_path = Path(tmp) / "lovverk"
        _build_synthetic_corpus(corpus_path, fixture, embedder=embedder)
        if args.llm_driven:
            _ensure_llm_driver_available()
            mcp_config_path = _write_llm_mcp_config(Path(tmp), corpus_path)
            results = [
                _run_scenario_llm_driven(scenario, mcp_config_path, args.llm_model)
                for scenario in scenarios
            ]
        else:
            reader = CorpusReader(corpus_path, embedder=embedder)
            results = [
                _run_scenario(reader, scenario, embedder_available=embedder is not None)
                for scenario in scenarios
            ]

    checksum = _fixture_checksum(fixture)
    output_path = args.output or RESULTS_DIR / f"{args.run_date}.md"
    report = _render_report(
        personas=personas,
        results=results,
        run_date=args.run_date,
        fixture_checksum=checksum,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    counts = Counter(result.status for result in results)
    print(
        "lovspor-eval: "
        f"{counts['pass']} pass, "
        f"{counts['partial']} partial, "
        f"{counts['fail']} fail, "
        f"{counts['gap-revealed']} gap-revealed",
    )
    print(f"report: {output_path}")
    if args.strict and (counts["fail"] or counts["partial"]):
        return 1
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lovspor-eval",
        description="Run persona-driven evals against a synthetic lovverk MCP corpus.",
    )
    parser.add_argument("--persona", help="Run one persona id, e.g. anne.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any fail or partial result. Declared gaps are informational.",
    )
    parser.add_argument(
        "--date",
        dest="run_date",
        default=date.today().isoformat(),
        help="Report date, default: today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write report to a custom path instead of evals/results/<date>.md.",
    )
    parser.add_argument(
        "--llm-driven",
        action="store_true",
        help=(
            "Drive each scenario via a real Claude Code subprocess (claude -p) "
            "with the lovverk MCP server. Default is the deterministic runner "
            "that invokes tools directly from each scenario's expected_tool_calls."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default="opus",
        help=(
            "Model alias passed to claude -p when --llm-driven is set. "
            "Cost approximations on 10 scenarios: haiku ~$0.60, sonnet ~$1.80, "
            "opus ~$9. Default opus targets the production-fidelity end."
        ),
    )
    return parser.parse_args(argv)


def _load_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return cast(dict[str, Any], loaded)


def _load_personas() -> dict[str, dict[str, Any]]:
    data = _load_mapping(ROOT / "personas.yaml")
    personas = data.get("personas")
    if not isinstance(personas, list):
        raise ValueError("evals/personas.yaml must contain personas: []")
    by_id: dict[str, dict[str, Any]] = {}
    for persona in personas:
        if not isinstance(persona, dict) or not isinstance(persona.get("id"), str):
            raise ValueError("each persona must be a mapping with string id")
        by_id[persona["id"]] = persona
    return by_id


def _load_scenarios(*, persona: str | None) -> list[dict[str, Any]]:
    files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    scenarios: list[dict[str, Any]] = []
    for path in files:
        data = _load_mapping(path)
        loaded = data.get("scenarios")
        if not isinstance(loaded, list):
            raise ValueError(f"{path} must contain scenarios: []")
        for scenario in loaded:
            if not isinstance(scenario, dict):
                raise ValueError(f"{path} contains a non-mapping scenario")
            if persona is None or scenario.get("persona") == persona:
                scenarios.append(cast(dict[str, Any], scenario))
    scenarios.sort(key=lambda item: cast(str, item["id"]))
    if persona is not None and not scenarios:
        raise ValueError(f"unknown persona or no scenarios: {persona}")
    return scenarios


def _validate_suite(
    personas: dict[str, dict[str, Any]],
    scenarios: list[dict[str, Any]],
    *,
    filtered_persona: str | None,
) -> None:
    if filtered_persona is None and len(personas) != EXPECTED_PERSONAS:
        raise ValueError(f"expected {EXPECTED_PERSONAS} personas, found {len(personas)}")
    scenario_counts = Counter(cast(str, scenario.get("persona")) for scenario in scenarios)
    if filtered_persona is None:
        missing = sorted(set(personas) - set(scenario_counts))
        if missing:
            raise ValueError(f"personas without scenarios: {', '.join(missing)}")
        bad_counts = {
            persona: count
            for persona, count in sorted(scenario_counts.items())
            if count != EXPECTED_SCENARIOS_PER_PERSONA
        }
        if bad_counts:
            raise ValueError(f"expected 10 scenarios per persona, got {bad_counts}")
        gap_count = sum(1 for scenario in scenarios if scenario.get("reveals_gap"))
        if gap_count < MIN_GAP_SCENARIOS:
            raise ValueError(f"expected at least 16 gap scenarios, found {gap_count}")
    unknown_personas = sorted(set(scenario_counts) - set(personas))
    if unknown_personas:
        raise ValueError(f"scenarios reference unknown personas: {', '.join(unknown_personas)}")


def _load_embedder() -> EmbeddingModel | None:
    """Return a real ``OpenAIEmbedder`` when ``OPENAI_API_KEY`` is set,
    otherwise ``None``.

    No mocks (per project rule) — we either run the eval suite against
    the real embedder or skip the semantic_search scenarios cleanly.
    Reads either ``OPENAI_API_KEY`` or the underscore-less
    ``OPENAI_APIKEY`` to match what ``Settings.from_env`` accepts on
    the engine side.
    """
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
    if not api_key:
        print(
            "lovspor-eval: OPENAI_API_KEY not set; semantic_search scenarios "
            "will be reported as gap-revealed (skipped). Set the key and rerun "
            "to exercise them against the real OpenAI embedder.",
            file=sys.stderr,
            flush=True,
        )
        return None
    return OpenAIEmbedder(api_key=api_key)


def _build_synthetic_corpus(
    corpus_path: Path,
    fixture: dict[str, Any],
    embedder: EmbeddingModel | None = None,
) -> None:
    if corpus_path.exists():
        shutil.rmtree(corpus_path)
    corpus_path.mkdir(parents=True)

    generated_at = datetime.fromisoformat(cast(str, fixture["generated_at"]))
    documents = cast(list[dict[str, Any]], fixture["documents"])
    records: dict[str, ManifestRecord] = {}
    for document in documents:
        record = _manifest_record(document, generated_at)
        records[cast(str, document["doc_id"])] = record
        _write_markdown(corpus_path, document)
        _write_history(corpus_path, document)
        if embedder is not None:
            _write_embeddings(corpus_path, document, embedder)

    write_manifest(
        Manifest(generated_at=generated_at, documents=records),
        corpus_path / "manifest.json",
    )


def _write_embeddings(
    corpus_path: Path,
    document: dict[str, Any],
    embedder: EmbeddingModel,
) -> None:
    """Write the per-doc ``.bin`` file for the synthetic corpus by
    embedding every ``### §`` section through the real OpenAI
    embedder. Mirrors the production
    ``lovspor.sync.orchestrator._write_embeddings_for_doc`` flow so
    the synthetic-vs-production drift PR #54 prevented for slugs is
    not reintroduced for embeddings file format.
    """
    dataset_dir = cast(str, document["dataset"])
    slug = cast(str, document["slug"])
    body = cast(str, document["body"])
    bin_path = corpus_path / dataset_dir / "embeddings" / f"{slug}.bin"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    sections = iter_sections(body)
    if not sections:
        write_embeddings(bin_path, [], scale=1.0)
        return
    texts = [section.text for section in sections]
    matrix = embedder.encode(texts)
    quantized, scale = quantize_int8(matrix)
    pairs = [(section.section_id, quantized[index]) for index, section in enumerate(sections)]
    write_embeddings(bin_path, pairs, scale)


def _manifest_record(document: dict[str, Any], generated_at: datetime) -> ManifestRecord:
    dataset_dir = cast(str, document["dataset"])
    slug = cast(str, document["slug"])
    body = cast(str, document["body"])
    doc_id = cast(str, document["doc_id"])
    return ManifestRecord(
        doc_type=DOC_TYPE_BY_DIR[dataset_dir],
        xml_hash=hashlib.sha256(f"{doc_id}\n{body}".encode()).hexdigest(),
        markdown_path=f"{dataset_dir}/{slug}.md",
        source_dataset=SOURCE_DATASET_BY_DIR[dataset_dir],
        last_seen=generated_at,
        status="current",
        slug=slug,
        title=cast(str, document["title"]),
        total_changes=cast(int, document["total_changes"]),
        last_changed=cast(str, document["last_changed"]),
        eu_basis=list(cast(list[str], document.get("eu_basis", []))),
    )


def _write_markdown(corpus_path: Path, document: dict[str, Any]) -> None:
    dataset_dir = cast(str, document["dataset"])
    slug = cast(str, document["slug"])
    title = cast(str, document["title"])
    doc_id = cast(str, document["doc_id"])
    eu_basis = list(cast(list[str], document.get("eu_basis", [])))
    eu_lines = (
        ["eu_basis: []"] if not eu_basis else ["eu_basis:"] + [f"  - {celex}" for celex in eu_basis]
    )
    frontmatter = "\n".join(
        [
            "---",
            f"id: {doc_id}",
            f"slug: {slug}",
            f"type: {DOC_TYPE_BY_DIR[dataset_dir]}",
            f'title: "{title}"',
            f'last_updated: "{document["last_changed"]}"',
            "source_provider: Lovdata",
            "source_license: NLOD 2.0",
            *eu_lines,
            "---",
            "",
        ],
    )
    text = f"{frontmatter}# {title}\n\n{cast(str, document['body']).rstrip()}\n"
    path = corpus_path / dataset_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_history(corpus_path: Path, document: dict[str, Any]) -> None:
    dataset_dir = cast(str, document["dataset"])
    slug = cast(str, document["slug"])
    payload = {
        "schema_version": 1,
        "slug": slug,
        "doc_id": document["doc_id"],
        "events": document.get("history", []),
    }
    history_dir = corpus_path / dataset_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{slug}.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _run_scenario(
    reader: CorpusReader,
    scenario: dict[str, Any],
    embedder_available: bool = True,
) -> ScenarioResult:
    call_specs = cast(list[dict[str, Any]], scenario.get("expected_tool_calls", []))
    if not embedder_available and any(spec.get("tool") == "semantic_search" for spec in call_specs):
        # Inject a synthetic gap label so the no-key report does not
        # rank ``None`` as the top roadmap gap when retrofitted
        # scenarios (with reveals_gap: null) hit the skip path.
        # ``env-config`` is the roadmap class so operators see this
        # is an environment issue, not a missing product feature.
        skipped_scenario = {
            **scenario,
            "reveals_gap": "semantic_search disabled (OPENAI_API_KEY not set)",
            "roadmap_class": "env-config",
        }
        return ScenarioResult(
            scenario=skipped_scenario,
            calls=[],
            criteria=[],
            status="gap-revealed",
            note="skipped: semantic_search requires OPENAI_API_KEY",
        )
    calls = [_invoke_tool(reader, spec) for spec in call_specs]
    criteria = [
        _evaluate_criterion(cast(dict[str, Any], criterion), calls)
        for criterion in cast(list[dict[str, Any]], scenario.get("success_criteria", []))
    ]
    expected_error_tools = {
        cast(str, criterion.get("tool"))
        for criterion in cast(list[dict[str, Any]], scenario.get("success_criteria", []))
        if criterion.get("kind") == "tool_error_contains" and isinstance(criterion.get("tool"), str)
    }
    unexpected_errors = [
        call for call in calls if not call.ok and call.tool not in expected_error_tools
    ]

    if scenario.get("expected_outcome") == "gap_revealed":
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="gap-revealed",
            note=f"gap revealed: {scenario.get('reveals_gap')}",
        )
    if unexpected_errors:
        first = unexpected_errors[0]
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="fail",
            note=f"unexpected {first.tool} error: {_response_text(first.response)}",
        )
    failed = [criterion for criterion in criteria if not criterion.passed]
    if not failed:
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="pass",
            note="all criteria passed",
        )
    status: Status = "partial" if len(failed) < len(criteria) else "fail"
    return ScenarioResult(
        scenario=scenario,
        calls=calls,
        criteria=criteria,
        status=status,
        note=f"{len(failed)} criteria failed",
    )


def _invoke_tool(reader: CorpusReader, spec: dict[str, Any]) -> ToolCallResult:
    tool = cast(str, spec["tool"])
    args = _tool_args(spec)
    try:
        response = _call_reader(reader, tool, args)
    except Exception as exc:
        return ToolCallResult(
            tool=tool,
            args=args,
            ok=False,
            response={"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return ToolCallResult(tool=tool, args=args, ok=True, response=response)


def _tool_args(spec: dict[str, Any]) -> dict[str, Any]:
    tool = cast(str, spec["tool"])
    if tool in {"get_law", "get_law_history", "get_eu_basis"}:
        args = {"slug": spec.get("slug") or spec.get("slug_match")}
    elif tool == "get_section":
        args = {
            "slug": spec.get("slug") or spec.get("slug_match"),
            "section_id": spec["section_id"],
        }
    elif tool in {"search_laws", "search_body", "semantic_search"}:
        query = spec.get("query")
        if query is None:
            query = " ".join(cast(list[str], spec.get("query_contains", [])))
        args = {"query": query}
        if "dataset" in spec:
            args["dataset"] = spec["dataset"]
        if tool in {"search_body", "semantic_search"} and "limit" in spec:
            args["limit"] = spec["limit"]
    elif tool == "validate_citation":
        args = {"citation": spec["citation"]}
    elif tool == "verify_quote":
        args = {
            "slug": spec.get("slug") or spec.get("slug_match"),
            "section_id": spec["section_id"],
            "quote": spec["quote"],
        }
    elif tool == "search_eu_implementations":
        args = {"eu_doc_id": str(spec["eu_doc_id"])}
    elif tool == "list_recent_changes":
        args = {
            key: spec[key]
            for key in ("dataset", "since", "limit")
            if key in spec and spec[key] is not None
        }
    elif tool == "corpus_status":
        args = {}
    else:
        raise ValueError(f"unsupported tool in scenario: {tool}")
    return args


def _call_reader(reader: CorpusReader, tool: str, args: dict[str, Any]) -> Any:
    handlers: dict[str, Callable[[], Any]] = {
        "get_law": lambda: reader.get_law(cast(str, args["slug"])),
        "get_section": lambda: reader.get_section(
            cast(str, args["slug"]),
            cast(str, args["section_id"]),
        ),
        "get_law_history": lambda: reader.get_law_history(cast(str, args["slug"])),
        "list_recent_changes": lambda: reader.list_recent_changes(
            dataset=cast(str | None, args.get("dataset")),
            since=cast(str | None, args.get("since")),
            limit=cast(int, args.get("limit", 20)),
        ),
        "search_laws": lambda: reader.search_laws(
            cast(str, args["query"]),
            dataset=cast(str | None, args.get("dataset")),
        ),
        "search_body": lambda: reader.search_body(
            cast(str, args["query"]),
            dataset=cast(str | None, args.get("dataset")),
            limit=cast(int, args.get("limit", 20)),
        ),
        "semantic_search": lambda: reader.semantic_search(
            cast(str, args["query"]),
            dataset=cast(str | None, args.get("dataset")),
            limit=cast(int, args.get("limit", 20)),
        ),
        "validate_citation": lambda: reader.validate_citation(cast(str, args["citation"])),
        "verify_quote": lambda: reader.verify_quote(
            cast(str, args["slug"]),
            cast(str, args["section_id"]),
            cast(str, args["quote"]),
        ),
        "get_eu_basis": lambda: reader.get_eu_basis(cast(str, args["slug"])),
        "search_eu_implementations": lambda: reader.search_eu_implementations(
            cast(str, args["eu_doc_id"]),
        ),
        "corpus_status": reader.corpus_status,
    }
    try:
        handler = handlers[tool]
    except KeyError as exc:
        raise ValueError(f"unsupported tool: {tool}") from exc
    return handler()


def _evaluate_criterion(
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    kind = cast(str, criterion["kind"])
    try:
        handler = CRITERION_HANDLERS[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported success criterion: {kind}") from exc
    return handler(kind, criterion, calls)


def _tool_called(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    return _criterion(
        kind,
        any(call.tool == criterion["tool"] for call in calls),
        f"tool {criterion['tool']} was called",
        f"tool {criterion['tool']} was not called",
    )


def _criterion(kind: str, passed: bool, pass_note: str, fail_note: str) -> CriterionResult:
    return CriterionResult(kind=kind, passed=passed, note=pass_note if passed else fail_note)


def _response_contains(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    target = str(criterion["target"]).lower()
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    passed = any(target in _response_text(call.response).lower() for call in selected)
    return _criterion(
        kind,
        passed,
        f"response contained {criterion['target']!r}",
        f"response did not contain {criterion['target']!r}",
    )


def _list_contains_slug(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    slug = criterion["slug"]
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    passed = any(
        isinstance(call.response, list)
        and any(isinstance(item, dict) and item.get("slug") == slug for item in call.response)
        for call in selected
    )
    return _criterion(
        kind,
        passed,
        f"result list contained slug {slug}",
        f"result list did not contain slug {slug}",
    )


def _result_count(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
    *,
    equals: bool,
) -> CriterionResult:
    count = cast(int, criterion["count"])
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    lengths = [len(call.response) for call in selected if isinstance(call.response, list)]
    passed = (
        any(length == count for length in lengths)
        if equals
        else any(length >= count for length in lengths)
    )
    comparator = "equaled" if equals else "was at least"
    return _criterion(
        kind,
        passed,
        f"result count {comparator} {count}",
        f"result counts {lengths or '[]'} did not satisfy {comparator} {count}",
    )


def _citation_valid(
    kind: str,
    calls: list[ToolCallResult],
    *,
    expected: bool,
) -> CriterionResult:
    selected = _calls_for_tool(calls, "validate_citation")
    passed = any(
        isinstance(call.response, dict) and call.response.get("valid") is expected
        for call in selected
    )
    label = "valid" if expected else "invalid"
    return _criterion(
        kind,
        passed,
        f"citation was {label}",
        f"no validate_citation response was {label}",
    )


def _tool_error_contains(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    target = str(criterion["target"]).lower()
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    passed = any(
        (not call.ok) and target in _response_text(call.response).lower() for call in selected
    )
    return _criterion(
        kind,
        passed,
        f"tool error contained {criterion['target']!r}",
        f"tool error did not contain {criterion['target']!r}",
    )


def _no_hallucinated_section(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    expected_slug = criterion.get("slug_match")
    expected_section = criterion.get("section_id")
    passed = any(
        call.tool == "get_section"
        and call.ok
        and isinstance(call.response, dict)
        and (expected_slug is None or call.response.get("slug") == expected_slug)
        and (expected_section is None or call.response.get("section_id") == expected_section)
        for call in calls
    )
    return _criterion(
        kind,
        passed,
        "get_section returned the expected slug and section",
        "get_section did not return the expected slug and section",
    )


def _history_event_count(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    expected = cast(int, criterion["count"])
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    counts = [
        len(call.response.get("events", [])) for call in selected if isinstance(call.response, dict)
    ]
    passed = any(count >= expected for count in counts)
    return _criterion(
        kind,
        passed,
        f"history had at least {expected} events",
        f"history event counts {counts or '[]'} were below {expected}",
    )


def _history_contains_type(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    event_type = criterion["event_type"]
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    passed = any(
        isinstance(call.response, dict)
        and any(event.get("type") == event_type for event in call.response.get("events", []))
        for call in selected
    )
    return _criterion(
        kind,
        passed,
        f"history contained event type {event_type}",
        f"history did not contain event type {event_type}",
    )


def _eu_basis_contains(
    kind: str,
    criterion: dict[str, Any],
    calls: list[ToolCallResult],
) -> CriterionResult:
    celex = str(criterion["celex"])
    selected = _calls_for_tool(calls, cast(str | None, criterion.get("tool")))
    passed = any(
        isinstance(call.response, dict) and celex in call.response.get("eu_basis", [])
        for call in selected
    )
    return _criterion(
        kind,
        passed,
        f"eu_basis contained {celex}",
        f"eu_basis did not contain {celex}",
    )


CRITERION_HANDLERS: dict[str, CriterionHandler] = {
    "tool_called": _tool_called,
    "response_contains": _response_contains,
    "list_contains_slug": _list_contains_slug,
    "result_count_equals": lambda kind, criterion, calls: _result_count(
        kind,
        criterion,
        calls,
        equals=True,
    ),
    "result_count_at_least": lambda kind, criterion, calls: _result_count(
        kind,
        criterion,
        calls,
        equals=False,
    ),
    "citation_valid": lambda kind, _criterion_data, calls: _citation_valid(
        kind,
        calls,
        expected=True,
    ),
    "citation_invalid": lambda kind, _criterion_data, calls: _citation_valid(
        kind,
        calls,
        expected=False,
    ),
    "tool_error_contains": _tool_error_contains,
    "no_hallucinated_section": _no_hallucinated_section,
    "history_event_count_at_least": _history_event_count,
    "history_contains_type": _history_contains_type,
    "eu_basis_contains": _eu_basis_contains,
}


def _calls_for_tool(
    calls: list[ToolCallResult],
    tool: str | None,
) -> list[ToolCallResult]:
    if tool is None:
        return calls
    return [call for call in calls if call.tool == tool]


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    return json.dumps(response, sort_keys=True, ensure_ascii=False)


def _fixture_checksum(fixture: dict[str, Any]) -> str:
    canonical = json.dumps(fixture, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _render_report(
    *,
    personas: dict[str, dict[str, Any]],
    results: list[ScenarioResult],
    run_date: str,
    fixture_checksum: str,
) -> str:
    lines = [
        f"# lovspor MCP persona eval baseline - {run_date}",
        "",
        "## Run metadata",
        "",
        f"- Date: {run_date}",
        f"- lovspor commit: `{_lovspor_commit()}`",
        f"- lovverk-fixture-checksum: `sha256:{fixture_checksum}`",
        f"- Personas: {len({result.persona for result in results})}",
        f"- Scenarios: {len(results)}",
        "",
        "## Summary",
        "",
        "| Persona | Pass | Partial | Fail | Gap-revealed |",
        "|---|---:|---:|---:|---:|",
    ]
    for persona_id in sorted({result.persona for result in results}):
        counts = Counter(result.status for result in results if result.persona == persona_id)
        name = personas[persona_id]["name"]
        lines.append(
            f"| {name} (`{persona_id}`) | {counts['pass']} | {counts['partial']} | "
            f"{counts['fail']} | {counts['gap-revealed']} |",
        )
    lines.extend(["", "## Per-persona breakdown", ""])
    for persona_id in sorted({result.persona for result in results}):
        persona = personas[persona_id]
        lines.extend(
            [
                f"### {persona['name']} (`{persona_id}`)",
                "",
                "| Scenario | Status | Note |",
                "|---|---|---|",
            ],
        )
        for result in [item for item in results if item.persona == persona_id]:
            lines.append(
                f"| `{result.scenario_id}` | {result.status} | {_escape_table(result.note)} |",
            )
        lines.append("")

    lines.extend(_failure_traces(results))
    lines.extend(_gaps_revealed(results))
    lines.extend(_recommendation(results))
    return "\n".join(lines).rstrip() + "\n"


def _failure_traces(results: list[ScenarioResult]) -> list[str]:
    failed = [result for result in results if result.status in {"fail", "partial"}]
    lines = ["## Failure traces", ""]
    if not failed:
        lines.extend(["_No failures or partial results._", ""])
        return lines
    for result in failed:
        lines.extend(
            [
                f"### {result.scenario_id}",
                "",
                "Scenario YAML:",
                "",
                "```yaml",
                _dump_yaml(result.scenario).rstrip(),
                "```",
                "",
                "Actual tool calls:",
                "",
                "```yaml",
                _dump_yaml([call.as_dict() for call in result.calls]).rstrip(),
                "```",
                "",
                "Diff against expected criteria:",
                "",
                "```diff",
                _criteria_diff(result).rstrip(),
                "```",
                "",
            ],
        )
    return lines


def _gaps_revealed(results: list[ScenarioResult]) -> list[str]:
    gap_results = [result for result in results if result.status == "gap-revealed"]
    grouped: dict[str, list[ScenarioResult]] = defaultdict(list)
    for result in gap_results:
        grouped[cast(str, result.scenario["reveals_gap"])].append(result)
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            -len({result.persona for result in item[1]}),
            -len(item[1]),
            item[0],
        ),
    )
    lines = ["## Gaps revealed", ""]
    if not ranked:
        lines.extend(["_No declared gaps were exercised._", ""])
        return lines
    for idx, (gap, entries) in enumerate(ranked, start=1):
        personas = sorted({entry.persona for entry in entries})
        scenarios = [entry.scenario_id for entry in entries]
        roadmap = cast(str, entries[0].scenario.get("roadmap_class", "unclassified"))
        lines.append(
            f"{idx}. **{gap}** — {len(personas)} personas, {len(entries)} scenarios, "
            f"roadmap Class {roadmap}. Scenarios: {', '.join(f'`{sid}`' for sid in scenarios)}.",
        )
    lines.append("")
    return lines


def _recommendation(results: list[ScenarioResult]) -> list[str]:
    gap_results = [result for result in results if result.status == "gap-revealed"]
    if not gap_results:
        return ["## Recommendation", "", "No roadmap gap dominated this run.", ""]
    grouped: dict[str, list[ScenarioResult]] = defaultdict(list)
    for result in gap_results:
        grouped[cast(str, result.scenario["reveals_gap"])].append(result)
    top_gap, entries = sorted(
        grouped.items(),
        key=lambda item: (
            -len({result.persona for result in item[1]}),
            -len(item[1]),
            item[0],
        ),
    )[0]
    personas = sorted({entry.persona for entry in entries})
    roadmap = cast(str, entries[0].scenario.get("roadmap_class", "unclassified"))
    paragraph = (
        f"The top gap to address next is {top_gap}, because it affected "
        f"{len(personas)} personas in this baseline. "
        f"That points most directly at roadmap Class {roadmap} and would improve both layperson "
        f"and professional workflows. "
        "After that, prioritize the recurring time-machine and alias-resolution gaps because "
        "they turn otherwise precise tools into manual recovery work."
    )
    return ["## Recommendation", "", paragraph, ""]


def _criteria_diff(result: ScenarioResult) -> str:
    expected = [
        {"kind": criterion.get("kind"), "expected": "pass"}
        for criterion in cast(list[dict[str, Any]], result.scenario.get("success_criteria", []))
    ]
    actual = [criterion.as_dict() for criterion in result.criteria]
    return "\n".join(
        difflib.unified_diff(
            _dump_yaml(expected).splitlines(),
            _dump_yaml(actual).splitlines(),
            fromfile="expected",
            tofile="actual",
            lineterm="",
        ),
    )


def _dump_yaml(value: Any) -> str:
    return cast(str, yaml.safe_dump(value, sort_keys=False, allow_unicode=True))


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _lovspor_commit() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--short"],  # noqa: S607
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return f"{head}+dirty" if dirty else head


_LLM_DRIVER_TIMEOUT_SECONDS = 300
"""Per-scenario subprocess timeout for ``claude -p``. Five minutes is
generous — a scenario that needs longer is almost certainly stuck on
an MCP server crash or rate-limit retry storm; better to mark it
failed than block the whole suite indefinitely."""

_LLM_DRIVER_MCP_TOOL_PREFIX = "mcp__lovverk__"
"""When the spawned Claude calls a lovverk MCP tool, the event in the
``stream-json`` output names it ``mcp__lovverk__<tool>`` because that is
how MCP servers expose tools to a model. Strip the prefix so the
captured trace matches the scenario's bare ``tool: <tool>`` field."""

_LLM_DRIVER_PROMPT_TEMPLATE = (
    "You are simulating a Norwegian-law user. Use the lovverk MCP tools to research\n"
    "and answer the question below. Stay grounded in the corpus.\n"
    "\n"
    "User question:\n"
    "{user_query}\n"
    "\n"
    "Tool usage guidance:\n"
    "- Use semantic_search when the user's wording differs from the law's vocabulary.\n"
    "- Use search_laws / search_body for keyword discovery.\n"
    "- Use get_section to read the verbatim text of one section. The response includes\n"
    "  a validated cross_references list — heed it.\n"
    "- Before quoting any verbatim text in your final answer, call verify_quote to\n"
    "  confirm it appears in the cited section.\n"
    "- Use validate_citation if you cite a specific 'section N-M slug' reference.\n"
    "\n"
    "Answer the user concisely after grounding your response."
)


def _ensure_llm_driver_available() -> None:
    """Refuse to start an --llm-driven run when the prerequisites are
    missing. Each prerequisite has a distinct error message so the
    operator sees what to fix rather than an opaque subprocess failure
    halfway through the suite."""
    if shutil.which("claude") is None:
        raise SystemExit(
            "lovspor-eval: --llm-driven requires the `claude` CLI on PATH. "
            "Install Claude Code or fall back to the deterministic runner.",
        )
    repo_pyproject = REPO_ROOT / "pyproject.toml"
    if not repo_pyproject.exists():
        raise SystemExit(
            f"lovspor-eval: cannot locate the lovspor source tree at {REPO_ROOT} "
            "(needed so the spawned MCP server can resolve `uv run lovspor mcp`).",
        )


def _write_llm_mcp_config(workdir: Path, corpus_path: Path) -> Path:
    """Write a fresh ``mcp.json`` pointing the spawned Claude at the
    in-memory synthetic corpus. ``--strict-mcp-config`` then forces
    Claude to use *only* this server, so the eval never accidentally
    hits the operator's production lovverk MCP server registered
    globally in ``~/.claude/settings.json``."""
    config = {
        "mcpServers": {
            "lovverk": {
                "command": "uv",
                "args": [
                    "run",
                    "--project",
                    str(REPO_ROOT),
                    "lovspor",
                    "mcp",
                    "--corpus-path",
                    str(corpus_path),
                ],
            },
        },
    }
    path = workdir / "mcp.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _run_scenario_llm_driven(
    scenario: dict[str, Any],
    mcp_config_path: Path,
    model: str,
) -> ScenarioResult:
    """Spawn ``claude -p`` for one scenario and translate the captured
    ``stream-json`` event stream back into ``ToolCallResult`` objects
    so the existing success-criteria evaluators apply unchanged.

    Sequential by design: 10 scenarios at ~30 s each is a five-minute
    run, and avoiding parallel ``uv run`` startups keeps both the
    subprocess error model and the cost estimate predictable.
    """
    user_query = cast(str, scenario.get("user_query", "")).strip()
    prompt = _LLM_DRIVER_PROMPT_TEMPLATE.format(user_query=user_query)
    cmd = [
        "claude",
        "-p",
        "--output-format=stream-json",
        "--verbose",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model,
    ]
    try:
        completed = subprocess.run(  # noqa: S603
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_LLM_DRIVER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ScenarioResult(
            scenario=scenario,
            calls=[],
            criteria=[],
            status="fail",
            note=f"llm-driven scenario timed out after {_LLM_DRIVER_TIMEOUT_SECONDS}s",
        )
    calls = _parse_llm_driver_stream(completed.stdout)
    # Codex round-1 PR #56 fix: a nonzero claude exit always fails the
    # scenario, even when some tool calls were already captured. The
    # captured trace stays visible for diagnostics, but a partial run
    # that crashed mid-stream is not a pass — the previous gating on
    # ``not calls and returncode != 0`` produced false positives when
    # claude exited 2 after a single tool call.
    if completed.returncode != 0:
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=[],
            status="fail",
            note=f"claude -p exited {completed.returncode}: {completed.stderr.strip()[:200]}",
        )
    criteria = [
        _evaluate_criterion(cast(dict[str, Any], criterion), calls)
        for criterion in cast(list[dict[str, Any]], scenario.get("success_criteria", []))
    ]
    # Mirror the deterministic runner's unexpected-tool-error check.
    # Codex round-1 PR #56 fix: an MCP tool returning ``is_error=true``
    # was previously invisible to the LLM-driven branch — a scenario
    # that only asserted ``tool_called`` (without a matching
    # ``tool_error_contains`` criterion) reported pass even when the
    # tool itself failed. Match the deterministic semantics so both
    # runners surface tool errors the same way.
    expected_error_tools = {
        cast(str, criterion.get("tool"))
        for criterion in cast(list[dict[str, Any]], scenario.get("success_criteria", []))
        if criterion.get("kind") == "tool_error_contains" and isinstance(criterion.get("tool"), str)
    }
    unexpected_errors = [
        call for call in calls if not call.ok and call.tool not in expected_error_tools
    ]
    if scenario.get("expected_outcome") == "gap_revealed":
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="gap-revealed",
            note=f"gap revealed: {scenario.get('reveals_gap')}",
        )
    if unexpected_errors:
        first = unexpected_errors[0]
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="fail",
            note=f"unexpected {first.tool} error: {_response_text(first.response)}",
        )
    failed = [criterion for criterion in criteria if not criterion.passed]
    if not failed:
        return ScenarioResult(
            scenario=scenario,
            calls=calls,
            criteria=criteria,
            status="pass",
            note="all criteria passed",
        )
    status: Status = "partial" if len(failed) < len(criteria) else "fail"
    return ScenarioResult(
        scenario=scenario,
        calls=calls,
        criteria=criteria,
        status=status,
        note=f"{len(failed)} criteria failed",
    )


def _parse_llm_driver_stream(stdout: str) -> list[ToolCallResult]:
    """Walk the ``stream-json`` output one line at a time and pair
    each ``tool_use`` block with the matching ``tool_result`` block
    so the captured trace looks identical in shape to what the
    deterministic runner produces."""
    pending: dict[str, dict[str, Any]] = {}
    results: list[ToolCallResult] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        message = event.get("message") or {}
        content = message.get("content") or []
        if event_type == "assistant":
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                pending[cast(str, block["id"])] = {
                    "name": cast(str, block.get("name", "")),
                    "input": cast(dict[str, Any], block.get("input") or {}),
                }
        elif event_type == "user":
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = cast(str, block.get("tool_use_id", ""))
                spec = pending.pop(tool_use_id, None)
                if spec is None:
                    continue
                tool_name = spec["name"]
                if tool_name.startswith(_LLM_DRIVER_MCP_TOOL_PREFIX):
                    tool_name = tool_name[len(_LLM_DRIVER_MCP_TOOL_PREFIX) :]
                results.append(
                    ToolCallResult(
                        tool=tool_name,
                        args=spec["input"],
                        ok=not block.get("is_error", False),
                        response=_decode_tool_result_payload(block.get("content")),
                    ),
                )
    return results


def _decode_tool_result_payload(content: Any) -> Any:
    """``tool_result.content`` arrives as a list of typed blocks
    (``[{"type": "text", "text": "<json>"}]``). Decode the inner JSON
    when possible so downstream criteria like ``list_contains_slug``
    can pattern-match on dict / list payloads exactly the way they
    do for the deterministic runner."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = cast(str, block.get("text", ""))
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


if __name__ == "__main__":
    main()
