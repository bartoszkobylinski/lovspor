#!/usr/bin/env python3
"""Warn before the CI push token expires, instead of after CI stops (#270).

`LOVSPOR_CI_PUSH_TOKEN` is a personal access token with an expiry date. On
2026-09-10 it lapsed and every PR blocked at `actions/checkout` with
"could not read Username for 'https://github.com'" — reported as "codex-tests
BLOCKED before the tests ran", a verdict about the pull request, for a cause
that was an operator's calendar. Nothing watched the date: the secret list
shows when a secret was *set*, never when its token *expires*.

GitHub answers an authenticated API request made with an expiring PAT with a
`GitHub-Authentication-Token-Expiration` response header. This script makes
one such request and turns the answer into a verdict. It fails closed: a
header it cannot read, a rejected token or an unreachable API is a failure,
never a pass. Only an accepted token with no expiry header passes without a
date, and says so, because that is what a non-expiring token looks like.

Stdlib only, so the workflow runs it with the runner's own python3.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EXPIRY_HEADER = "GitHub-Authentication-Token-Expiration"
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
# No HTTP status at all: the request never reached an answering server.
UNREACHABLE = 0
_FORMATS = ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S %z")


@dataclass(frozen=True)
class Answer:
    """What the API said to the token: an HTTP status and the expiry header."""

    status: int
    expiry: str | None


@dataclass(frozen=True)
class Verdict:
    """Whether the token is fit for the next run, and the line that says why."""

    ok: bool
    message: str


def parse_expiry(value: str) -> datetime:
    """Read the header's timestamp; refuse a shape it does not recognise."""
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise ValueError(f"unrecognised {EXPIRY_HEADER} value: {value!r}")


def _refusal(status: int, name: str) -> Verdict | None:
    """The verdict for an answer that carries no usable expiry, if it is one."""
    if status == HTTP_UNAUTHORIZED:
        return Verdict(False, f"{name} was rejected (HTTP 401): expired or revoked — renew it")
    if status == UNREACHABLE:
        return Verdict(False, f"{name} could not be checked: the API was unreachable")
    if status != HTTP_OK:
        return Verdict(False, f"{name} could not be checked: the API answered HTTP {status}")
    return None


def judge(answer: Answer, now: datetime, warn_days: int, name: str) -> Verdict:
    """Turn one API answer into a verdict about the named secret."""
    refusal = _refusal(answer.status, name)
    if refusal is not None:
        return refusal
    if answer.expiry is None:
        return Verdict(True, f"{name} is accepted and reports no expiry date")
    try:
        expires = parse_expiry(answer.expiry)
    except ValueError as exc:
        return Verdict(False, str(exc))
    days = (expires - now).total_seconds() / 86400
    when = f"{name} expires {expires:%Y-%m-%d %H:%M} UTC ({days:.1f} days from now)"
    if days <= warn_days:
        return Verdict(False, f"{when} — renew it before CI stops")
    return Verdict(True, when)


def ask(api_url: str, repo: str, token: str) -> Answer:
    """One authenticated request; the token travels only in the header."""
    if urllib.parse.urlsplit(api_url).scheme not in ("https", "http"):
        raise SystemExit(f"--api-url must be an http(s) URL, not {api_url!r}")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        f"{api_url.rstrip('/')}/repos/{repo}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked above
            return Answer(response.status, response.headers.get(EXPIRY_HEADER))
    except urllib.error.HTTPError as exc:
        return Answer(exc.code, exc.headers.get(EXPIRY_HEADER))
    except urllib.error.URLError:
        return Answer(UNREACHABLE, None)


def _report(verdict: Verdict) -> None:
    print(f"::{'notice' if verdict.ok else 'error'}::{verdict.message}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"{'✅' if verdict.ok else '❌'} {verdict.message}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-env", required=True, help="env var holding the token")
    parser.add_argument("--repo", required=True, help="owner/name the token must reach")
    parser.add_argument("--warn-days", type=int, default=14)
    parser.add_argument("--api-url", default="https://api.github.com")
    args = parser.parse_args(argv)

    token = os.environ.get(args.secret_env, "")
    if not token.strip():
        verdict = Verdict(False, f"{args.secret_env} is empty or not set")
    else:
        answer = ask(args.api_url, args.repo, token)
        verdict = judge(answer, datetime.now(UTC), args.warn_days, args.secret_env)
    _report(verdict)
    return 0 if verdict.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
