#!/usr/bin/env python3
"""Fail loudly when the production corpus stops following Lovdata.

The nightly sync (`.github/workflows/sync.yml`) reports a run that fails or is
cancelled (#790), but nothing reported the slower failures: a schedule that
stopped firing, a run that succeeds and pushes nothing, or a hosted site that
still serves an old corpus. This check looks at the outcome instead of the
mechanism, once a day, after the sync's observed start window.

Failures (exit 1, so a scheduled run notifies the owner):

1. The latest completed `sync.yml` run on `main` did not succeed, or no run
   completed within the age limit.
2. lovverk `main` is older than the age limit AND Lovdata's `lastModified`
   for a tracked archive is newer than lovverk's last commit: upstream moved,
   the corpus did not.

Warnings (informational, never a failure):

3. lovspor.no's `site-facts.json` `corpus_commit` is not lovverk `main`, or
   `deployment-capabilities.json` `state.checkout.lovspor_commit` is not the
   engine's `main`. The lag is printed. A release is a deliberate owner act
   (`docs/operations.md`, release envelope), so lag is expected between
   releases and only reported.

It fails closed where it must decide: an unreadable run list or lovverk head
is a failure, and so is an unreachable Lovdata while lovverk is already old.
The warnings tolerate an unreachable site.

Stdlib only, so the workflow runs it with the runner's own python3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The datasets the sync tracks (src/lovspor/sync/orchestrator.py,
# _TRACKED_DATASETS), as Lovdata names their archives.
TRACKED_ARCHIVES = ("gjeldende-lover.tar.bz2", "gjeldende-sentrale-forskrifter.tar.bz2")
LOVDATA_LIST_URL = "https://api.lovdata.no/v1/publicData/list"
SITE_FACTS_URL = "https://lovspor.no/site-facts.json"
CAPABILITIES_URL = "https://lovspor.no/deployment-capabilities.json"
GITHUB_API = "https://api.github.com"
USER_AGENT = "lovspor-corpus-freshness (+https://github.com/bartoszkobylinski/lovspor)"
HTTP_OK = 200
# No HTTP status at all: the request never reached an answering server.
UNREACHABLE = 0

ERROR = "error"
WARNING = "warning"
NOTICE = "notice"

Fetch = Callable[[str], tuple[int, Any]]


@dataclass(frozen=True)
class SyncRun:
    """One `sync.yml` run on main, as the Actions API lists it."""

    status: str
    conclusion: str | None
    created_at: datetime
    url: str


@dataclass(frozen=True)
class Commit:
    sha: str
    time: datetime


@dataclass(frozen=True)
class Lag:
    """What production serves against what `main` holds, for one repository."""

    served: str
    main: Commit
    behind_by: int | None = None
    served_time: datetime | None = None


@dataclass(frozen=True)
class Observed:
    """Everything the verdict reads. `None` means "could not be read"."""

    now: datetime
    sync_runs: list[SyncRun] | None
    lovverk_main: Commit | None
    archives: dict[str, datetime] | None
    corpus_lag: Lag | None = None
    engine_lag: Lag | None = None
    unread: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def parse_time(value: str) -> datetime:
    """An ISO 8601 timestamp as an aware UTC instant; naive input is UTC."""
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hours(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600


def _age(hours: float) -> str:
    return f"{hours / 24:.1f} days" if hours >= 48 else f"{hours:.1f} h"  # noqa: PLR2004


def judge_sync(runs: list[SyncRun] | None, now: datetime, max_age_hours: float) -> list[Finding]:
    """Check 1: the latest completed sync on main succeeded, recently."""
    if runs is None:
        return [Finding(ERROR, "could not read the sync.yml run list")]
    findings = [
        Finding(NOTICE, f"a sync.yml run is {run.status}: {run.url}")
        for run in runs
        if run.status != "completed"
    ]
    completed = sorted(
        (run for run in runs if run.status == "completed"),
        key=lambda run: run.created_at,
        reverse=True,
    )
    if not completed:
        return [*findings, Finding(ERROR, "no completed sync.yml run on main was found")]
    latest = completed[0]
    age = _hours(now, latest.created_at)
    if latest.conclusion != "success":
        findings.append(
            Finding(
                ERROR,
                f"the latest completed sync.yml run on main concluded "
                f"{latest.conclusion or 'without a conclusion'} "
                f"({_age(age)} ago): {latest.url}",
            )
        )
    elif age > max_age_hours:
        findings.append(
            Finding(
                ERROR,
                f"no sync.yml run on main has completed for {_age(age)} "
                f"(limit {max_age_hours:g} h); the last one: {latest.url}",
            )
        )
    else:
        findings.append(Finding(NOTICE, f"latest sync.yml run on main succeeded: {latest.url}"))
    return findings


def _synced_since(runs: list[SyncRun] | None, instant: datetime) -> bool:
    return any(run.conclusion == "success" and run.created_at > instant for run in runs or ())


def judge_corpus(observed: Observed, max_age_hours: float) -> list[Finding]:
    """Check 2: lovverk is old AND Lovdata published after its last commit."""
    head = observed.lovverk_main
    if head is None:
        return [Finding(ERROR, "could not read lovverk main")]
    age = _hours(observed.now, head.time)
    where = f"lovverk main {head.sha[:8]} is {_age(age)} old"
    if age <= max_age_hours:
        return [Finding(NOTICE, f"{where} (limit {max_age_hours:g} h)")]
    if observed.archives is None:
        return [Finding(ERROR, f"{where} and Lovdata's archive list could not be read")]
    findings: list[Finding] = []
    for name in TRACKED_ARCHIVES:
        modified = observed.archives.get(name)
        if modified is None:
            findings.append(Finding(ERROR, f"{where} and Lovdata no longer lists {name}"))
        elif modified > head.time:
            message = (
                f"{where}; Lovdata modified {name} at {modified:%Y-%m-%dT%H:%MZ}, "
                f"{_age(_hours(modified, head.time))} after it"
            )
            if _synced_since(observed.sync_runs, modified):
                # Kept a failure on purpose: a sync that exits 0 and pushes
                # nothing is one of the failures this check exists for. The
                # hint only says where to look first.
                message += (
                    "; a successful sync started after that, so check its log: "
                    "a republished archive with no content change also lands here"
                )
            findings.append(Finding(ERROR, message))
    if not findings:
        findings.append(Finding(NOTICE, f"{where}, but Lovdata has published nothing newer since"))
    return findings


def _lag_line(label: str, lag: Lag) -> str:
    parts = [f"{label} serves {lag.served[:8]}; main is {lag.main.sha[:8]}"]
    if lag.behind_by is not None:
        parts.append(f"{lag.behind_by} commit(s) behind")
    if lag.served_time is not None:
        parts.append(f"{_age(_hours(lag.main.time, lag.served_time))} older")
    return ", ".join(parts)


def judge_site(observed: Observed) -> list[Finding]:
    """Check 3, informational: what lovspor.no serves against main."""
    findings = [Finding(WARNING, f"could not read {what}") for what in observed.unread]
    for label, lag in (
        ("lovspor.no site-facts.json corpus_commit", observed.corpus_lag),
        ("lovspor.no deployment-capabilities.json lovspor_commit", observed.engine_lag),
    ):
        if lag is None:
            continue
        if lag.served == lag.main.sha:
            findings.append(Finding(NOTICE, f"{label} is main ({lag.main.sha[:8]})"))
        else:
            findings.append(Finding(WARNING, _lag_line(label, lag)))
    return findings


def judge(observed: Observed, max_age_hours: float) -> list[Finding]:
    return [
        *judge_sync(observed.sync_runs, observed.now, max_age_hours),
        *judge_corpus(observed, max_age_hours),
        *judge_site(observed),
    ]


def fetch_json(url: str, token: str | None = None) -> tuple[int, Any]:
    """GET a JSON document; (status, payload), payload None unless HTTP 200."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"refusing a non-https URL: {url!r}")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token and url.startswith(GITHUB_API + "/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https checked above
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https checked above
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return UNREACHABLE, None


