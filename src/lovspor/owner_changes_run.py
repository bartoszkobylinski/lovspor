"""The sync's owner-change pass: read the pin file, apply each pin, write a report.

Called by the ``Apply pinned owner changes`` step of ``sync.yml`` on the
lovverk clone, after the temporal gate epoch and before the sync. The rules
live in :mod:`lovspor.owner_changes_apply`; this module adds what needs the
remote: the pull-request head, the attempt ref, and the README refresh.

A refusal never fails the run: the pin is skipped and reported. A pin file
that breaks the grammar applies no pin and is reported. An empty file makes
no git call at all, so the sync behaves exactly as it did before. Only a git
failure that leaves the clone in an unknown state raises, which fails the job
before anything is pushed.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lovspor.corpus_readme import README_NAME, ReadmeOutcome, refresh_readme
from lovspor.errors import LovsporError
from lovspor.owner_changes import Pin, PinFileError, load_pin_file
from lovspor.owner_changes_apply import (
    Applied,
    PinRefusedError,
    changed_since_pin,
    git_bytes,
    git_ok,
    merge_pin,
    plan,
)

ATTEMPT_NAMESPACE = "refs/owner-changes"
README_MESSAGE = "readme: corpus-status block after owner changes"


@dataclass
class Report:
    """What the pass did, as the later steps read it back."""

    file_problems: list[str] = field(default_factory=list)
    applied: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)
    refused: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class RunContext:
    """Where the pins come from, for the merge message."""

    lovspor_sha: str
    run_id: str


def attempt_ref(pin: Pin) -> str:
    """The ref that records one attempt of one pin on lovverk."""
    return f"{ATTEMPT_NAMESPACE}/{pin.commit}/{pin.attempt}"


def apply_pins(repo: Path, pin_file: Path, context: RunContext) -> Report:
    """Apply every pin in ``pin_file`` to ``repo`` and say what happened."""
    report = Report()
    try:
        pins = load_pin_file(pin_file)
    except PinFileError as exc:
        report.file_problems = exc.problems
        return report
    if not pins:
        return report
    _require_main(repo)
    applied = [entry for pin in pins if (entry := _apply_one(repo, pin, context, report))]
    if applied:
        _refresh_readme(repo)
    return report


def check_applied(repo: Path, report_file: Path) -> list[str]:
    """Paths of pins applied in this run that the sync changed afterwards."""
    data = json.loads(report_file.read_text(encoding="utf-8"))
    applied = [
        Applied(Pin(**entry["pin"]), str(entry["base"]), str(entry["merge"]))
        for entry in data["applied"]
    ]
    return changed_since_pin(repo, applied)


def write_report(report: Report, path: Path) -> None:
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _apply_one(repo: Path, pin: Pin, context: RunContext, report: Report) -> Applied | None:
    try:
        base = plan(repo, pin, _pr_head(repo, pin), _attempt_exists(repo, pin))
        if base is None:
            report.skipped.append({"pin": asdict(pin), "reason": "already on main"})
            return None
        _record_attempt(repo, pin)
        applied = merge_pin(repo, pin, base, _message(pin, context))
    except PinRefusedError as exc:
        report.refused.append({"pin": asdict(pin), "code": exc.code, "detail": str(exc)})
        return None
    report.applied.append(
        {"pin": asdict(pin), "base": applied.base, "merge": applied.merge, "summary": _name(pin)}
    )
    return applied


def _name(pin: Pin) -> str:
    return f"lovverk#{pin.pull_request} {pin.commit} attempt {pin.attempt}"


def _message(pin: Pin, context: RunContext) -> str:
    return (
        f"owner-change: {_name(pin)} "
        f"(pinned in lovspor {context.lovspor_sha}, run {context.run_id})"
    )


def _require_main(repo: Path) -> None:
    try:
        branch = git_bytes(repo, "symbolic-ref", "-q", "HEAD").decode().strip()
    except LovsporError:
        branch = ""
    if branch != "refs/heads/main":
        raise LovsporError(f"owner changes apply only on main, not {branch or 'a detached HEAD'}")


def _pr_head(repo: Path, pin: Pin) -> str:
    if not git_ok(repo, "fetch", "-q", "origin", f"refs/pull/{pin.pull_request}/head"):
        raise PinRefusedError(pin, "pr-unavailable", "could not fetch the pull request head")
    return git_bytes(repo, "rev-parse", "FETCH_HEAD").decode().strip()


def _attempt_exists(repo: Path, pin: Pin) -> bool:
    try:
        listed = git_bytes(repo, "ls-remote", "origin", attempt_ref(pin))
    except LovsporError as exc:
        raise PinRefusedError(pin, "remote-unavailable", "could not list attempt refs") from exc
    return bool(listed.strip())


def _record_attempt(repo: Path, pin: Pin) -> None:
    """Create the attempt ref, create-only on the server; refuse unless it is new."""
    ref = attempt_ref(pin)
    lease = f"--force-with-lease={ref}:"
    try:
        out = git_bytes(repo, "push", "--porcelain", lease, "origin", f"{pin.commit}:{ref}")
    except LovsporError as exc:
        raise PinRefusedError(pin, "attempt-not-recorded", "the attempt ref push failed") from exc
    expected = f"*\t{pin.commit}:{ref}\t[new reference]"
    if expected not in out.decode(errors="replace").splitlines():
        raise PinRefusedError(pin, "attempt-not-recorded", "the attempt ref was not new")


def _refresh_readme(repo: Path) -> None:
    if refresh_readme(repo) is ReadmeOutcome.WRITTEN:
        git_bytes(repo, "add", "--", README_NAME)
        git_bytes(repo, "commit", "-q", "-m", README_MESSAGE, "--", README_NAME)
