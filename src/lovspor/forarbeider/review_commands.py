"""``lovspor ops forarbeider-review``: record and inspect decisions on staged chapters.

Offline, and it permits nothing: no command here fetches, publishes, indexes or
moves a chapter, and the observation stays ``held`` whatever is decided. A group
of its own because a decision takes more options than one command may hold, so
the scope sits on the group and each act on its subcommand. The decorated
commands only call their ``_impl``, so the bodies stay inside the mutation gate
(#292). Everything printed is ids, digests, codes and paths, never chapter text,
and a refused value is named by its field, never echoed.
"""

import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, NamedTuple

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.corpus_fetch import default_corpus_path
from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.commands import aware_moment
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.review_ledger import (
    DecisionRequest,
    StoredDecision,
    current_status,
    load_history,
    record_decision,
    verify_ledger,
)
from lovspor.forarbeider.review_models import (
    ALL_ATTESTATIONS,
    Attestation,
    AttestationKind,
    ReviewReason,
    ReviewStatus,
    subject_key,
    subject_of,
)
from lovspor.forarbeider.staging import (
    checked_directory,
    checked_output,
    private_directory_exists,
    read_private,
)
from lovspor.ops.commands import ops_app

#: A saved page is at most 16 MiB; the JSON of its chapter is larger only by its escaping.
MAX_OBSERVATION_BYTES = 64 * 1024 * 1024

_ENGINE_ROOT = Path(__file__).resolve().parents[3]
_PIN = re.compile(r"(?P<key>[0-9a-f]{64})@(?P<revision>\d{1,6})")
_NOT_PERMISSION = "a review record, not permission to publish or crawl"

review_app = typer.Typer(help="Offline review decisions on staged chapters.")
ops_app.add_typer(review_app, name="forarbeider-review")


class ReviewScope(BaseModel):
    """Where decisions live and, for an act, who decides and when."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ledger: Path
    reviewer: str | None
    decided_at: str | None


class Pinned(BaseModel):
    """An observation and the subject and revision its reviewer saw."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation: Path
    expect: str


class Entry(NamedTuple):
    status: ReviewStatus
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


def _forbidden() -> tuple[Path, ...]:
    return (_ENGINE_ROOT, default_corpus_path())


def _interactive() -> bool:
    return sys.stdin.isatty()


def read_observation(raw: Path, forbidden: Sequence[Path]) -> ChapterResult:
    """A chapter result from a private file outside every repository.

    Raises:
        ForarbeiderRefusedError: an unsafe path or file, or not schema version 1.
    """
    path = checked_output(raw, forbidden)
    payload = read_private(path, MAX_OBSERVATION_BYTES)
    try:
        return ChapterResult.model_validate_json(payload)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(path)) from error


def _pinned_revision(result: ChapterResult, expect: str) -> int:
    match = _PIN.fullmatch(expect)
    if match is None:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_INVALID, "expect")
    if match["key"] != subject_key(subject_of(result)):
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_SUBJECT_MISMATCH, match["key"])
    return int(match["revision"])


def entry_of(status: ReviewStatus, because: Sequence[str]) -> Entry:
    """A reason with no reference gets an empty one, which the evidence grammar refuses."""
    pairs = [value.partition("=") for value in because]
    reasons = tuple(reason for reason, _, _ in pairs)
    return Entry(status, reasons, tuple(reference for _, _, reference in pairs))


def _reasons(values: tuple[str, ...]) -> tuple[ReviewReason, ...]:
    try:
        return tuple(sorted({ReviewReason(value) for value in values}))
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_INVALID, "reason_codes") from error


def _attestations(values: tuple[str, ...]) -> tuple[Attestation, ...]:
    grouped: dict[AttestationKind, set[str]] = {}
    for value in values:
        name, _, reference = value.partition("=")
        try:
            grouped.setdefault(AttestationKind(name), set()).add(reference)
        except ValueError as error:
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_INVALID, "attest") from error
    try:
        return tuple(
            Attestation(kind=kind, attested=True, evidence=tuple(sorted(grouped[kind])))
            for kind in sorted(grouped)
        )
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_INVALID, "attest") from error


