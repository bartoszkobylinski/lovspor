#!/usr/bin/env python
"""Load test for the hosted MCP endpoint, ``lovspor mcp-http`` (issue #480).

Drives N concurrent simulated clients over Streamable HTTP, for each level of
``--concurrency``, and reports per level: calls, ok, refusals by reason
(``capacity`` / ``in_flight`` / ``rate`` / ``quota``), errors, latency
p50/p95/p99 of the successful calls, and throughput.

Each client opens its own MCP session and replays a research question:
``search_laws`` -> ``get_section`` x k -> ``verify_quote`` (a quote taken from
the first section it read) -> ``validate_citation``, over a small fixed list of
real acts. ``semantic_search`` spends the operator's OpenAI money and runs only
with ``--include-paid``. A refused call is counted and not retried: the report
shows the brakes as a client meets them.

The token comes from ``--token-file`` (one per line, ``-`` for stdin; clients
take the tokens round-robin) or ``LOVSPOR_LOAD_TOKEN``, never from argv, and is
never printed. Repo tooling only: this is not part of the MCP runtime and never
runs in CI. The production procedure (dedicated low-quota token, quiet hour,
revoke after) is ``docs/operations.md`` § "Load-testing the hosted endpoint".

    LOVSPOR_LOAD_TOKEN=lsp_... uv run python scripts/load/mcp_load.py \\
        --url http://127.0.0.1:8000/mcp --concurrency 1,2,4,8,16 --questions 3
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from lovspor.errors import LovsporError

TOKEN_ENV = "LOVSPOR_LOAD_TOKEN"  # noqa: S105 -- a variable name, not a secret
DEFAULT_LEVELS = "1,2,4,8,16"
FALLBACK_QUOTE = "Loven gjelder"
_SEARCH_LIMIT = 5
_QUOTE_WORDS = 8
_ERROR_EXAMPLES = 3
_DETAIL_CHARS = 200

Call = tuple[str, dict[str, Any]]


class LoadTestError(LovsporError):
    """Bad arguments or a missing token: the run cannot start."""


class Outcome(StrEnum):
    OK = "ok"
    CAPACITY = "capacity"
    IN_FLIGHT = "in_flight"
    RATE = "rate"
    QUOTA = "quota"
    ERROR = "error"


REFUSALS = (Outcome.CAPACITY, Outcome.IN_FLIGHT, Outcome.RATE, Outcome.QUOTA)

# Substrings of the QuotaExceededError messages in src/lovspor/quota.py, which
# reach the client as in-band tool errors (a tool body cannot send a 429).
# "unknown credential" is deliberately absent: that is an auth failure, an error.
_REFUSAL_MARKERS: tuple[tuple[str, Outcome], ...] = (
    ("is at capacity", Outcome.CAPACITY),
    ("calls already in flight", Outcome.IN_FLIGHT),
    ("rate limit of", Outcome.RATE),
    ("daily quota of", Outcome.QUOTA),
    ("daily ceiling of", Outcome.QUOTA),
    ("semantic searches is exhausted", Outcome.QUOTA),
)


class Scenario(BaseModel, frozen=True):
    """One research question: a search, then sections of the act it finds."""

    query: str
    slug: str
    sections: tuple[str, ...]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(query="husleie", slug="husleieloven-husll", sections=("1-1", "9-2", "10-2")),
    Scenario(query="arbeidsmiljø", slug="arbeidsmiljøloven-aml", sections=("14-9", "15-3")),
    Scenario(query="forvaltning", slug="forvaltningsloven-fvl", sections=("17", "24")),
    Scenario(query="skatt", slug="skatteloven-sktl", sections=("5-1", "1-1")),
    Scenario(query="personopplysning", slug="personopplysningsloven", sections=("1", "24")),
    Scenario(query="avhending", slug="avhendingslova-avhl", sections=("3-1", "4-1")),
)


class Sample(BaseModel):
    """One tool call as the client saw it."""

    tool: str
    outcome: Outcome
    seconds: float
    detail: str = ""


class LevelReport(BaseModel):
    """Everything measured at one concurrency level."""

    concurrency: int
    elapsed_s: float
    calls: int
    ok: int
    refused: dict[str, int]
    errors: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    calls_per_s: float
    ok_per_s: float
    per_tool: dict[str, dict[str, int]]
    error_examples: list[str]


class RunSettings(BaseModel):
    """What every client of a run shares. Tokens are passed separately, so a
    dump of the settings can never carry one."""

    url: str
    timeout: float
    questions: int
    duration: float | None
    include_paid: bool


# --- pure: classification, arithmetic, reports ------------------------------------


def classify(is_error: bool, text: str) -> Outcome:
    """A successful call is ``ok`` whatever its text says; an error is a refusal
    only when it carries one of the quota layer's own messages."""
    if not is_error:
        return Outcome.OK
    for marker, outcome in _REFUSAL_MARKERS:
        if marker in text:
            return outcome
    return Outcome.ERROR


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile: always a value that was actually observed."""
    if not 0 < pct <= 100:  # noqa: PLR2004
        raise LoadTestError(f"percentile must be in (0, 100], got {pct}")
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct * len(ordered) / 100)
    return ordered[rank - 1]


def _ms(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _rate(count: int, elapsed: float) -> float:
    return round(count / elapsed, 2) if elapsed > 0 else 0.0


def _per_tool(samples: Sequence[Sample]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter[str]] = {}
    for sample in samples:
        table.setdefault(sample.tool, Counter())[sample.outcome.value] += 1
    return {tool: dict(counts) for tool, counts in table.items()}


def _error_examples(samples: Sequence[Sample]) -> list[str]:
    details = [s.detail for s in samples if s.outcome is Outcome.ERROR and s.detail]
    return list(dict.fromkeys(details))[:_ERROR_EXAMPLES]


def summarize(concurrency: int, samples: Sequence[Sample], elapsed: float) -> LevelReport:
    """Latency is over successful calls only: a refusal returns in microseconds
    and would drag every percentile down exactly when the server is struggling."""
    counts = Counter(s.outcome for s in samples)
    ok_ms = [s.seconds * 1000 for s in samples if s.outcome is Outcome.OK]
    return LevelReport(
        concurrency=concurrency,
        elapsed_s=round(elapsed, 3),
        calls=len(samples),
        ok=counts[Outcome.OK],
        refused={outcome.value: counts[outcome] for outcome in REFUSALS},
        errors=counts[Outcome.ERROR],
        p50_ms=_ms(percentile(ok_ms, 50)),
        p95_ms=_ms(percentile(ok_ms, 95)),
        p99_ms=_ms(percentile(ok_ms, 99)),
        calls_per_s=_rate(len(samples), elapsed),
        ok_per_s=_rate(counts[Outcome.OK], elapsed),
        per_tool=_per_tool(samples),
        error_examples=_error_examples(samples),
    )


_TABLE_HEAD = (
    "| clients | calls | ok | capacity | in_flight | rate | quota | errors "
    "| p50 ms | p95 ms | p99 ms | calls/s | ok/s |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _row(report: LevelReport) -> str:
    refused = " | ".join(str(report.refused[o.value]) for o in REFUSALS)
    latency = " | ".join(_cell(v) for v in (report.p50_ms, report.p95_ms, report.p99_ms))
    return (
        f"| {report.concurrency} | {report.calls} | {report.ok} | {refused} "
        f"| {report.errors} | {latency} | {report.calls_per_s:g} | {report.ok_per_s:g} |"
    )


def render_table(reports: Sequence[LevelReport]) -> str:
    """Markdown table, one row per concurrency level."""
    return "\n".join([_TABLE_HEAD, *(_row(r) for r in reports)])


def render_json(reports: Sequence[LevelReport], run: Mapping[str, Any]) -> str:
    return json.dumps(
        {"run": dict(run), "levels": [r.model_dump(mode="json") for r in reports]},
        indent=2,
        ensure_ascii=False,
    )


# --- pure: arguments and tokens ------------------------------------------------------


def parse_levels(text: str) -> list[int]:
    """``"1,2,4"`` -> ``[1, 2, 4]``; every level a positive integer."""
    try:
        levels = [int(part) for part in text.split(",")]
    except ValueError as exc:
        raise LoadTestError(f"--concurrency wants positive integers, got {text!r}") from exc
    if not levels or min(levels) < 1:
        raise LoadTestError(f"--concurrency wants positive integers, got {text!r}")
    return levels


def parse_tokens(text: str) -> list[str]:
    """One token per line; blank lines and ``#`` comments are skipped."""
    lines = (line.strip() for line in text.splitlines())
    tokens = [line for line in lines if line and not line.startswith("#")]
    if not tokens:
        raise LoadTestError("the token file holds no token")
    return tokens


