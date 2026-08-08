"""Stage 5 control-arm driver for Claude Code CLI: argv, parsing, records."""

import json
from pathlib import Path

import pytest

from lovspor.llhb.claude_cli import (
    CaseTiming,
    RunIdentity,
    build_control_argv,
    build_result_record,
    parse_cli_output,
)
from lovspor.llhb.schema import load_schema, validate_case

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

IDENTITY = RunIdentity(
    run_id="llhb-v1-run-20260808-pilot1",
    provider="anthropic",
    model_id="claude-opus-5",
    condition="control",
)

TIMING = CaseTiming(started_at="2026-08-08T12:00:01Z", total_ms=1234)


def success_stdout(**overrides: object) -> str:
    payload: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Svar fra modellen.",
        "num_turns": 1,
        "duration_ms": 900,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "session_id": "abc123",
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestBuildControlArgv:
    def test_disables_tools_and_mcp(self) -> None:
        argv = build_control_argv(IDENTITY, "Hva sier loven?", "SYSTEM")

        assert argv[:2] == ["claude", "-p"]
        assert "Hva sier loven?" in argv
        assert argv[argv.index("--model") + 1] == "claude-opus-5"
        assert argv[argv.index("--system-prompt") + 1] == "SYSTEM"
        assert argv[argv.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert argv[argv.index("--output-format") + 1] == "json"

    def test_rejects_non_control_identity(self) -> None:
        lovspor_arm = IDENTITY.model_copy(update={"condition": "lovspor"})

        with pytest.raises(ValueError, match="control"):
            build_control_argv(lovspor_arm, "Hva sier loven?", "SYSTEM")


class TestParseCliOutput:
    def test_parses_success(self) -> None:
        parsed = parse_cli_output(success_stdout(), returncode=0)

        assert parsed.ok is True
        assert parsed.final_answer == "Svar fra modellen."
        assert parsed.turns == 1
        assert parsed.usage == {"input_tokens": 100, "output_tokens": 50}
        assert parsed.error is None

    def test_flags_error_subtype(self) -> None:
        parsed = parse_cli_output(
            success_stdout(subtype="error_max_turns", is_error=True), returncode=0
        )

        assert parsed.ok is False
        assert parsed.error is not None
        assert "error_max_turns" in parsed.error

    def test_flags_nonzero_returncode(self) -> None:
        parsed = parse_cli_output("boom", returncode=1)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "exit code 1" in parsed.error

    def test_flags_non_json_stdout(self) -> None:
        parsed = parse_cli_output("not json at all", returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "JSON" in parsed.error

    def test_never_raises_on_huge_integer_payload(self) -> None:
        parsed = parse_cli_output('{"result": ' + "9" * 5000 + "}", returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None

    def test_flags_is_error_even_with_success_subtype(self) -> None:
        parsed = parse_cli_output(success_stdout(is_error=True), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None

    def test_flags_non_string_result(self) -> None:
        parsed = parse_cli_output(success_stdout(result=42), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "result" in parsed.error

    def test_normalizes_invalid_turns_to_none(self) -> None:
        assert parse_cli_output(success_stdout(num_turns=0), returncode=0).turns is None
        assert parse_cli_output(success_stdout(num_turns="mange"), returncode=0).turns is None

    def test_flags_non_object_json(self) -> None:
        parsed = parse_cli_output('["a", "list"]', returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "object" in parsed.error


class TestBuildResultRecord:
    def test_success_record_is_schema_valid(self) -> None:
        parsed = parse_cli_output(success_stdout(), returncode=0)

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        schema = load_schema(SCHEMA_DIR / "result_record.schema.json")
        assert validate_case(record, schema) == []
        assert record["final_answer"] == "Svar fra modellen."
        assert record["tool_calls"] == []
        assert record["completed"] is True
        assert record["errors"] == []
        assert record["turns"] == 1

    def test_error_record_is_schema_valid_and_incomplete(self) -> None:
        parsed = parse_cli_output("boom", returncode=1)

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        schema = load_schema(SCHEMA_DIR / "result_record.schema.json")
        assert validate_case(record, schema) == []
        assert record["final_answer"] is None
        assert record["completed"] is False
        assert record["errors"][0]["stage"] == "request"
        assert "exit code 1" in record["errors"][0]["message"]