def _record(
    scope: ReviewScope, pinned: Pinned, entry: Entry, attested: tuple[Attestation, ...]
) -> str:
    ledger = checked_directory(scope.ledger, _forbidden())
    result = read_observation(pinned.observation, _forbidden())
    request = DecisionRequest(
        subject=subject_of(result),
        reviewer=scope.reviewer,
        decided_at=aware_moment(scope.decided_at or "").isoformat(),
        status=entry.status,
        reason_codes=_reasons(entry.reasons),
        evidence=tuple(sorted(set(entry.evidence))),
        attestations=attested,
        expected_revision=_pinned_revision(result, pinned.expect),
    )
    outcome = record_decision(ledger, request)
    word = entry.status.value if outcome.written else "unchanged"
    revision = outcome.stored.decision.revision
    return f"{word} {result.id}: revision {revision} ({_NOT_PERMISSION})"


def decide_impl(scope: ReviewScope, pinned: Pinned, entry: Entry) -> str:
    """Record one decision that is not an approval and return the line that reports it.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    return _record(scope, pinned, entry, ())


def approve_impl(scope: ReviewScope, pinned: Pinned, attest: tuple[str, ...]) -> str:
    """Record an approval: every attestation, then a person at a terminal typing a phrase.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    result = read_observation(pinned.observation, _forbidden())
    _pinned_revision(result, pinned.expect)
    attested = _attestations(attest)
    held = {attestation.kind for attestation in attested}
    for kind in ALL_ATTESTATIONS:
        if kind not in held:
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_ATTESTATION_MISSING, kind.value)
    if result.text_sha256 is None:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_NOT_ELIGIBLE, result.id)
    _require_a_person(f"approve {result.id} {result.text_sha256[:12]}")
    evidence = {reference for item in attested for reference in item.evidence}
    entry = Entry(
        ReviewStatus.APPROVED,
        (ReviewReason.ATTESTATIONS_COMPLETE.value,),
        tuple(evidence),
    )
    return _record(scope, pinned, entry, attested)


def _require_a_person(phrase: str) -> None:
    """Keeps cron, CI and a pipe out. A pseudo-terminal can still get past it."""
    if not _interactive() or typer.prompt(f"Type to confirm: {phrase}") != phrase:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_NOT_INTERACTIVE, "approval")


def show_impl(scope: ReviewScope, observation: Path) -> str:
    """The binding, status and every revision of one observation, never its text.

    Raises:
        ForarbeiderRefusedError: an unsafe path, or a broken chain.
    """
    ledger = checked_directory(scope.ledger, _forbidden())
    result = read_observation(observation, _forbidden())
    subject = subject_of(result)
    history = load_history(ledger, subject)
    lines = [
        result.id,
        f"source_page_url {subject.source_page_url}",
        f"source_sha256 {subject.source_sha256}",
        f"text_sha256 {subject.text_sha256 or 'none'}",
        f"extractor {subject.extractor_version} normalizer {subject.normalizer_version}",
        f"hold_reasons {','.join(result.hold_reasons)}",
        f"status {current_status(history).value}",
        f"--expect {subject_key(subject)}@{len(history)}",
    ]
    return "\n".join([*lines, *(_revision_line(stored) for stored in history)])


def _revision_line(stored: StoredDecision) -> str:
    decision = stored.decision
    moment = decision.decided_at.isoformat().replace("+00:00", "Z")
    return (
        f"revision {decision.revision} {decision.status.value} {decision.reviewer} {moment} "
        f"{','.join(decision.reason_codes)} {','.join(decision.evidence)}"
    )


def candidates_impl(scope: ReviewScope, staging: Path) -> str:
    """One line per observation in ``staging``, with the pin its review needs.

    Raises:
        ForarbeiderRefusedError: an unsafe path, a file that is not an
            observation, or a broken chain.
    """
    ledger = checked_directory(scope.ledger, _forbidden())
    directory = checked_directory(staging, _forbidden())
    if not private_directory_exists(directory):
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(directory))
    paths = sorted(directory.glob("*.json"))
    lines = [_candidate_line(ledger, path) for path in paths]
    return "\n".join(lines) if lines else "no observations"


def _candidate_line(ledger: Path, path: Path) -> str:
    result = read_observation(path, _forbidden())
    subject = subject_of(result)
    history = load_history(ledger, subject)
    reasons = ",".join(result.hold_reasons)
    pin = f"{subject_key(subject)}@{len(history)}"
    return (
        f"{result.id} status {current_status(history).value} "
        f"revision {len(history)} reasons {reasons} --expect {pin} {path}"
    )