def _read_runs(fetch: Fetch, repo: str) -> list[SyncRun] | None:
    status, payload = fetch(
        f"{GITHUB_API}/repos/{repo}/actions/workflows/sync.yml/runs?branch=main&per_page=10"
    )
    if status != HTTP_OK or not isinstance(payload, dict):
        return None
    try:
        return [
            SyncRun(
                status=run["status"],
                conclusion=run.get("conclusion"),
                created_at=parse_time(run["created_at"]),
                url=run["html_url"],
            )
            for run in payload["workflow_runs"]
        ]
    except (KeyError, TypeError, ValueError):
        return None


def _read_head(fetch: Fetch, repo: str) -> Commit | None:
    status, payload = fetch(f"{GITHUB_API}/repos/{repo}/commits/main")
    if status != HTTP_OK or not isinstance(payload, dict):
        return None
    try:
        return Commit(payload["sha"], parse_time(payload["commit"]["committer"]["date"]))
    except (KeyError, TypeError, ValueError):
        return None


def _read_archives(fetch: Fetch) -> dict[str, datetime] | None:
    status, payload = fetch(LOVDATA_LIST_URL)
    if status != HTTP_OK or not isinstance(payload, list):
        return None
    try:
        return {item["filename"]: parse_time(item["lastModified"]) for item in payload}
    except (KeyError, TypeError, ValueError):
        return None