def _read_token_file(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoadTestError(f"cannot read token file {path}: {exc.strerror}") from exc


def resolve_tokens(path: Path | None, env: Mapping[str, str]) -> list[str]:
    """``--token-file`` wins over ``LOVSPOR_LOAD_TOKEN``; argv never carries one."""
    if path is not None:
        return parse_tokens(_read_token_file(path))
    value = env.get(TOKEN_ENV, "").strip()
    if not value:
        raise LoadTestError(f"no token: pass --token-file PATH (- for stdin) or set {TOKEN_ENV}")
    return [value]


def _levels_arg(text: str) -> list[int]:
    try:
        return parse_levels(text)
    except LoadTestError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n")[0],
        # Off: "--token lsp_..." would otherwise abbreviate --token-file and
        # put a token on argv, then echo it back as an unreadable path.
        allow_abbrev=False,
    )
    parser.add_argument("--url", required=True, help="the /mcp endpoint")
    parser.add_argument("--token-file", type=Path, help="tokens, one per line; - = stdin")
    parser.add_argument("--concurrency", type=_levels_arg, default=DEFAULT_LEVELS)
    size = parser.add_mutually_exclusive_group()
    size.add_argument("--questions", type=int, default=3, help="questions per client")
    size.add_argument("--duration", type=float, help="seconds per level instead")
    parser.add_argument("--include-paid", action="store_true", help="add semantic_search")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-request seconds")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between levels")
    parser.add_argument("--json-out", type=Path, help="also write the JSON report here")
    return parser.parse_args(argv)


