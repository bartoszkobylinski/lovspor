#!/usr/bin/env python3
"""Tell an infrastructure death apart from a job that failed on its own (#272).

When the self-hosted box goes unreachable mid-job, GitHub records the job as
`failure` while the step it was executing is still `in_progress`: the step never
completed and never failed, because the machine stopped answering. The pipeline
then reported that as "codex-tests BLOCKED before the tests ran", which reads as
a verdict about the pull request. It is not. On 2026-09-10 that misreading cost
about three hours on PR #269 — two dead runs whose diff was never at fault.

The distinction is mechanical: a job that reached its own failure has completed
steps up to the one that failed; a job that died with its runner has a step
frozen mid-flight. This script reads the run's jobs payload
(`gh api repos/<repo>/actions/runs/<id>/jobs`) and says which it was.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Verdict:
    """What ended the lane, and the evidence for saying so."""

    kind: str
    job: str = ""
    step: str = ""

    @property
    def is_infrastructure(self) -> bool:
        return self.kind == "infrastructure"


def _unfinished_step(job: dict[str, Any]) -> str:
    """Name the first step left mid-flight, or "" when every step finished.

    A step is unfinished when it never reached `completed` — `in_progress`,
    `queued`, or a null conclusion. Steps that were skipped after the failure
    are `completed` with conclusion `skipped`, so they never match.
    """
    for step in job.get("steps") or []:
        status = step.get("status")
        if status != "completed" or step.get("conclusion") is None:
            name = step.get("name")
            return str(name) if name else "(unnamed step)"
    return ""


def classify(jobs: list[dict[str, Any]], lanes: list[str]) -> Verdict:
    """Classify the first failed lane job in `lanes` order."""
    by_name = {str(job.get("name")): job for job in jobs}
    for lane in lanes:
        job = by_name.get(lane)
        if job is None or job.get("conclusion") != "failure":
            continue
        step = _unfinished_step(job)
        if step:
            return Verdict("infrastructure", job=lane, step=step)
        return Verdict("in_job", job=lane)
    return Verdict("unknown")


def _load(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    # `gh api .../jobs` returns {"total_count": n, "jobs": [...]}; a caller that
    # already unwrapped it with --jq may hand over the bare list.
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return [job for job in jobs if isinstance(job, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", default="-", help="jobs JSON file, or - for stdin")
    parser.add_argument(
        "--lane",
        action="append",
        default=None,
        help="lane job name, repeatable; checked in the order given",
    )
    args = parser.parse_args(argv)

    lanes = args.lane or ["codex-author", "codex-tests"]
    verdict = classify(_load(args.jobs), lanes)
    print(f"kind={verdict.kind}")
    print(f"job={verdict.job}")
    print(f"step={verdict.step}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