def _read_lag(fetch: Fetch, repo: str, served: str, main: Commit) -> Lag:
    """Commits and time between the served commit and main; unknowns stay None."""
    if served == main.sha:
        return Lag(served, main)
    status, payload = fetch(f"{GITHUB_API}/repos/{repo}/compare/{served}...{main.sha}")
    if status != HTTP_OK or not isinstance(payload, dict):
        return Lag(served, main)
    behind = payload.get("ahead_by")
    try:
        served_time = parse_time(payload["base_commit"]["commit"]["committer"]["date"])
    except (KeyError, TypeError, ValueError):
        served_time = None
    return Lag(served, main, behind if isinstance(behind, int) else None, served_time)


def _served(fetch: Fetch, url: str, path: tuple[str, ...]) -> str | None:
    status, payload = fetch(url)
    if status != HTTP_OK:
        return None
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) and value else None


def observe(fetch: Fetch, repo: str, corpus_repo: str, now: datetime) -> Observed:
    """Read every input once. Network lives here; the verdict never fetches."""
    lovverk_main = _read_head(fetch, corpus_repo)
    archives = _read_archives(fetch)
    unread: list[str] = []
    corpus_lag = engine_lag = None
    served_corpus = _served(fetch, SITE_FACTS_URL, ("corpus_commit",))
    if served_corpus is None:
        unread.append("site-facts.json corpus_commit")
    elif lovverk_main is not None:
        corpus_lag = _read_lag(fetch, corpus_repo, served_corpus, lovverk_main)
    served_engine = _served(fetch, CAPABILITIES_URL, ("state", "checkout", "lovspor_commit"))
    engine_main = _read_head(fetch, repo)
    if served_engine is None:
        unread.append("deployment-capabilities.json state.checkout.lovspor_commit")
    elif engine_main is None:
        unread.append(f"{repo} main")
    else:
        engine_lag = _read_lag(fetch, repo, served_engine, engine_main)
    return Observed(
        now=now,
        sync_runs=_read_runs(fetch, repo),
        lovverk_main=lovverk_main,
        archives=archives,
        corpus_lag=corpus_lag,
        engine_lag=engine_lag,
        unread=unread,
    )


_MARK = {ERROR: "❌", WARNING: "⚠️", NOTICE: "✅"}


def render(findings: list[Finding]) -> str:
    return "".join(f"- {_MARK[f.level]} {f.message}\n" for f in findings)


def _report(findings: list[Finding], report_path: str | None) -> None:
    for finding in findings:
        print(f"::{finding.level}::{finding.message}")
    text = render(findings)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"## Corpus freshness\n\n{text}")
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None, fetch: Fetch | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the engine repository")
    parser.add_argument("--corpus-repo", required=True, help="owner/name of the corpus repository")
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument("--report", help="also write the findings as Markdown to this file")
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or None
    fetcher: Fetch = fetch or (lambda url: fetch_json(url, token))
    observed = observe(fetcher, args.repo, args.corpus_repo, datetime.now(UTC))
    findings = judge(observed, args.max_age_hours)
    _report(findings, args.report)
    return 1 if any(f.level == ERROR for f in findings) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
