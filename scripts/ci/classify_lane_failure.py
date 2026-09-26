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

A job that did reach its own failure can still owe nothing to the diff (#270):
from 2026-09-10 08:55 UTC every `codex-tests` run died in `actions/checkout`
because the push token had expired, and the pipeline reported it as a pipeline
failure — the wording that sends a reader to the runner or the code. Handed the
failed lane job's log (`--log`), the script names that case `credential`: an
operator renews a secret, and no rerun or code change can clear it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# What git prints when GitHub refuses the credential it was handed. The first is
# verbatim from #270 (run 34457835291): git got the expired token, GitHub
# refused it, git fell back to asking for a username, and GIT_TERMINAL_PROMPT=0
# made that fatal. The second is git's wording for the same refusal when a
# credential helper answers instead of the prompt; it was not observed in #270.
CREDENTIAL_SIGNATURES = (
    "could not read Username for 'https://github.com': terminal prompts disabled",
    "Authentication failed for 'https://github.com/",
)

# Only the runner's own error annotation, straight after the line's timestamp,
# is evidence. A failing test that prints a fixture carrying one of the strings
# above is still a code failure.
_ERROR_ANNOTATION = re.compile(r"^(?:\S+\s)?##\[error\](?P<message>.*)$")


@dataclass(frozen=True)
class Verdict:
    """What ended the lane, and the evidence for saying so."""

    kind: str
    job: str = ""
    step: str = ""
    signature: str = ""

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


def _failed_step(job: dict[str, Any]) -> str:
    """Name the first step that concluded `failure`, or "" when none did."""
    for step in job.get("steps") or []:
        if step.get("conclusion") == "failure":
            return str(step.get("name") or "(unnamed step)")
    return ""


def credential_signature(log: str) -> str:
    """Return the credential signature an error annotation in `log` carries."""
    for line in log.splitlines():
        annotation = _ERROR_ANNOTATION.match(line)
        if annotation is None:
            continue
        for signature in CREDENTIAL_SIGNATURES:
            if signature in annotation.group("message"):
                return signature
    return ""


def _in_job(job: dict[str, Any], lane: str, log: str) -> Verdict:
    signature = credential_signature(log)
    if signature:
        return Verdict("credential", job=lane, step=_failed_step(job), signature=signature)
    return Verdict("in_job", job=lane)


def classify(jobs: list[dict[str, Any]], lanes: list[str], log: str = "") -> Verdict:
    """Classify the first failed lane job in `lanes` order.

    `log` is that job's log, when the caller fetched it. It only refines a job
    that reached its own failure: a step frozen mid-flight is the machine,
    whatever an earlier line of the log says.
    """
    by_name = {str(job.get("name")): job for job in jobs}
    for lane in lanes:
        job = by_name.get(lane)
        if job is None or job.get("conclusion") != "failure":
            continue
        step = _unfinished_step(job)
        if step:
            return Verdict("infrastructure", job=lane, step=step)
        return _in_job(job, lane, log)
    return Verdict("unknown")


def _load(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    # `gh api .../jobs` returns {"total_count": n, "jobs": [...]}; a caller that
    # already unwrapped it with --jq may hand over the bare list.
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return [job for job in jobs if isinstance(job, dict)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", default="-", help="jobs JSON file, or - for stdin")
    parser.add_argument(
        "--lane",
        action="append",
        default=None,
        help="lane job name, repeatable; checked in the order given",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="the failed lane job's log; adds the credential class and a signature= line",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    lanes = args.lane or ["codex-author", "codex-tests"]
    log = None if args.log is None else Path(args.log).read_text(encoding="utf-8", errors="replace")
    verdict = classify(_load(args.jobs), lanes, log or "")
    print(f"kind={verdict.kind}")
    print(f"job={verdict.job}")
    print(f"step={verdict.step}")
    # Only with --log, so a caller without it reads the same three lines.
    if log is not None:
        print(f"signature={verdict.signature}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
