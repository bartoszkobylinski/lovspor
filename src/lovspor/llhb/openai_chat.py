"""OpenAI-compatible chat-completions driver for the LLHB control arm.

Ruling #25 runs every provider through its own agent CLI. Models that
have no CLI — NorMistral served by Sigma2, Borealis on a rented vLLM —
can still take the control arm over the plain chat-completions shape
those servers all speak. This driver builds one request per case,
posts it, and turns the response into the same ``ParsedCliResult`` the
Claude driver produces, so the orchestrator's retry loop, raw-response
retention and record schema are shared rather than duplicated.

Control only, by construction: the request carries no ``tools`` field,
and a response that reports tool calls anyway becomes an error record —
a control run that touched a tool is invalid under ruling #25 whichever
driver produced it. The treatment arm for these models needs the
function-calling bridge deferred to v2 (DECISIONS.md, ruling #25).

Reasoning models (NorMistral-11b-thinking) prefix the answer with a
``<think>…</think>`` block. The scorer reads the answer, so the block is
stripped from ``final_answer``; the raw body — block included — is
retained by the orchestrator beside the run.
"""

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel

from lovspor.llhb.claude_cli import HarnessTrace, ParsedCliResult, RunIdentity

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN = "<think>"
_HTTP_OK = 200
_NO_TOOLS = HarnessTrace(exposed_tools=(), mcp_servers=(), permission_denials=())
# A rejected credential is not transient: no retry can change it.
_PERMANENT_STATUSES = frozenset({401, 403})


class ChatSampling(BaseModel, frozen=True):
    """Sampling settings sent verbatim and recorded in run metadata."""

    temperature: float = 0.0
    max_output_tokens: int | None = None


class ChatEndpoint(BaseModel, frozen=True):
    """Where the control arm posts, and with what credential."""

    base_url: str
    api_key: str
    timeout_s: int = 600


class ChatExchange(BaseModel):
    """Raw outcome of one HTTP exchange, before any parsing."""

    status: int
    body: str
    duration_ms: int
    timed_out: bool = False
    error: str | None = None

    @property
    def permanent_failure(self) -> bool:
        return self.status in _PERMANENT_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.status == _HTTP_OK and self.error is None and not self.timed_out


def build_request(
    identity: RunIdentity, question: str, system_prompt: str, sampling: ChatSampling
) -> dict[str, Any]:
    """The chat-completions body for one control case; never a ``tools`` field."""
    if identity.condition != "control":
        raise ValueError("the chat driver serves the control arm only")
    request: dict[str, Any] = {
        "model": identity.model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "temperature": sampling.temperature,
        "stream": False,
    }
    if sampling.max_output_tokens is not None:
        request["max_tokens"] = sampling.max_output_tokens
    return request


def post_chat(endpoint: ChatEndpoint, request: dict[str, Any]) -> ChatExchange:
    """One POST to ``/chat/completions``; a transport failure is a result."""
    started = time.monotonic()
    url = endpoint.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {endpoint.api_key}"}
    try:
        with httpx.Client(timeout=endpoint.timeout_s) as client:
            response = client.post(url, json=request, headers=headers)
        status, body, timed_out, error = response.status_code, response.text, False, None
    except httpx.TimeoutException:
        status, body, timed_out, error = 0, "", True, "chat request timed out"
    except httpx.HTTPError as exc:
        status, body, timed_out, error = 0, "", False, f"chat request failed: {exc}"
    duration_ms = int((time.monotonic() - started) * 1000)
    return ChatExchange(
        status=status, body=body, duration_ms=duration_ms, timed_out=timed_out, error=error
    )


def strip_thinking(text: str) -> str:
    """Drop every ``<think>…</think>`` block; an unclosed block is dropped to the end."""
    stripped = _THINK_RE.sub("", text)
    open_at = stripped.find(_THINK_OPEN)
    if open_at != -1:
        stripped = stripped[:open_at]
    return stripped.strip()


def parse_chat_completion(exchange: ChatExchange) -> ParsedCliResult:
    """Normalize one exchange; never raises on bad output."""
    if exchange.error is not None or exchange.timed_out:
        return ParsedCliResult(ok=False, error=exchange.error or "chat request timed out")
    if not exchange.succeeded:
        return ParsedCliResult(ok=False, error=f"chat endpoint returned HTTP {exchange.status}")
    try:
        message, finish_reason, usage = _first_choice(json.loads(exchange.body))
        answer = _answer_text(message)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return ParsedCliResult(ok=False, error=f"unreadable chat response: {exc}")
    if message.get("tool_calls"):
        return ParsedCliResult(ok=False, error="response carried tool calls in a control run")
    if not answer:
        return ParsedCliResult(ok=False, error="empty answer after stripping the thinking block")
    return ParsedCliResult(
        ok=True,
        final_answer=answer,
        turns=1,
        usage=usage,
        harness=_NO_TOOLS,
        truncated=finish_reason == "length",
    )


def _first_choice(body: Any) -> tuple[dict[str, Any], str | None, dict[str, object] | None]:
    if not isinstance(body, dict):
        raise ValueError("body is not a JSON object")
    choices = body["choices"]
    if not isinstance(choices, list) or not choices:
        raise ValueError("no choices")
    message = choices[0]["message"]
    if not isinstance(message, dict):
        raise ValueError("message is not an object")
    usage = body.get("usage")
    return message, choices[0].get("finish_reason"), usage if isinstance(usage, dict) else None


def _answer_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, list):
        # Some servers answer in content parts; only the text parts carry the answer.
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise TypeError(f"content is {type(content).__name__}")
    return strip_thinking(content)
