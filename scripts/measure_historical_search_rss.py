#!/usr/bin/env python
"""Peak RSS of historical ``search_body`` states on a real corpus (issue #223).

A historical ``search_body`` captures the whole ``git archive`` tar in memory
and keeps the state's stripped bodies in ``_SnapshotData.search_bodies``, up to
``_MAX_SNAPSHOT_STATES`` states at once. This measures what that costs, through
the same ``CorpusReader`` the MCP server runs, in four scenarios:

``cold_one_state``
    one historical ``search_body`` from a fresh reader: archive capture plus
    strip, the transient peak.
``four_states``
    four distinct historical states resident at once.
``live_warm_four_states``
    the same, after a live ``search_body`` has built the current body index.
``embeddings_warm_four_states``
    the same again, after the embedding index is also built — what a hosted
    server holds once ``semantic_search`` has been called.

Each scenario runs in a fresh interpreter because ``ru_maxrss`` is a
high-water mark that never falls inside one process. Read-only: the corpus is
only read, and the embeddings scenario builds its index from the committed
sidecars — the adapter is constructed but never asked for a vector, so no
provider request is made and no credential is needed.

    uv run python scripts/measure_historical_search_rss.py \\
        --corpus ../lovverk --dates 2026-06-15,2026-07-15,2026-08-15,2026-09-15
"""

from __future__ import annotations

import argparse
import os
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date
from functools import partial
from pathlib import Path

from pydantic import BaseModel, ValidationError

from lovspor.embeddings import EmbeddingConfig, create_embedder
from lovspor.mcp import CorpusReader

SCENARIOS = (
    "cold_one_state",
    "four_states",
    "live_warm_four_states",
    "embeddings_warm_four_states",
)
# The hosted unit's cgroup limits (deploy/digitalocean/lovspor-mcp.service).
MEMORY_HIGH_MIB = 1400
MEMORY_MAX_MIB = 1700
# _MAX_SNAPSHOT_STATES in lovspor.mcp: the most states the reader keeps at once.
STATE_COUNT = 4
_KIB = 1024
_MIB = 1024 * 1024
# Satisfies adapter construction only; the index build never embeds a query.
_NO_REQUEST_KEY = "measurement-only-no-request-is-sent"


class Step(BaseModel):
    """One sample, taken after a step finished, in the scenario's own process."""

    scenario: str
    step: str
    seconds: float
    peak_mib: float
    rss_mib: float
    child_peak_mib: float
    detail: str = ""


def maxrss_to_mib(maxrss: int, platform: str) -> float:
    """``ru_maxrss`` is bytes on macOS and KiB on Linux."""
    unit = 1 if platform == "darwin" else _KIB
    return maxrss * unit / _MIB


