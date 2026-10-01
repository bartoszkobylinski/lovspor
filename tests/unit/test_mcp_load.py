"""Tests for ``scripts/load/mcp_load.py`` (#480).

The load run itself needs a live ``lovspor mcp-http`` and is run by hand. What
is pinned here is everything a wrong report would come from: which refusal a
tool error is counted as, the percentile arithmetic, the per-level summary,
and the workflow each simulated client replays — above all that the paid tool
never runs unless asked for.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "load" / "mcp_load.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_load", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load = _load_script()


# --- classify: the exact texts quota.py raises, as FastMCP wraps them --------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Error executing tool get_section: this server is at capacity "
            "(4 calls in flight across all users)",
            "capacity",
        ),
        ("Error executing tool x: 4 calls already in flight for this credential", "in_flight"),
        ("Error executing tool x: rate limit of 120/min exceeded", "rate"),
        ("Error executing tool x: daily quota of 5000 calls is exhausted", "quota"),
        (
            "Error executing tool x: this server has reached its daily ceiling of 20000 "
            "calls across all users",
            "quota",
        ),
        (
            "Error executing tool x: daily limit of 500 semantic searches is exhausted; "
            "the other fifteen tools are unaffected",
            "quota",
        ),
        ("Error executing tool x: unknown credential beta-001", "error"),
        ("Error executing tool get_section: no section 99 in slug", "error"),
    ],
)
def test_classify_tool_error_texts(text: str, expected: str) -> None:
    assert load.classify(True, text) == expected


def test_classify_success_is_ok_even_if_text_mentions_capacity() -> None:
    assert load.classify(False, "the law talks about being at capacity") == "ok"


# --- percentile ---------------------------------------------------------------


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert load.percentile(values, 50) == 50.0
    assert load.percentile(values, 95) == 95.0
    assert load.percentile(values, 99) == 99.0
    assert load.percentile(values, 100) == 100.0


def test_percentile_unsorted_and_small() -> None:
    assert load.percentile([3.0, 1.0, 2.0], 50) == 2.0
    assert load.percentile([3.0, 1.0, 2.0], 99) == 3.0
    assert load.percentile([7.0], 1) == 7.0


def test_percentile_empty_is_none() -> None:
    assert load.percentile([], 50) is None


@pytest.mark.parametrize("pct", [0, -1, 101])
def test_percentile_rejects_out_of_range(pct: float) -> None:
    with pytest.raises(load.LoadTestError):
        load.percentile([1.0], pct)


# --- summarize ------------------------------------------------------------------


def _sample(outcome: str, ms: float, tool: str = "get_section") -> object:
    return load.Sample(tool=tool, outcome=outcome, seconds=ms / 1000)


def test_summarize_counts_every_bucket() -> None:
    samples = [
        _sample("ok", 10),
        _sample("ok", 20),
        _sample("ok", 30),
        _sample("capacity", 1),
        _sample("in_flight", 1),
        _sample("in_flight", 1),
        _sample("rate", 1),
        _sample("quota", 1),
        _sample("error", 5),
    ]
    report = load.summarize(4, samples, elapsed=2.0)
    assert report.concurrency == 4
    assert report.calls == 9
    assert report.ok == 3
    assert report.refused == {"capacity": 1, "in_flight": 2, "rate": 1, "quota": 1}
    assert report.errors == 1
    assert report.calls_per_s == 4.5
    assert report.ok_per_s == 1.5


def test_summarize_latency_is_over_ok_calls_only() -> None:
    samples = [_sample("ok", 100), _sample("ok", 200), _sample("capacity", 1)]
    report = load.summarize(2, samples, elapsed=1.0)
    assert report.p50_ms == 100.0
    assert report.p95_ms == 200.0
    assert report.p99_ms == 200.0


def test_summarize_no_ok_calls_has_no_latency() -> None:
    report = load.summarize(1, [_sample("error", 3)], elapsed=1.0)
    assert report.p50_ms is None
    assert report.ok_per_s == 0.0


def test_summarize_zero_elapsed_does_not_divide_by_zero() -> None:
    report = load.summarize(1, [], elapsed=0.0)
    assert report.calls == 0
    assert report.calls_per_s == 0.0


def test_summarize_keeps_a_few_distinct_error_examples() -> None:
    samples = [
        load.Sample(tool="t", outcome="error", seconds=0.1, detail="boom"),
        load.Sample(tool="t", outcome="error", seconds=0.1, detail="boom"),
        load.Sample(tool="t", outcome="error", seconds=0.1, detail="bang"),
    ]
    report = load.summarize(1, samples, elapsed=1.0)
    assert report.error_examples == ["boom", "bang"]


def test_summarize_per_tool_counts() -> None:
    samples = [_sample("ok", 1, "search_laws"), _sample("rate", 1, "search_laws")]
    report = load.summarize(1, samples, elapsed=1.0)
    assert report.per_tool == {"search_laws": {"ok": 1, "rate": 1}}


# --- rendering -------------------------------------------------------------------


def test_render_table_has_one_row_per_level() -> None:
    reports = [
        load.summarize(1, [_sample("ok", 10)], elapsed=1.0),
        load.summarize(2, [_sample("ok", 10), _sample("capacity", 1)], elapsed=1.0),
    ]
    table = load.render_table(reports)
    lines = table.splitlines()
    assert lines[0].startswith("| clients |")
    assert len(lines) == 4
    assert lines[3].startswith("| 2 |")
    assert "| 1 |" in lines[3]


def test_render_table_prints_dash_for_missing_latency() -> None:
    table = load.render_table([load.summarize(1, [_sample("error", 1)], elapsed=1.0)])
    assert "| - |" in table


def test_report_json_round_trips() -> None:
    report = load.summarize(1, [_sample("ok", 10)], elapsed=1.0)
    document = load.render_json([report], {"url": "http://127.0.0.1:1/mcp"})
    parsed = json.loads(document)
    assert parsed["run"] == {"url": "http://127.0.0.1:1/mcp"}
    assert parsed["levels"][0]["ok"] == 1


# --- argument parsing --------------------------------------------------------------


def test_parse_levels() -> None:
    assert load.parse_levels("1,2,4,8,16") == [1, 2, 4, 8, 16]
    assert load.parse_levels(" 3 ") == [3]


@pytest.mark.parametrize("text", ["", "0", "1,-2", "a", "1,,2"])
def test_parse_levels_rejects(text: str) -> None:
    with pytest.raises(load.LoadTestError):
        load.parse_levels(text)


def test_parse_tokens_skips_blanks_and_comments() -> None:
    assert load.parse_tokens("lsp_a\n\n# note\n  lsp_b  \n") == ["lsp_a", "lsp_b"]


def test_parse_tokens_rejects_empty() -> None:
    with pytest.raises(load.LoadTestError):
        load.parse_tokens("\n# only a comment\n")


def test_resolve_tokens_prefers_file(tmp_path: Path) -> None:
    path = tmp_path / "tok"
    path.write_text("lsp_file\n")
    assert load.resolve_tokens(path, {"LOVSPOR_LOAD_TOKEN": "lsp_env"}) == ["lsp_file"]


def test_resolve_tokens_from_env() -> None:
    assert load.resolve_tokens(None, {"LOVSPOR_LOAD_TOKEN": "lsp_env"}) == ["lsp_env"]


def test_resolve_tokens_missing_names_both_sources() -> None:
    with pytest.raises(load.LoadTestError, match="LOVSPOR_LOAD_TOKEN"):
        load.resolve_tokens(None, {})


def test_resolve_tokens_error_never_echoes_file_content(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    with pytest.raises(load.LoadTestError) as info:
        load.resolve_tokens(path, {})
    assert str(path) in str(info.value)


def test_parse_args_rejects_duration_and_questions_together() -> None:
    with pytest.raises(SystemExit):
        load.parse_args(["--url", "http://127.0.0.1:1/mcp", "--duration", "5", "--questions", "2"])


def test_parse_args_has_no_token_option() -> None:
    with pytest.raises(SystemExit):
        load.parse_args(["--url", "http://127.0.0.1:1/mcp", "--token", "lsp_x"])


def test_parse_args_defaults() -> None:
    args = load.parse_args(["--url", "http://127.0.0.1:1/mcp"])
    assert args.concurrency == [1, 2, 4, 8, 16]
    assert args.include_paid is False
    assert args.questions == 3
    assert args.duration is None


# --- the workflow each client replays ------------------------------------------------


def test_lookup_calls_default_excludes_semantic_search() -> None:
    scenario = load.SCENARIOS[0]
    calls = load.lookup_calls(scenario, include_paid=False)
    tools = [tool for tool, _ in calls]
    assert "semantic_search" not in tools
    assert tools[0] == "search_laws"
    assert tools[1:] == ["get_section"] * len(scenario.sections)


def test_lookup_calls_paid_opt_in_prepends_semantic_search() -> None:
    calls = load.lookup_calls(load.SCENARIOS[0], include_paid=True)
    assert calls[0][0] == "semantic_search"
    assert sum(tool == "semantic_search" for tool, _ in calls) == 1


def test_lookup_calls_arguments() -> None:
    scenario = load.SCENARIOS[0]
    calls = load.lookup_calls(scenario, include_paid=False)
    assert calls[0][1] == {"query": scenario.query, "limit": 5}
    assert calls[1][1] == {"slug": scenario.slug, "section_id": scenario.sections[0]}


def test_check_calls_verify_then_validate() -> None:
    scenario = load.SCENARIOS[0]
    calls = load.check_calls(scenario, "some quote")
    assert calls == [
        (
            "verify_quote",
            {"slug": scenario.slug, "section_id": scenario.sections[0], "quote": "some quote"},
        ),
        ("validate_citation", {"citation": f"§ {scenario.sections[0]} {scenario.slug}"}),
    ]


def test_scenario_for_spreads_clients_across_the_list() -> None:
    first = load.scenario_for(0, 0)
    assert load.scenario_for(1, 0) != first
    assert load.scenario_for(0, 1) != first
    assert load.scenario_for(0, len(load.SCENARIOS)) == first


def test_every_scenario_has_sections() -> None:
    assert len(load.SCENARIOS) >= 4
    assert all(s.sections for s in load.SCENARIOS)


def test_quote_from_takes_leading_words_of_plain_text() -> None:
    body = "**(1)** Loven gjelder  avtaler om\nbruk av husrom mot vederlag, og mer tekst."
    assert load.quote_from(body, words=6) == "Loven gjelder avtaler om bruk av"


def test_quote_from_empty_body_falls_back() -> None:
    assert load.quote_from("", words=6) == load.FALLBACK_QUOTE


def test_body_of_reads_structured_or_text() -> None:
    assert load.body_of({"body": "tekst"}, "") == "tekst"
    assert load.body_of(None, json.dumps({"body": "fra tekst"})) == "fra tekst"
    assert load.body_of(None, "not json") == ""
    assert load.body_of({"result": []}, "") == ""