def verify_impl(scope: ReviewScope) -> str:
    """Every chain and the digest of its head, which an operator can keep elsewhere.

    Raises:
        ForarbeiderRefusedError: an unsafe path, or a broken chain.
    """
    chains = verify_ledger(checked_directory(scope.ledger, _forbidden()))
    lines = [_chain_line(chain) for chain in chains]
    closing = f"{len(chains)} chains verified; a cut or rebuilt chain is not detectable here"
    return "\n".join([*lines, closing])


def _chain_line(chain: tuple[StoredDecision, ...]) -> str:
    head = chain[-1]
    subject = head.decision.subject
    return (
        f"{subject_key(subject)} {subject.chapter_id} revision {head.decision.revision} "
        f"status {head.decision.status.value} head {head.sha256}"
    )


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error


_Observation = Annotated[
    Path, typer.Option("--observation", help="A private chapter result.", readable=False)
]
_Expect = Annotated[str, typer.Option("--expect", help="The <subject>@<revision> printed by show.")]
_Because = Annotated[
    list[str],
    typer.Option(
        "--because",
        help="REASON=REFERENCE, a reason code and adr:NNNN, issue:N, pr:N or sha256:HEX; "
        "may be repeated.",
    ),
]


@review_app.callback()
def review_scope(
    ctx: typer.Context,
    ledger: Annotated[
        Path, typer.Option("--ledger", help="Absolute owner-only directory outside Git.")
    ],
    reviewer: Annotated[
        str | None, typer.Option("--reviewer", help="A handle, never a name or address.")
    ] = None,
    decided_at: Annotated[
        str | None, typer.Option("--decided-at", help="ISO 8601 with offset.")
    ] = None,
) -> None:
    """Record reviewer decisions on staged chapters (issue #738, ADR-0018 PR3A).

    Offline. Permits nothing: an approval is a record of what a reviewer
    attested, not permission to publish or crawl. Exit 1 is a typed refusal,
    and nothing is written.
    """
    ctx.obj = ReviewScope(ledger=ledger, reviewer=reviewer, decided_at=decided_at)


@review_app.command("candidates")
def candidates(
    ctx: typer.Context,
    staging: Annotated[Path, typer.Option("--staging", help="Directory of chapter results.")],
) -> None:
    """List staged chapters with their status and the pin a decision needs."""
    _echo(lambda: candidates_impl(ctx.obj, staging))


@review_app.command("show")
def show(ctx: typer.Context, observation: _Observation) -> None:
    """Show what one observation binds and every revision decided on it."""
    _echo(lambda: show_impl(ctx.obj, observation))


@review_app.command("verify")
def verify(ctx: typer.Context) -> None:
    """Verify every chain and print the digest of each head."""
    _echo(lambda: verify_impl(ctx.obj))


@review_app.command("hold")
def hold(ctx: typer.Context, observation: _Observation, expect: _Expect, because: _Because) -> None:
    """Hold a chapter: it stays unapproved, with the reasons recorded."""
    _decide(ctx.obj, Pinned(observation=observation, expect=expect), ReviewStatus.HELD, because)


@review_app.command("reject")
def reject(
    ctx: typer.Context, observation: _Observation, expect: _Expect, because: _Because
) -> None:
    """Reject a chapter. Only reopen leads back from here."""
    _decide(ctx.obj, Pinned(observation=observation, expect=expect), ReviewStatus.REJECTED, because)


@review_app.command("reopen")
def reopen(
    ctx: typer.Context, observation: _Observation, expect: _Expect, because: _Because
) -> None:
    """Return a chapter to pending, which withdraws any earlier approval."""
    _decide(ctx.obj, Pinned(observation=observation, expect=expect), ReviewStatus.PENDING, because)


def _decide(scope: ReviewScope, target: Pinned, status: ReviewStatus, because: list[str]) -> None:
    _echo(lambda: decide_impl(scope, target, entry_of(status, because)))


@review_app.command("approve")
def approve(
    ctx: typer.Context,
    observation: _Observation,
    expect: _Expect,
    attest: Annotated[
        list[str],
        typer.Option("--attest", help="KIND=REFERENCE for all four kinds; may be repeated."),
    ],
) -> None:
    """Record an approval at a terminal. This is NOT permission to publish or crawl."""
    target = Pinned(observation=observation, expect=expect)
    _echo(lambda: approve_impl(ctx.obj, target, tuple(attest)))