# --- pure: the workflow a client replays ------------------------------------------------


def scenario_for(client: int, question: int) -> Scenario:
    """Spread clients across the list so N clients do not all read one act."""
    return SCENARIOS[(client + question) % len(SCENARIOS)]


def lookup_calls(scenario: Scenario, include_paid: bool) -> list[Call]:
    paid: list[Call] = [("semantic_search", {"query": scenario.query, "limit": _SEARCH_LIMIT})]
    search: Call = ("search_laws", {"query": scenario.query, "limit": _SEARCH_LIMIT})
    sections: list[Call] = [
        ("get_section", {"slug": scenario.slug, "section_id": section})
        for section in scenario.sections
    ]
    return [*(paid if include_paid else []), search, *sections]


def check_calls(scenario: Scenario, quote: str) -> list[Call]:
    first = scenario.sections[0]
    return [
        ("verify_quote", {"slug": scenario.slug, "section_id": first, "quote": quote}),
        ("validate_citation", {"citation": f"§ {first} {scenario.slug}"}),
    ]


def _has_alpha(token: str) -> bool:
    return any(char.isalpha() for char in token)


def quote_from(body: str, words: int = _QUOTE_WORDS) -> str:
    """The first run of plain words in a section body, as a model would quote it."""
    tokens = [token.strip("*_") for token in body.split()]
    start = next((i for i, token in enumerate(tokens) if _has_alpha(token)), None)
    if start is None:
        return FALLBACK_QUOTE
    return " ".join(itertools.takewhile(_has_alpha, tokens[start : start + words]))


def body_of(structured: object, text: str) -> str:
    data = structured if isinstance(structured, dict) else _json_or_none(text)
    body = data.get("body") if isinstance(data, dict) else None
    return body if isinstance(body, str) else ""


