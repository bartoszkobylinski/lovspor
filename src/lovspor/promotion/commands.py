"""``lovspor promote`` — the operator's way to put one local regulation into ``lovverk``.

ADR-0016 slice S3. Four commands, in the order an operator runs them:

``preview``
    Extract, identify, place and render one archived artifact and print the
    result. Writes nothing and records nothing: it is what the reviewer reads.
``approve``
    Record the reviewer's decision — a JSON document, ``approve``, ``reject``
    or ``hold`` — in ``promotions.jsonl`` beside the archive. An approval
    names the text it was given for, by content hash and extractor version.
``local``
    Write the approved version into a ``lovverk`` checkout: the document, its
    observations and audit file, and the local manifest. A hold writes
    nothing to the corpus and is recorded in the decision log; a rerun with
    the same inputs writes nothing at all. It never commits: it prints the
    commit to make, at promotion time.
``history``
    After that commit, derive ``history/<slug>.json`` from git.

The archive root comes from ``LOVSPOR_OBSERVATORY_ROOT`` (ADR-0010 §5); the
corpus from ``--corpus``, which must be a separate ``lovverk`` checkout. The
decorated commands only call ``*_impl`` functions, so their bodies stay in
the mutation gate (#292: mutmut skips decorated functions).
"""

from __future__ import annotations

