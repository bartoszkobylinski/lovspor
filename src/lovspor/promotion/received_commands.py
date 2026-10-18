"""``lovspor promote received`` — promote a text an authority sent in answer to innsyn.

ADR-0016 Amendment 1, slice I2. The same three steps as an observed artifact
(:mod:`~lovspor.promotion.commands`), over the innsyn channel's reader:

``preview``
    Read one received document, extract, identify, place and render it, and
    print the result. Writes nothing and records nothing.
``approve``
    Record the reviewer's decision on it in ``promotions.jsonl``, named by
    its :class:`~lovspor.promotion.decisions.ReceivedKey`.
``local``
    Write the approved version into a ``lovverk`` checkout with
    ``basis: "received"``, or record its hold. Never commits.

Only a document the operator classified ``regulation_text`` is read (A1);
the operator's ``answers`` is review evidence, never identity (A1-1); a scan
without a text layer is held as ``empty_text`` (A1-2). No file name,
recipient or reviewer name reaches the corpus.

The innsyn workspace comes from ``LOVSPOR_INNSYN_DIR``, the decision log from
``LOVSPOR_OBSERVATORY_ROOT``, as for every promotion. The decorated commands
only call ``*_impl`` functions (#292: mutmut skips decorated functions).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import ConfigError, PromotionRefusedError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR, InnsynWorkspace, innsyn_workspace
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import ObservatoryRoot, engine_root
from lovspor.promotion.commands import (
    HELD_EXIT_CODE,
    _Corpus,
    _Decision,
    _decision_document,
    _echo_held,
    _echo_promoted,
    _KlassVersion,
    _promoted,
    _record_held,
    _refusing,
    _standing,
    echo_preview,
    echo_unchanged,
    promote_app,
    reviewed_hash,
)
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import Decision, DecisionLog, HumanDecision, utc_text
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.innsyn_authority import received_authority
from lovspor.promotion.innsyn_source import ReceivedArtifact, read_received
from lovspor.promotion.models import Authority
from lovspor.promotion.plan import Held, prepare, require_approval, standing_on
from lovspor.promotion.received_writer import received_history, received_write_set
from lovspor.promotion.withdraw import refuse_withdrawn
from lovspor.promotion.writer import apply

received_app = typer.Typer(
    name="received",
    help="Promote a regulation text received under offentleglova (ADR-0016 A1).",
    no_args_is_help=True,
)
promote_app.add_typer(received_app)

_Request = Annotated[
    str, typer.Option("--request", help="The innsyn request id, as the ledger names it.")
]
_Document = Annotated[
    str, typer.Option("--document", help="SHA-256 of the received document, as recorded.")
]


@dataclass(frozen=True)
class ReceivedRequest:
    """One received document to promote, and where to promote it."""

    request_id: str
    document: str
    corpus: Path
    klass_version: str


@dataclass(frozen=True)
class ReceivedContext:
    """Everything one received document's promotion reads."""

    root: ObservatoryRoot
    artifact: ReceivedArtifact
    authority: Authority
    corpus: CorpusCheckout


def _workspace() -> InnsynWorkspace:
    raw = os.environ.get(ENV_INNSYN_DIR, "")
    if not raw.strip():
        raise PromotionRefusedError(f"set {ENV_INNSYN_DIR} to the innsyn workspace")
    try:
        return innsyn_workspace(Path(raw))
    except ConfigError as exc:
        raise PromotionRefusedError(f"cannot use the innsyn directory: {exc}") from exc


def _context(request: ReceivedRequest) -> ReceivedContext:
    root = _root()
    workspace = _workspace()
    artifact = read_received(workspace, request.request_id, request.document)
    authority = received_authority(workspace, artifact.key.authority_id, request.klass_version)
    corpus = CorpusCheckout(request.corpus, [engine_root(), root.path, workspace.path])
    return ReceivedContext(root, artifact, authority, corpus)


def received_preview_impl(request: ReceivedRequest) -> None:
    """Print what ``local`` would write for this document; write and record nothing."""
    context = _context(request)
    decisions = DecisionLog(context.root)
    _standing(decisions, context.artifact.key)
    prepared = prepare(context.artifact, context.authority, context.corpus)
    if isinstance(prepared, Held):
        _echo_held(prepared)
        raise typer.Exit(HELD_EXIT_CODE)
    refuse_withdrawn(decisions, None, prepared.identity.doc_id)
    received_history(context.corpus, prepared)
    echo_preview(prepared)


def received_approve_impl(request_id: str, document: str, decision: Path, now: datetime) -> None:
    """Record a human decision on one received document in the decision log."""
    root = _root()
    artifact = read_received(_workspace(), request_id, document)
    given = _decision_document(decision)
    approving = given.decision is Decision.APPROVE
    recorded = HumanDecision(
        artifact=artifact.key,
        decided_at=now,
        content_hash=reviewed_hash(artifact.payload, artifact.content_type) if approving else None,
        extractor_version=EXTRACTOR_VERSION if approving else None,
        **given.model_dump(),
    )
    decisions = DecisionLog(root)
    decisions.append(recorded)
    typer.echo(f"Recorded {recorded.decision.value} of {document} on request {request_id}")
    who = f"{recorded.decided_by} ({recorded.reviewer_role})"
    typer.echo(f"by {who} at {utc_text(now)} in {decisions.path}")


def received_local_impl(request: ReceivedRequest, now: datetime) -> None:
    """Promote one approved received document into the corpus checkout, or record why not."""
    context = _context(request)
    key = context.artifact.key
    decisions = DecisionLog(context.root)
    decision = _standing(decisions, key)
    prepared = prepare(context.artifact, context.authority, context.corpus)
    if isinstance(prepared, Held):
        _record_held(decisions, key, prepared, now)
        raise typer.Exit(HELD_EXIT_CODE)
    refuse_withdrawn(decisions, None, prepared.identity.doc_id)
    approval = standing_on(
        require_approval(decision, prepared, decisions.carried()), decisions.carries()
    )
    # Before the unchanged short-circuit: an observed document holding this text is refused.
    received_history(context.corpus, prepared)
    if prepared.unchanged:
        echo_unchanged(prepared)
        return
    writes = received_write_set(prepared, context.artifact, approval, context.corpus)
    written = apply(context.corpus, writes)
    decisions.record_outcome(_promoted(key, prepared, writes.audit, now))
    _echo_promoted(context.corpus, prepared, written)


@received_app.command("preview")
def preview(
    request: _Request, document: _Document, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Show what `promote received local` would write for one document. Writes nothing."""
    selected = ReceivedRequest(request, document, corpus, klass_version)
    _refusing(lambda: received_preview_impl(selected))


@received_app.command("approve")
def approve(request: _Request, document: _Document, decision: _Decision) -> None:
    """Record a reviewer's approve/reject/hold of one received document."""
    _refusing(lambda: received_approve_impl(request, document, decision, datetime.now(UTC)))


@received_app.command("local")
def local(
    request: _Request, document: _Document, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Write one approved received regulation into a lovverk checkout. Never commits."""
    selected = ReceivedRequest(request, document, corpus, klass_version)
    _refusing(lambda: received_local_impl(selected, datetime.now(UTC)))
