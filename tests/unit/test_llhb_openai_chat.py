"""OpenAI-compatible chat driver: request shape, thinking-block stripping, records."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.llhb.claude_cli import CaseTiming, RunIdentity, build_result_record
from lovspor.llhb.openai_chat import (
    ChatEndpoint,
    ChatExchange,
    ChatSampling,
    build_request,
    parse_chat_completion,
    post_chat,
    strip_thinking,
)
from lovspor.llhb.schema import load_schema, validate_case

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

IDENTITY = RunIdentity(
    run_id="llhb-v1-run-20260825-nmctrl1",
    provider="norallm",
    model_id="NorMistral-11b-thinking:latest",
    condition="control",
)
TIMING = CaseTiming(started_at="2026-08-25T12:00:01Z", total_ms=6738)
ENDPOINT = ChatEndpoint(base_url="https://chat.example/api/", api_key="sk-test", timeout_s=30)
URL = "https://chat.example/api/chat/completions"


def body(content: Any, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "model": "NorMistral-11b-thinking:latest",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 581, "completion_tokens": 224, "total_tokens": 805},
    }
    document.update(overrides)
    return document


def exchange(document: Any, status: int = 200) -> ChatExchange:
    return ChatExchange(status=status, body=json.dumps(document), duration_ms=10)


class TestBuildRequest:
    def test_carries_system_and_user_messages_and_no_tools(self) -> None:
        request = build_request(IDENTITY, "Hva sier loven?", "SYSTEM", ChatSampling())

        assert request["messages"] == [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "Hva sier loven?"},
        ]
        assert request["model"] == IDENTITY.model_id
        assert request["temperature"] == 0.0
        assert request["stream"] is False
        assert "tools" not in request

    def test_max_output_tokens_is_only_sent_when_set(self) -> None:
        plain = build_request(IDENTITY, "q", "s", ChatSampling())
        capped = build_request(IDENTITY, "q", "s", ChatSampling(max_output_tokens=2048))

        assert "max_tokens" not in plain
        assert capped["max_tokens"] == 2048

    def test_refuses_the_treatment_condition(self) -> None:
        treatment = IDENTITY.model_copy(update={"condition": "lovspor"})

        with pytest.raises(ValueError, match="control arm only"):
            build_request(treatment, "q", "s", ChatSampling())


class TestStripThinking:
    def test_drops_a_closed_block_and_surrounding_whitespace(self) -> None:
        assert strip_thinking("<think>resonnement</think>\n\nOslo.") == "Oslo."

    def test_drops_every_block(self) -> None:
        assert strip_thinking("<think>a</think>Ja.<think>b</think> Nei.") == "Ja.Nei."

    def test_drops_an_unclosed_block_to_the_end(self) -> None:
        assert strip_thinking("Svar.<think>aldri lukket") == "Svar."

    def test_leaves_plain_text_alone(self) -> None:
        assert strip_thinking("  Bare et svar.  ") == "Bare et svar."


class TestParseChatCompletion:
    def test_success_strips_thinking_and_records_usage(self) -> None:
        parsed = parse_chat_completion(exchange(body("<think>tenker</think>Oslo.")))

        assert parsed.ok is True
        assert parsed.final_answer == "Oslo."
        assert parsed.turns == 1
        assert parsed.usage == {"prompt_tokens": 581, "completion_tokens": 224, "total_tokens": 805}
        assert parsed.tool_calls == []
        assert parsed.truncated is False
        assert parsed.harness is not None
        assert parsed.harness.exposed_tools == ()

    def test_success_builds_a_schema_valid_record(self) -> None:
        parsed = parse_chat_completion(exchange(body("Oslo.")))

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        validate_case(record, load_schema(SCHEMA_DIR / "result_record.schema.json"))
        assert record["harness"] == {
            "exposed_tools": [],
            "mcp_servers": [],
            "permission_denials": [],
        }
        assert record["completed"] is True

    def test_content_parts_are_joined(self) -> None:
        parts = [{"type": "text", "text": "Os"}, {"type": "text", "text": "lo."}]

        parsed = parse_chat_completion(exchange(body(parts)))

        assert parsed.final_answer == "Oslo."

    def test_length_finish_reason_marks_truncation(self) -> None:
        document = body("Delvis")
        document["choices"][0]["finish_reason"] = "length"

        assert parse_chat_completion(exchange(document)).truncated is True

    @pytest.mark.parametrize(
        ("document", "fragment"),
        [
            ({"choices": []}, "no choices"),
            ({"detail": "boom"}, "choices"),
            ([1, 2], "not a JSON object"),
            (body(42), "content is int"),
        ],
    )
    def test_unreadable_bodies_fail_closed(self, document: Any, fragment: str) -> None:
        parsed = parse_chat_completion(exchange(document))

        assert parsed.ok is False
        assert parsed.error is not None
        assert "unreadable chat response" in parsed.error
        assert fragment in parsed.error

    def test_malformed_json_fails_closed(self) -> None:
        parsed = parse_chat_completion(ChatExchange(status=200, body="{not json", duration_ms=1))

        assert parsed.ok is False
        assert parsed.error is not None
        assert parsed.error.startswith("unreadable chat response")

    def test_tool_calls_in_a_control_response_are_an_error(self) -> None:
        document = body("Oslo.")
        document["choices"][0]["message"]["tool_calls"] = [{"id": "call_1"}]

        parsed = parse_chat_completion(exchange(document))

        assert parsed.ok is False
        assert parsed.error == "response carried tool calls in a control run"

    def test_tool_calls_are_reported_even_when_the_response_has_no_text(self) -> None:
        """A function-call-only response is still a control-arm violation."""
        document = body(None)
        document["choices"][0]["message"]["tool_calls"] = [{"id": "call_1"}]

        parsed = parse_chat_completion(exchange(document))

        assert parsed.ok is False
        assert parsed.error == "response carried tool calls in a control run"

    def test_an_answer_that_is_all_thinking_is_an_error(self) -> None:
        parsed = parse_chat_completion(exchange(body("<think>bare tanker</think>")))

        assert parsed.ok is False
        assert parsed.error == "empty answer after stripping the thinking block"

    def test_a_non_200_status_is_an_error_with_the_status(self) -> None:
        parsed = parse_chat_completion(exchange({"error": "nope"}, status=503))

        assert parsed.ok is False
        assert parsed.error == "chat endpoint returned HTTP 503"

    def test_a_transport_error_wins_over_the_body(self) -> None:
        failed = ChatExchange(status=0, body="", duration_ms=1, error="chat request failed: x")

        assert parse_chat_completion(failed).error == "chat request failed: x"

    def test_a_timeout_is_an_error(self) -> None:
        timed_out = ChatExchange(status=0, body="", duration_ms=1, timed_out=True)

        assert parse_chat_completion(timed_out).error == "chat request timed out"

    def test_failure_builds_a_schema_valid_record(self) -> None:
        parsed = parse_chat_completion(exchange({"error": "nope"}, status=500))

        record = build_result_record(IDENTITY, "llhb-v1-C1-001", parsed, TIMING)

        validate_case(record, load_schema(SCHEMA_DIR / "result_record.schema.json"))
        assert record["completed"] is False
        assert record["final_answer"] is None


class TestPostChat:
    def test_posts_json_with_the_bearer_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=URL, json=body("Oslo."))
        request = build_request(IDENTITY, "q", "s", ChatSampling())

        result = post_chat(ENDPOINT, request)

        sent = httpx_mock.get_requests()[0]
        assert sent.headers["Authorization"] == "Bearer sk-test"
        assert json.loads(sent.content)["messages"][1]["content"] == "q"
        assert result.status == 200
        assert result.timed_out is False
        assert result.error is None
        assert json.loads(result.body)["choices"][0]["message"]["content"] == "Oslo."

    def test_a_timeout_becomes_a_result(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=URL)

        result = post_chat(ENDPOINT, build_request(IDENTITY, "q", "s", ChatSampling()))

        assert result.timed_out is True
        assert result.error == "chat request timed out"
        assert result.status == 0

    def test_a_connection_error_becomes_a_result(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=URL)

        result = post_chat(ENDPOINT, build_request(IDENTITY, "q", "s", ChatSampling()))

        assert result.timed_out is False
        assert result.error is not None
        assert result.error.startswith("chat request failed: ")

    def test_a_rejected_credential_is_permanent(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=URL, status_code=401, json={"detail": "bad key"})

        result = post_chat(ENDPOINT, build_request(IDENTITY, "q", "s", ChatSampling()))

        assert result.status == 401
        assert result.permanent_failure is True

    def test_a_server_error_is_not_permanent(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=URL, status_code=503)

        result = post_chat(ENDPOINT, build_request(IDENTITY, "q", "s", ChatSampling()))

        assert result.permanent_failure is False
