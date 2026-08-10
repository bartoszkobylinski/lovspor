"""Claude Code CLI driver: argv per condition, stream-json parsing, records."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.llhb.claude_cli import (
    CaseTiming,
    RunIdentity,
    ToolAccess,
    build_argv,
    build_result_record,
    parse_stream_json,
)
from lovspor.llhb.schema import load_schema, validate_case

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

IDENTITY = RunIdentity(
    run_id="llhb-v1-run-20260808-pilot1",
    provider="anthropic",
    model_id="claude-opus-5",
    condition="control",
)
TREATMENT = IDENTITY.model_copy(update={"condition": "lovspor"})
ACCESS = ToolAccess(
    mcp_config_json='{"mcpServers":{"lovverk":{"command":"/bin/lovspor"}}}',
    allowed_tools=("mcp__lovverk__get_section", "mcp__lovverk__search_laws"),
)
TIMING = CaseTiming(started_at="2026-08-08T12:00:01Z", total_ms=1234)


def init_event(tools: list[str] | None = None, **overrides: object) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "tools": tools if tools is not None else [],
        "mcp_servers": [],
        "model": "claude-opus-5",
    }
    event.update(overrides)
    return event


def result_event(**overrides: object) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Svar fra modellen.",
        "num_turns": 1,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "permission_denials": [],
    }
    event.update(overrides)
    return event


def tool_use(use_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": use_id, "name": name, **{"input": arguments}}]
        },
    }


def tool_result(use_id: str, content: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


def stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def control_stream(**result_overrides: object) -> str:
    return stream(init_event(), result_event(**result_overrides))


class TestBuildArgv:
    def test_control_disables_tools_and_mcp(self) -> None:
        argv = build_argv(IDENTITY, "Hva sier loven?", "SYSTEM")

        assert argv[:2] == ["claude", "-p"]
        assert "Hva sier loven?" in argv
        assert argv[argv.index("--model") + 1] == "claude-opus-5"
        assert argv[argv.index("--system-prompt") + 1] == "SYSTEM"
        assert argv[argv.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert "--allowedTools" not in argv

    def test_both_conditions_stream_the_same_way(self) -> None:
        """Same output format in both arms: ruling #25 allows exactly one
        difference between them, and it is not the transcript format."""
        control = build_argv(IDENTITY, "Hva sier loven?", "SYSTEM")
        treatment = build_argv(TREATMENT, "Hva sier loven?", "SYSTEM", ACCESS)

        for argv in (control, treatment):
            assert argv[argv.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in argv
        assert (
            control[: control.index("--mcp-config")] == treatment[: treatment.index("--mcp-config")]
        )

    def test_treatment_carries_the_config_and_allowed_tools_last(self) -> None:
        argv = build_argv(TREATMENT, "Hva sier loven?", "SYSTEM", ACCESS)

        assert argv[argv.index("--mcp-config") + 1] == ACCESS.mcp_config_json
        assert argv[argv.index("--allowedTools") + 1 :] == list(ACCESS.allowed_tools)
        assert argv.index("--allowedTools") == len(argv) - 1 - len(ACCESS.allowed_tools)

    def test_rejects_control_with_tool_access(self) -> None:
        with pytest.raises(ValueError, match="control argv"):
            build_argv(IDENTITY, "Hva sier loven?", "SYSTEM", ACCESS)

    def test_rejects_treatment_without_tool_access(self) -> None:
        with pytest.raises(ValueError, match="lovspor argv"):
            build_argv(TREATMENT, "Hva sier loven?", "SYSTEM")

    def test_rejects_unknown_condition(self) -> None:
        with pytest.raises(ValueError, match="unknown condition"):
            build_argv(IDENTITY.model_copy(update={"condition": "pilot"}), "Q", "SYSTEM")


class TestToolAccess:
    def test_cannot_be_edited_after_it_is_built(self) -> None:
        """What the CLI is told and what the metadata records come from the
        same object; a caller must not be able to change one afterwards."""
        with pytest.raises(ValidationError):
            ACCESS.allowed_tools = ()


class TestParseStreamJson:
    def test_parses_a_toolless_success(self) -> None:
        parsed = parse_stream_json(control_stream(), returncode=0)

        assert parsed.ok is True
        assert parsed.final_answer == "Svar fra modellen."
        assert parsed.turns == 1
        assert parsed.usage == {"input_tokens": 100, "output_tokens": 50}
        assert parsed.tool_calls == []
        assert parsed.truncated is False
        assert parsed.error is None

    def test_records_the_offered_tool_surface(self) -> None:
        offered = ["mcp__lovverk__get_section"]

        parsed = parse_stream_json(stream(init_event(offered), result_event()), returncode=0)

        assert parsed.harness is not None
        assert parsed.harness.exposed_tools == tuple(offered)

    def test_control_surface_is_provably_empty(self) -> None:
        parsed = parse_stream_json(control_stream(), returncode=0)

        assert parsed.harness is not None
        assert parsed.harness.exposed_tools == ()

    def test_captures_tool_calls_in_order_with_their_payloads(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven", "section_id": "1"}),
            tool_result("tu-1", "§ 1. Formål"),
            tool_use("tu-2", "mcp__lovverk__search_laws", {"query": "testlov"}),
            tool_result("tu-2", "ingen treff", is_error=True),
            result_event(num_turns=4),
        )

        calls = parse_stream_json(transcript, returncode=0).tool_calls

        assert [call.index for call in calls] == [0, 1]
        assert [call.name for call in calls] == [
            "mcp__lovverk__get_section",
            "mcp__lovverk__search_laws",
        ]
        assert calls[0].arguments == {"slug": "testloven", "section_id": "1"}
        assert calls[0].result == "§ 1. Formål"
        assert calls[0].is_error is False
        assert calls[1].is_error is True

    def test_matches_results_by_id_not_by_position(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_use("tu-2", "mcp__lovverk__get_section", {"slug": "b"}),
            tool_result("tu-2", "svar-b"),
            tool_result("tu-1", "svar-a"),
            result_event(),
        )

        calls = parse_stream_json(transcript, returncode=0).tool_calls

        assert [call.result for call in calls] == ["svar-a", "svar-b"]

    def test_unanswered_tool_call_keeps_a_null_payload(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            result_event(),
        )

        calls = parse_stream_json(transcript, returncode=0).tool_calls

        assert calls[0].result is None
        assert calls[0].is_error is False

    def test_keeps_the_tool_trace_of_a_failed_case(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result("tu-1", "svar"),
            result_event(subtype="error_during_execution", is_error=True),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert len(parsed.tool_calls) == 1

    def test_records_permission_denials(self) -> None:
        denial = {"tool_name": "mcp__lovverk__get_law", "tool_use_id": "tu-9"}

        parsed = parse_stream_json(
            stream(init_event(), result_event(permission_denials=[denial])), returncode=0
        )

        assert parsed.harness is not None
        assert parsed.harness.permission_denials == (denial,)

    def test_records_mcp_server_status(self) -> None:
        servers = [{"name": "lovverk", "status": "connected"}]

        parsed = parse_stream_json(
            stream(init_event(mcp_servers=servers), result_event()), returncode=0
        )

        assert parsed.harness is not None
        assert parsed.harness.mcp_servers == tuple(servers)

    def test_flags_truncation_on_max_turns(self) -> None:
        parsed = parse_stream_json(
            stream(init_event(), result_event(subtype="error_max_turns", is_error=True)),
            returncode=0,
        )

        assert parsed.truncated is True
        assert parsed.ok is False

    def test_flags_truncation_on_max_tokens(self) -> None:
        assert parse_stream_json(control_stream(stop_reason="max_tokens"), returncode=0).truncated

    def test_flags_nonzero_returncode(self) -> None:
        parsed = parse_stream_json("boom", returncode=1)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "exit code 1" in parsed.error

    def test_a_nonzero_exit_does_not_discard_the_tool_trace(self) -> None:
        """A case that crashed after calling a tool still called it. Ruling
        #25 turns on whether a run touched a tool at all, so evidence of a
        call must not depend on how the process happened to end."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven"}),
            tool_result("tu-1", "§ 1. Formål"),
            result_event(num_turns=2),
        )

        parsed = parse_stream_json(transcript, returncode=1)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "exit code 1" in parsed.error
        assert [call.name for call in parsed.tool_calls] == ["mcp__lovverk__get_section"]
        assert parsed.harness is not None
        assert parsed.harness.exposed_tools == ("mcp__lovverk__get_section",)

    def test_flags_an_assistant_event_without_a_message(self) -> None:
        """Skipping a malformed event silently is how a trace ends up
        shorter than the conversation it describes."""
        parsed = parse_stream_json(
            stream(init_event(), {"type": "assistant"}, result_event()), returncode=0
        )

        assert parsed.ok is False
        assert parsed.error is not None
        assert "readable message" in parsed.error

    def test_tolerates_blank_lines_between_events(self) -> None:
        parsed = parse_stream_json(control_stream().replace("\n", "\n\n"), returncode=0)

        assert parsed.ok is True

    def test_flags_an_unparseable_line(self) -> None:
        parsed = parse_stream_json(control_stream() + "not json\n", returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "line 3" in parsed.error

    def test_flags_a_non_object_line(self) -> None:
        parsed = parse_stream_json('["a", "list"]\n', returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "JSON object" in parsed.error

    def test_never_raises_on_huge_integer_payload(self) -> None:
        parsed = parse_stream_json('{"result": ' + "9" * 5000 + "}", returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None

    def test_flags_a_transcript_without_a_result_event(self) -> None:
        parsed = parse_stream_json(stream(init_event()), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "result event" in parsed.error

    def test_a_transcript_that_stops_early_keeps_the_calls_it_showed(self) -> None:
        """A process killed mid-run leaves a valid prefix, and the calls in
        that prefix were really made."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven"}),
            tool_result("tu-1", "§ 1. Formål"),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert [call.name for call in parsed.tool_calls] == ["mcp__lovverk__get_section"]
        assert parsed.harness is not None
        assert parsed.harness.exposed_tools == ("mcp__lovverk__get_section",)

    def test_a_truncated_final_line_keeps_the_calls_before_it(self) -> None:
        transcript = (
            stream(
                init_event(["mcp__lovverk__get_section"]),
                tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven"}),
                tool_result("tu-1", "§ 1. Formål"),
            )
            + '{"type":"resu'
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "line 4" in parsed.error
        assert [call.name for call in parsed.tool_calls] == ["mcp__lovverk__get_section"]

    def test_flags_a_transcript_without_an_init_event(self) -> None:
        """No init event means no evidence of what was offered, and an
        absent tool list must never read as a proven empty one."""
        parsed = parse_stream_json(stream(result_event()), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "init event" in parsed.error

    def test_flags_a_malformed_tool_use_block(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu-1"}]}},
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "tool_use block 0" in parsed.error

    def test_flags_a_tool_use_without_an_id(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "mcp__lovverk__get_law", "input": {}}]
                },
            },
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "no id" in parsed.error

    def test_flags_a_tool_result_with_no_matching_call(self) -> None:
        """An orphan result is evidence of a call whose tool_use block we
        did not read. Reporting zero calls for it would understate tool
        use, which is the one thing this parser must never do."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_result("ghost", "et svar"),
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "ghost" in parsed.error

    def test_flags_a_second_init_event(self) -> None:
        """Two init events describe two tool environments; taking the
        first would let a run that gained MCP mid-transcript be recorded
        as toolless."""
        transcript = stream(
            init_event([]),
            init_event(["mcp__lovverk__get_section"]),
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "init event" in parsed.error

    def test_flags_two_results_sharing_one_id(self) -> None:
        """Last-write-wins would drop a payload and keep the wrong one,
        with nothing in the record saying which call it belonged to."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("dup", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result("dup", "første"),
            tool_result("dup", "andre"),
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "dup" in parsed.error

    def test_flags_two_calls_sharing_one_id(self) -> None:
        """Whichever call is matched first takes the payload and the other
        is recorded as unanswered - an arbitrary split of real evidence."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("dup", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_use("dup", "mcp__lovverk__get_section", {"slug": "b"}),
            tool_result("dup", "svar"),
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "dup" in parsed.error

    def test_flags_a_call_with_a_name_but_no_readable_input(self) -> None:
        """Both halves of the guard matter on their own: a block naming a
        tool but carrying no argument object is still unreadable."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-1",
                            "name": "mcp__lovverk__get_section",
                            "input": "slug=testloven",
                        }
                    ]
                },
            },
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "no readable name or input" in parsed.error

    def test_a_tool_result_block_in_an_assistant_event_is_not_a_call(self) -> None:
        """Block scanning is filtered on event type as well as block type;
        loosening either half would let one arm's blocks read as the
        other's."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "svar"}]
                },
            },
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is True
        assert parsed.tool_calls == []

    def test_flags_a_tool_result_without_an_id(self) -> None:
        transcript = stream(
            init_event(),
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "tool_use_id" in parsed.error

    def test_flags_a_non_list_tool_surface(self) -> None:
        parsed = parse_stream_json(
            stream(init_event(**{"tools": "alle"}), result_event()), returncode=0
        )

        assert parsed.ok is False
        assert parsed.error is not None
        assert "tools is str" in parsed.error

    def test_ignores_text_and_thinking_blocks(self) -> None:
        transcript = stream(
            init_event(),
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "…"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Svar"}]}},
            {"type": "system", "subtype": "thinking_tokens", "count": 12},
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is True
        assert parsed.tool_calls == []

    def test_flags_error_subtype(self) -> None:
        parsed = parse_stream_json(
            control_stream(subtype="error_max_turns", is_error=True), returncode=0
        )

        assert parsed.ok is False
        assert parsed.error is not None
        assert "error_max_turns" in parsed.error

    def test_flags_is_error_even_with_success_subtype(self) -> None:
        parsed = parse_stream_json(control_stream(is_error=True), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None

    def test_flags_non_string_result(self) -> None:
        parsed = parse_stream_json(control_stream(result=42), returncode=0)

        assert parsed.ok is False
        assert parsed.error is not None
        assert "result" in parsed.error

    def test_normalizes_invalid_turns_to_none(self) -> None:
        assert parse_stream_json(control_stream(num_turns=0), returncode=0).turns is None
        assert parse_stream_json(control_stream(num_turns="mange"), returncode=0).turns is None


class TestBuildResultRecord:
    def test_control_record_is_schema_valid(self) -> None:
        parsed = parse_stream_json(control_stream(), returncode=0)

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        schema = load_schema(SCHEMA_DIR / "result_record.schema.json")
        assert validate_case(record, schema) == []
        assert record["final_answer"] == "Svar fra modellen."
        assert record["tool_calls"] == []
        assert record["harness"] == {
            "exposed_tools": [],
            "mcp_servers": [],
            "permission_denials": [],
        }
        assert record["completed"] is True
        assert record["errors"] == []
        assert record["turns"] == 1

    def test_treatment_record_is_schema_valid(self) -> None:
        transcript = stream(
            init_event(
                ["mcp__lovverk__get_section"],
                mcp_servers=[{"name": "lovverk", "status": "connected"}],
            ),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven", "section_id": "1"}),
            tool_result("tu-1", "§ 1. Formål"),
            result_event(num_turns=3),
        )
        parsed = parse_stream_json(transcript, returncode=0)

        record = build_result_record(TREATMENT, "llhb-v1-C1-001", parsed, TIMING)

        schema = load_schema(SCHEMA_DIR / "result_record.schema.json")
        assert validate_case(record, schema) == []
        # No payload in the record: it is regenerable from the pin, and the
        # orchestrator adds result_ref and result_sha256 when it stores the
        # bytes beside the run (ruling #27).
        assert record["tool_calls"] == [
            {
                "index": 0,
                "name": "mcp__lovverk__get_section",
                "arguments": {"slug": "testloven", "section_id": "1"},
                "is_error": False,
            }
        ]
        assert record["harness"] == {
            "exposed_tools": ["mcp__lovverk__get_section"],
            "mcp_servers": [{"name": "lovverk", "status": "connected"}],
            "permission_denials": [],
        }

    def test_error_record_is_schema_valid_and_incomplete(self) -> None:
        parsed = parse_stream_json("boom", returncode=1)

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        schema = load_schema(SCHEMA_DIR / "result_record.schema.json")
        assert validate_case(record, schema) == []
        assert record["final_answer"] is None
        assert record["harness"] is None
        assert record["completed"] is False
        # A case that never ran is not a case that hit a cap: recording it
        # as truncated would put a cap hit in the results that never
        # happened (METHODOLOGY §5 records cap hits, never invents them).
        assert record["truncated"] is False
        assert record["errors"][0]["stage"] == "request"
        assert "exit code 1" in record["errors"][0]["message"]


class TestPartialTrace:
    def test_a_malformed_event_after_a_good_call_keeps_the_good_call(self) -> None:
        """The call before the damage was still made; dropping it reports
        less tool use than the transcript contains."""
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven"}),
            tool_result("tu-1", "§ 1. Formål"),
            {"type": "assistant"},
            result_event(num_turns=2),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert [call.name for call in parsed.tool_calls] == ["mcp__lovverk__get_section"]

    def test_unreadable_results_still_leave_the_calls_visible(self) -> None:
        transcript = stream(
            init_event(["mcp__lovverk__get_section"]),
            tool_use("tu-1", "mcp__lovverk__get_section", {"slug": "testloven"}),
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            result_event(),
        )

        parsed = parse_stream_json(transcript, returncode=0)

        assert parsed.ok is False
        assert [call.name for call in parsed.tool_calls] == ["mcp__lovverk__get_section"]
        assert parsed.tool_calls[0].result is None