import contextlib
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import (
    ConfigError,
    ParseError,
    PromotionError,
    PromotionRefusedError,
    StorageBoundaryError,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry import read_registry, registry_path
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import ObservatoryRoot, engine_root, observatory_root
from lovspor.promotion.archive import Fetch, authority_fetches, locate, read_artifact
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    ArtifactKey,
    Decision,
    DecisionDocument,
    DecisionLog,
    HeldRecord,
    HumanDecision,
    PromotedRecord,
    PromotionAudit,
    utc_text,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from lovspor.promotion.identity import content_hash
from lovspor.promotion.local_history import HISTORY_SUBJECT, derive_local_history
from lovspor.promotion.models import Authority, AuthorityType, HeldExtraction
from lovspor.promotion.personal_data import screen_personal_data
from lovspor.promotion.plan import Held, Prepared, prepare, require_approval
from lovspor.promotion.writer import apply, write_set

#: Exit status of a run that stopped at a hold: not a failure of the command,
#: and not a promotion either, so a script can tell the three apart.
HELD_EXIT_CODE = 3

promote_app = typer.Typer(
    name="promote",
    help="Promote observed local regulations into lovverk (ADR-0016).",
    no_args_is_help=True,
)

_Authority = Annotated[
    str, typer.Option("--authority", help="KLASS code of the authority, as the registry holds it.")
]
_Artifact = Annotated[
    str,
    typer.Option("--artifact", help="SHA-256 of the archived bytes, or the URL that served them."),
]
_Corpus = Annotated[
    Path, typer.Option("--corpus", help="Absolute path to a lovverk checkout (never this repo).")
]
_KlassVersion = Annotated[
    str,
    typer.Option("--klass-version", help="SSB KLASS vintage the authority code is read under."),
]


@dataclass(frozen=True)
class Request:
    """One artifact to promote, and where to promote it."""

    authority_id: str
    artifact: str
    corpus: Path
    klass_version: str


@dataclass(frozen=True)
class _Context:
    root: ObservatoryRoot
    log: ObservationLog
    fetches: tuple[Fetch, ...]
    key: ArtifactKey
    corpus: CorpusCheckout
    authority: Authority


def _refusing(action: Callable[[], None]) -> None:
    """Run ``action``; a refusal reaches the operator as one line, not a traceback."""
    try:
        action()
    except PromotionError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _context(request: Request) -> _Context:
    root = _root()
    log = ObservationLog(root)
    fetches = authority_fetches(log, request.authority_id)
    key = locate(fetches, request.authority_id, request.artifact)
    corpus = CorpusCheckout(request.corpus, [engine_root(), root.path])
    authority = _authority(root, request.authority_id, request.klass_version)
    return _Context(root, log, fetches, key, corpus, authority)


def _authority(root: ObservatoryRoot, authority_id: str, klass_version: str) -> Authority:
    """The authority block, from the source register — never typed in by hand."""
    try:
        record = read_registry(registry_path(root)).sources.get(authority_id)
    except (OSError, ParseError) as exc:
        msg = f"cannot read the source register: {exc}"
        raise PromotionRefusedError(msg) from exc
    if record is None:
        msg = f"{authority_id} is not in the source register"
        raise PromotionRefusedError(msg)
    try:
        return Authority(
            id=authority_id,
            type=AuthorityType(record.authority_type),
            name=record.name,
            klass_version=klass_version,
        )
    except ValidationError as exc:
        msg = f"the authority block does not validate: {exc.errors()[0]['msg']}"
        raise PromotionRefusedError(msg) from exc


def _decision_document(path: Path) -> DecisionDocument:
    try:
        document = DecisionDocument.model_validate_json(path.read_bytes())
    except OSError as exc:
        msg = f"cannot read the decision document at {path}: {exc}"
        raise PromotionRefusedError(msg) from exc
    except ValidationError as exc:
        msg = f"the decision document does not validate: {exc}"
        raise PromotionRefusedError(msg) from exc
    if screen_personal_data(document.reason):
        msg = "the reason carries personal data; it is published with the audit record"
        raise PromotionRefusedError(msg)
    return document


def _reviewed_text(log: ObservationLog, fetches: tuple[Fetch, ...], key: ArtifactKey) -> str:
    """The content hash of the text an approval is given for; a held text cannot be approved."""
    artifact = read_artifact(log, fetches, key, None)
    extracted = extract_regulation(artifact.payload, artifact.content_type)
    if isinstance(extracted, HeldExtraction):
        msg = f"nothing to approve: the text is held ({extracted.reason.value}: {extracted.detail})"
        raise PromotionRefusedError(msg)
    return content_hash(extracted.regulation.full_text)


def approve_impl(authority_id: str, artifact: str, document_path: Path, now: datetime) -> None:
    """Record a human decision on one artifact in the decision log."""
    root = _root()
    log = ObservationLog(root)
    fetches = authority_fetches(log, authority_id)
    key = locate(fetches, authority_id, artifact)
    document = _decision_document(document_path)
    approving = document.decision is Decision.APPROVE
    decision = HumanDecision(
        artifact=key,
        decision=document.decision,
        decided_by=document.decided_by,
        decided_at=now,
        reason=document.reason,
        content_hash=_reviewed_text(log, fetches, key) if approving else None,
        extractor_version=EXTRACTOR_VERSION if approving else None,
        classifier=document.classifier,
    )
    decisions = DecisionLog(root)
    decisions.append(decision)
    typer.echo(f"Recorded {decision.decision.value} of {key.sha256} at {key.source_url}")
    typer.echo(f"by {decision.decided_by} at {utc_text(now)} in {decisions.path}")


def preview_impl(request: Request) -> None:
    """Print what ``local`` would write for this artifact; write and record nothing."""
    context = _context(request)
    artifact = read_artifact(context.log, context.fetches, context.key, None)
    prepared = prepare(artifact, context.authority, context.corpus)
    if isinstance(prepared, Held):
        _echo_held(prepared)
        raise typer.Exit(HELD_EXIT_CODE)
    typer.echo(f"id: {prepared.identity.doc_id}  version: {prepared.version}")
    typer.echo(f"path: {prepared.markdown_path}")
    typer.echo(f"content_hash: {prepared.identity.content_hash}")
    if prepared.unchanged:
        typer.echo("unchanged: the corpus already holds this version")
        return
    typer.echo("")
    typer.echo(prepared.markdown, nl=False)


def local_impl(request: Request, now: datetime) -> None:
    """Promote one approved artifact into the corpus checkout, or record why not."""
    context = _context(request)
    decisions = DecisionLog(context.root)
    decision = decisions.latest_decision(context.key)
    through = decision.decided_at if decision is not None else None
    artifact = read_artifact(context.log, context.fetches, context.key, through)
    prepared = prepare(artifact, context.authority, context.corpus)
    if isinstance(prepared, Held):
        _record_held(decisions, context.key, prepared, now)
        raise typer.Exit(HELD_EXIT_CODE)
    approval = require_approval(decision, prepared)
    if prepared.unchanged:
        typer.echo(f"Unchanged: {prepared.identity.doc_id} v{prepared.version} is already at")
        typer.echo(f"{prepared.markdown_path}; nothing written, nothing to commit.")
        return
    writes = write_set(prepared, artifact, approval, context.corpus)
    written = apply(context.corpus, writes)
    decisions.record_outcome(_promoted(context.key, prepared, writes.audit, now))
    _echo_promoted(context.corpus, prepared, written)


def _promoted(
    key: ArtifactKey, prepared: Prepared, audit: PromotionAudit, now: datetime
) -> PromotedRecord:
    return PromotedRecord(
        artifact=key,
        recorded_at=now,
        doc_id=prepared.identity.doc_id,
        version=prepared.version,
        markdown_path=prepared.markdown_path,
        content_hash=prepared.identity.content_hash,
        commit_subject=prepared.commit_subject,
        audit=audit,
    )


def history_impl(corpus_path: Path) -> None:
    """Derive ``history/<slug>.json`` for every current local document from git."""
    forbidden = [engine_root()]
    with contextlib.suppress(ConfigError, StorageBoundaryError):
        forbidden.append(observatory_root().path)
    corpus = CorpusCheckout(corpus_path, forbidden)
    written = derive_local_history(corpus)
    if not written:
        typer.echo("History is current; nothing written, nothing to commit.")
        return
    for path in written:
        typer.echo(f"wrote {path}")
    subject = HISTORY_SUBJECT.format(count=len(written))
    _echo_commit(corpus, subject)


def _record_held(decisions: DecisionLog, key: ArtifactKey, held: Held, now: datetime) -> None:
    record = HeldRecord(
        artifact=key,
        recorded_at=now,
        stage=held.stage,
        reason=held.reason,
        detail=held.detail,
        personal_data=held.personal_data,
    )
    decisions.record_outcome(record)
    _echo_held(held)
    typer.echo(f"Nothing was written to the corpus. The hold is recorded in {decisions.path}")


def _echo_held(held: Held) -> None:
    typer.echo(f"Held at {held.stage}: {held.reason} - {held.detail}")
    for hit in held.personal_data:
        typer.echo(f"  personal data: {hit.kind.value} on line {hit.line}")


def _echo_promoted(corpus: CorpusCheckout, prepared: Prepared, written: tuple[str, ...]) -> None:
    typer.echo(
        f"Promoted {prepared.identity.doc_id} v{prepared.version} -> {prepared.markdown_path}"
    )
    for path in written:
        typer.echo(f"wrote {path}")
    _echo_commit(corpus, prepared.commit_subject)
    typer.echo(
        f"Then derive its history: lovspor promote history --corpus {shlex.quote(str(corpus.path))}"
    )


def _echo_commit(corpus: CorpusCheckout, subject: str) -> None:
    where = shlex.quote(str(corpus.path))
    typer.echo("Nothing is committed. Commit now, at promotion time (never backdated):")
    typer.echo(f"  git -C {where} add -- lokale-forskrifter")
    typer.echo(f"  git -C {where} commit -m {shlex.quote(subject)}")


@promote_app.command("preview")
def preview(
    authority: _Authority, artifact: _Artifact, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Show what `promote local` would write for one artifact. Writes nothing."""
    request = Request(authority, artifact, corpus, klass_version)
    _refusing(lambda: preview_impl(request))


@promote_app.command("approve")
def approve(
    authority: _Authority,
    artifact: _Artifact,
    decision: Annotated[
        Path,
        typer.Option("--decision", help="JSON: decision, decided_by, reason[, classifier]."),
    ],
) -> None:
    """Record a reviewer's approve/reject/hold of one artifact in the decision log."""
    _refusing(lambda: approve_impl(authority, artifact, decision, datetime.now(UTC)))


@promote_app.command("local")
def local(
    authority: _Authority, artifact: _Artifact, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Write one approved local regulation into a lovverk checkout. Never commits."""
    request = Request(authority, artifact, corpus, klass_version)
    _refusing(lambda: local_impl(request, datetime.now(UTC)))


@promote_app.command("history")
def history(corpus: _Corpus) -> None:
    """Derive history/<slug>.json for the local dataset from the checkout's git log."""
    _refusing(lambda: history_impl(corpus))