def _json_or_none(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return None


# --- network: sessions and levels ------------------------------------------------------


def _text_of(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def _sample(tool: str, started: float, outcome: Outcome, detail: str) -> Sample:
    kept = detail[:_DETAIL_CHARS] if outcome is Outcome.ERROR else ""
    return Sample(tool=tool, outcome=outcome, seconds=time.perf_counter() - started, detail=kept)


async def timed_call(session: ClientSession, call: Call) -> tuple[Sample, str]:
    """One tool call -> its sample and, for a section read, the section body."""
    tool, arguments = call
    started = time.perf_counter()
    try:
        result = await session.call_tool(tool, arguments)
    except (McpError, httpx.HTTPError) as exc:
        return _sample(tool, started, Outcome.ERROR, f"{type(exc).__name__}: {exc}"), ""
    text = _text_of(result)
    outcome = classify(result.isError, text)
    return _sample(tool, started, outcome, text), body_of(result.structuredContent, text)


async def run_question(
    session: ClientSession, scenario: Scenario, include_paid: bool, sink: list[Sample]
) -> None:
    body = ""
    for call in lookup_calls(scenario, include_paid):
        sample, text_body = await timed_call(session, call)
        sink.append(sample)
        body = body or text_body
    for call in check_calls(scenario, quote_from(body)):
        sink.append((await timed_call(session, call))[0])


def _more(settings: RunSettings, question: int, deadline: float | None) -> bool:
    if deadline is not None:
        return time.monotonic() < deadline
    return question < settings.questions


async def _client_loop(
    session: ClientSession, settings: RunSettings, client: int, sink: list[Sample]
) -> None:
    deadline = None if settings.duration is None else time.monotonic() + settings.duration
    question = 0
    while _more(settings, question, deadline):
        await run_question(session, scenario_for(client, question), settings.include_paid, sink)
        question += 1


async def run_client(settings: RunSettings, token: str, client: int, sink: list[Sample]) -> None:
    """One simulated user: its own HTTP client and MCP session for the level.

    A session that cannot open is one ``session`` error, not a crash of the
    level: at the edge of capacity that is itself a result worth counting.
    """
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(settings.timeout)
    started = time.perf_counter()
    try:
        async with (
            httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as http,
            streamable_http_client(settings.url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            await _client_loop(session, settings, client, sink)
    except (McpError, httpx.HTTPError, OSError, ExceptionGroup) as exc:
        sink.append(_sample("session", started, Outcome.ERROR, f"{type(exc).__name__}: {exc}"))


async def run_level(settings: RunSettings, tokens: Sequence[str], concurrency: int) -> LevelReport:
    sink: list[Sample] = []
    started = time.monotonic()
    async with asyncio.TaskGroup() as group:
        for client in range(concurrency):
            token = tokens[client % len(tokens)]
            group.create_task(run_client(settings, token, client, sink))
    return summarize(concurrency, sink, time.monotonic() - started)


async def run_all(
    settings: RunSettings, tokens: Sequence[str], levels: Sequence[int], pause: float
) -> list[LevelReport]:
    reports: list[LevelReport] = []
    for index, level in enumerate(levels):
        if index:
            await asyncio.sleep(pause)
        reports.append(await run_level(settings, tokens, level))
        print(f"level {level}: {reports[-1].calls} calls", file=sys.stderr)
    return reports


def _run_meta(args: argparse.Namespace, tokens: Sequence[str]) -> dict[str, Any]:
    return {
        "url": args.url,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "concurrency": args.concurrency,
        "questions_per_client": None if args.duration else args.questions,
        "duration_s": args.duration,
        "include_paid": args.include_paid,
        "tokens": len(tokens),
    }


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        tokens = resolve_tokens(args.token_file, os.environ)
    except LoadTestError as exc:
        print(f"mcp_load: {exc}", file=sys.stderr)
        return 2
    settings = RunSettings(
        url=args.url,
        timeout=args.timeout,
        questions=args.questions,
        duration=args.duration,
        include_paid=args.include_paid,
    )
    meta = _run_meta(args, tokens)
    reports = asyncio.run(run_all(settings, tokens, args.concurrency, args.pause))
    print(render_table(reports))
    if args.json_out is not None:
        args.json_out.write_text(render_json(reports, meta) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