def current_rss_mib() -> float:
    """Resident set now — ``ps`` reports KiB on both platforms."""
    out = subprocess.run(  # noqa: S603
        ["ps", "-o", "rss=", "-p", str(os.getpid())],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return int(out.stdout.strip()) / _KIB


def _sample(scenario: str, step: str, started: float, detail: str) -> Step:
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return Step(
        scenario=scenario,
        step=step,
        seconds=round(time.monotonic() - started, 2),
        peak_mib=round(maxrss_to_mib(own, sys.platform), 1),
        rss_mib=round(current_rss_mib(), 1),
        child_peak_mib=round(maxrss_to_mib(children, sys.platform), 1),
        detail=detail,
    )


def parse_steps(stdout: str) -> list[Step]:
    """Every non-blank line a child printed is one :class:`Step`."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("the scenario printed no steps")
    try:
        return [Step.model_validate_json(line) for line in lines]
    except ValidationError as exc:
        raise ValueError(f"unreadable step line: {exc}") from exc


def render_table(steps: list[Step]) -> str:
    """Markdown table; headroom is negative once a limit is crossed."""
    head = (
        "| scenario | step | s | peak MiB | rss MiB | git peak MiB "
        f"| vs High {MEMORY_HIGH_MIB} | vs Max {MEMORY_MAX_MIB} | detail |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {s.scenario} | {s.step} | {s.seconds} | {s.peak_mib:.0f} | {s.rss_mib:.0f} "
        f"| {s.child_peak_mib:.0f} | {MEMORY_HIGH_MIB - s.peak_mib:.0f} "
        f"| {MEMORY_MAX_MIB - s.peak_mib:.0f} | {s.detail} |"
        for s in steps
    ]
    return "\n".join([head, *rows])


_Action = tuple[str, Callable[[], str]]


def _historical(reader: CorpusReader, day: date, query: str) -> str:
    result = reader.at_state(day.isoformat()).search_body(query)
    return f"commit {result['corpus_commit'][:9]}, {len(result['results'])} hits"


def _live(reader: CorpusReader, query: str) -> str:
    hits = reader.search_body(query)
    return f"{len(hits)} hits"


def _embeddings(reader: CorpusReader) -> str:
    # The index build is private; semantic_search would also embed a query,
    # which is a provider request this measurement must not make.
    index = reader._load_embedding_index()
    excluded = dict(reader._excluded_bins)
    return f"{len(index)} sections, excluded {excluded}"


def _reader(corpus: Path, with_embedder: bool) -> CorpusReader:
    embedder = None
    if with_embedder:
        embedder = create_embedder(EmbeddingConfig.from_env(api_key=_NO_REQUEST_KEY))
    reader = CorpusReader(corpus, embedder=embedder)
    _ = reader.manifest
    return reader


def _plan(scenario: str, reader: CorpusReader, dates: list[date], query: str) -> list[_Action]:
    plan: list[_Action] = []
    if scenario == "embeddings_warm_four_states":
        plan.append(("embedding index", partial(_embeddings, reader)))
    if scenario in ("live_warm_four_states", "embeddings_warm_four_states"):
        plan.append(("live search_body", partial(_live, reader, query)))
    chosen = dates[:1] if scenario == "cold_one_state" else dates
    plan += [(f"recorded_at={d}", partial(_historical, reader, d, query)) for d in chosen]
    return plan


def run_child(scenario: str, corpus: Path, dates: list[date], query: str) -> None:
    """Run one scenario in this process, printing a :class:`Step` per line."""
    started = time.monotonic()
    reader = _reader(corpus, scenario == "embeddings_warm_four_states")
    print(_sample(scenario, "reader + manifest", started, "").model_dump_json(), flush=True)
    for name, action in _plan(scenario, reader, dates, query):
        detail = action()
        print(_sample(scenario, name, started, detail).model_dump_json(), flush=True)


def run_parent(args: argparse.Namespace) -> None:
    """Each scenario in a fresh interpreter, so every peak is its own."""
    steps: list[Step] = []
    for scenario in args.scenarios.split(","):
        child = subprocess.run(  # noqa: S603
            [sys.executable, __file__, "--child", scenario, *_shared_args(args)],
            stdout=subprocess.PIPE,
            check=True,
            text=True,
        )
        steps += parse_steps(child.stdout)
    print(render_table(steps))


def _shared_args(args: argparse.Namespace) -> list[str]:
    return ["--corpus", str(args.corpus), "--dates", args.dates, "--query", args.query]


def parse_dates(value: str) -> list[date]:
    """Exactly four distinct dates: a repeat would re-hit a cached state and
    report three resident states as four."""
    dates = [date.fromisoformat(d) for d in value.split(",")]
    if len(dates) != STATE_COUNT or len(set(dates)) != STATE_COUNT:
        raise ValueError(f"--dates needs {STATE_COUNT} distinct dates, got {value!r}")
    return dates


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dates", required=True, help="four YYYY-MM-DD, comma-separated")
    parser.add_argument("--query", default="arbeidsgiver")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--child", choices=SCENARIOS, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = _parse_args(argv)
    dates = parse_dates(args.dates)
    if args.child:
        run_child(args.child, args.corpus.resolve(), dates, args.query)
    else:
        run_parent(args)


if __name__ == "__main__":
    main(sys.argv[1:])
