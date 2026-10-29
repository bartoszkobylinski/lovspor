"""``lovspor promote received refresh`` — re-render an unchanged received version (#645).

A received version promoted before the renderer learned a front-matter field
(``status: "repealed"``, #639) keeps its old rendering: its ``content_hash``
has not moved, so ``promote received local`` stops at ``Unchanged``, and
``promote migrate`` never rewrites the Markdown (#643). This command
re-renders every current ``basis: "received"`` document from its source in
the innsyn workspace, the one its front matter names (``innsyn_request`` and
``source_sha256``), and rewrites the Markdown only where the bytes differ.

It is a re-render, never a new version: the same ``version``,
``content_hash`` and provenance (``received_at``, the request, the source,
its form), under the KLASS vintage the rendering was published with. The
re-rendering is refused, not written, when its body or any of those fields
would change: that is a new version, and belongs to ``promote received
local``. Like ``local``, it needs the owner's standing approval of exactly
this text at the running extractor. Only the Markdown is written: the
manifest, the observations and audit file and the evidence sidecar describe
the same version and stay as they are, and nothing is recorded in the
decision log. It never commits; it prints the ``migration(lokal-forskrift):
re-render …`` commit to make, which the history derivation reads as no event.

``--dry-run`` prints the diff and writes nothing. It also shows the diff of a
document whose approval was given at an earlier extractor, named as needing
re-approval and exiting 1, so the owner reads what approving again covers
(#719); the write refuses it until then. A refused document is
reported on its own line and the exit status is 1; the others are still
refreshed. The decorated command only calls :func:`refresh_impl` (#292).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import BaseModel, ConfigDict

from lovspor.errors import PromotionError, PromotionRefusedError
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import engine_root
from lovspor.promotion.commands import _Corpus, _echo_commit, _refusing, _standing
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import Carried, DecisionLog, HumanDecision
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.innsyn_authority import received_authority
from lovspor.promotion.innsyn_source import ReceivedArtifact, read_received
from lovspor.promotion.migrate import (
    Published,
    front_matter,
    planned_diff,
    published,
    published_klass_version,
)
from lovspor.promotion.plan import (
    APPROVAL_ELSEWHERE,
    Held,
    Prepared,
    awaits_reapproval,
    prepare,
    render_prepared,
    require_approval,
)
from lovspor.promotion.received_commands import _workspace, received_app
from lovspor.promotion.withdraw import refuse_withdrawn
from lovspor.promotion.writer import apply_files, refuse_nlod

REFRESH_SUBJECT = "migration(lokal-forskrift): re-render {authority_id}/{slug} front matter"
REFRESH_BATCH_SUBJECT = (
    "migration(lokal-forskrift): re-render front matter of {count} received documents"
)

#: The front matter that names the version and its provenance: a refresh never moves it.
KEPT_KEYS: Final = frozenset(
    {
        "id",
        "slug",
        "type",
        "ref_id",
        "authority",
        "version",
        "content_hash",
        "received_at",
        "innsyn_request",
        "innsyn_saksnummer",
        "received_form",
        "observed_at_first",
        "source_url",
        "source_sha256",
        "source_provider",
        "source_license",
        "basis",
        "asserted",
        "language",
    }
)
_RECEIVED = json.dumps("received")


class Refresh(BaseModel):
    """One received version's Markdown, re-rendered with its version and provenance kept."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    authority_id: str
    slug: str
    version: int
    markdown_path: str
    markdown: str
    #: Shown by a dry run only: its approval stands at an earlier extractor (#719).
    needs_reapproval: bool = False

    @property
    def commit_subject(self) -> str:
        return REFRESH_SUBJECT.format(authority_id=self.authority_id, slug=self.slug)


@dataclass(frozen=True)
class RefreshInputs:
    """The innsyn workspace, the decision log and the checkout one refresh run reads.

    ``preview`` is a dry run: it may show a rendering whose approval was given
    at an earlier extractor (#719), which a write refuses.
    """

    workspace: InnsynWorkspace
    decisions: DecisionLog
    corpus: CorpusCheckout
    preview: bool = False


@dataclass(frozen=True)
class RefreshOutcome:
    """What the run decided per document: refreshes, and refusals by id."""

    refreshes: list[Refresh]
    refusals: dict[str, str]


def is_received(document: Published) -> bool:
    """True when the published rendering says it was received under offentleglova."""
    return front_matter(document.markdown).get("basis") == _RECEIVED


def plan_refresh(inputs: RefreshInputs, document: Published) -> Refresh | None:
    """The document's re-rendering, ``None`` when it is byte-identical, or a refusal."""
    artifact = named_source(inputs.workspace, document)
    klass_version = published_klass_version(document)
    authority = received_authority(inputs.workspace, document.record.authority_id, klass_version)
    decision = _standing(inputs.decisions, artifact.key)
    refuse_withdrawn(inputs.decisions, None, document.doc_id)
    prepared = _same_version(document, prepare(artifact, authority, inputs.corpus))
    carried = inputs.decisions.carried()
    reapproval = previewed_approval(decision, prepared, carried, preview=inputs.preview)
    return refresh_of(document, render_prepared(prepared, artifact), needs_reapproval=reapproval)


def previewed_approval(
    decision: HumanDecision | None, prepared: Prepared, carried: Carried, *, preview: bool
) -> bool:
    """True when only a preview goes on, pending a re-approval; refuses anything else unapproved."""
    if preview and awaits_reapproval(decision, prepared, carried):
        return True
    require_approval(decision, prepared, carried)
    return False


def refresh_of(document: Published, markdown: str, *, needs_reapproval: bool) -> Refresh | None:
    """``markdown`` as the document's refresh, ``None`` when byte-identical, or a refusal.

    A byte-identical rendering awaiting re-approval has no diff to show:
    ``promote migrate`` carries its approval, so it keeps the approval refusal.
    """
    if markdown == document.markdown:
        if needs_reapproval:
            raise PromotionRefusedError(APPROVAL_ELSEWHERE)
        return None
    refuse_beyond_front_matter(document, markdown)
    return Refresh(
        doc_id=document.doc_id,
        authority_id=document.record.authority_id,
        slug=document.record.slug,
        version=document.record.version,
        markdown_path=document.record.markdown_path,
        markdown=markdown,
        needs_reapproval=needs_reapproval,
    )


def refresh_files(refreshes: list[Refresh]) -> dict[str, str]:
    """Every file the refreshes write: their Markdown, refused if any would mention NLOD."""
    files = {refresh.markdown_path: refresh.markdown for refresh in refreshes}
    refuse_nlod(files)
    return files


def refresh_subject(refreshes: list[Refresh]) -> str:
    """The commit subject: the document's own, or a count when there are several."""
    if len(refreshes) == 1:
        return refreshes[0].commit_subject
    return REFRESH_BATCH_SUBJECT.format(count=len(refreshes))


def named_source(workspace: InnsynWorkspace, document: Published) -> ReceivedArtifact:
    """The received document the published rendering names as its source."""
    front = front_matter(document.markdown)
    try:
        request_id = json.loads(front["innsyn_request"])
        sha256 = json.loads(front["source_sha256"])
    except (KeyError, ValueError) as exc:
        msg = f"its front matter does not name its received source ({type(exc).__name__})"
        raise PromotionRefusedError(msg) from exc
    return read_received(workspace, request_id, sha256)


def _same_version(document: Published, prepared: Held | Prepared) -> Prepared:
    """``prepared``, refused unless it is the version the checkout already holds."""
    if isinstance(prepared, Held):
        msg = f"its source is now held at {prepared.stage} ({prepared.reason}: {prepared.detail})"
        raise PromotionRefusedError(msg)
    if prepared.identity.doc_id != document.doc_id or not prepared.unchanged:
        msg = (
            f"its source now reads as {prepared.identity.doc_id} v{prepared.version} (content_hash "
            f"{prepared.identity.content_hash[:12]}…). That is a new version, not a re-render: "
            "approve it and run `lovspor promote received local`"
        )
        raise PromotionRefusedError(msg)
    return prepared


def refuse_beyond_front_matter(document: Published, markdown: str) -> None:
    """Refuse a re-rendering that moves the body, the version or its provenance."""
    if _body(markdown) != _body(document.markdown):
        msg = "its body would change though its text does not; a refresh rewrites front matter only"
        raise PromotionRefusedError(msg)
    old, new = front_matter(document.markdown), front_matter(markdown)
    moved = sorted(key for key in KEPT_KEYS if old.get(key) != new.get(key))
    if moved:
        msg = f"re-rendering would change {', '.join(moved)}; a refresh keeps the version as it is"
        raise PromotionRefusedError(msg)


def _body(markdown: str) -> str:
    return markdown.removeprefix("---\n").partition("\n---\n")[2]


@dataclass(frozen=True)
class RefreshRequest:
    """Which checkout to refresh, and whether to write."""

    corpus: Path
    dry_run: bool


def refresh_impl(request: RefreshRequest) -> None:
    """Re-render every current received document whose rendering is stale. Never commits."""
    root = _root()
    workspace = _workspace()
    corpus = CorpusCheckout(request.corpus, [engine_root(), root.path, workspace.path])
    inputs = RefreshInputs(workspace, DecisionLog(root), corpus, preview=request.dry_run)
    report_outcome(corpus, _plan_all(inputs), dry_run=request.dry_run)


def report_outcome(corpus: CorpusCheckout, outcome: RefreshOutcome, *, dry_run: bool) -> None:
    """Print the refusals, then the diff (dry run) or the writes; exit 1 when one is held back."""
    for doc_id, reason in outcome.refusals.items():
        typer.echo(f"Refused: {doc_id}: {reason}", err=True)
    if not outcome.refreshes:
        if not outcome.refusals:
            typer.echo("Nothing to refresh; nothing written, nothing to commit.")
    elif dry_run:
        preview_refreshes(corpus, outcome.refreshes)
    else:
        write_refreshes(corpus, outcome.refreshes)
    if outcome.refusals or any(refresh.needs_reapproval for refresh in outcome.refreshes):
        raise typer.Exit(1)


def preview_refreshes(corpus: CorpusCheckout, refreshes: list[Refresh]) -> None:
    """The dry run: each re-approval it awaits, the diff, and how many wait (#719)."""
    waiting = [refresh.doc_id for refresh in refreshes if refresh.needs_reapproval]
    for doc_id in waiting:
        typer.echo(
            f"{doc_id}: needs re-approval at v{EXTRACTOR_VERSION}: its approval was given at an "
            "earlier extractor; the diff below is what approving it again covers"
        )
    diff = planned_diff(corpus, refresh_files(refreshes))
    typer.echo(diff + "Dry run: nothing written.", nl=not waiting)
    if waiting:
        typer.echo(
            f" {len(waiting)} document(s) need re-approval at v{EXTRACTOR_VERSION} "
            "before refresh writes them."
        )


def _plan_all(inputs: RefreshInputs) -> RefreshOutcome:
    outcome = RefreshOutcome(refreshes=[], refusals={})
    for doc_id, record in sorted(inputs.corpus.local_manifest().documents.items()):
        if record.status != "current":
            continue
        try:
            document = published(inputs.corpus, doc_id, record)
            if not is_received(document):
                continue
            refresh = plan_refresh(inputs, document)
        except PromotionError as exc:
            outcome.refusals[doc_id] = str(exc)
            continue
        if refresh is None:
            typer.echo(f"{doc_id}: unchanged; its rendering is current")
        else:
            outcome.refreshes.append(refresh)
    return outcome


def write_refreshes(corpus: CorpusCheckout, refreshes: list[Refresh]) -> None:
    if any(refresh.needs_reapproval for refresh in refreshes):
        raise PromotionRefusedError(APPROVAL_ELSEWHERE)
    written = apply_files(corpus, refresh_files(refreshes))
    for refresh in refreshes:
        typer.echo(
            f"{refresh.doc_id} v{refresh.version}: front matter re-rendered; "
            "version, content_hash and provenance unchanged"
        )
    for path in written:
        typer.echo(f"wrote {path}")
    _echo_commit(corpus, refresh_subject(refreshes))


@received_app.command("refresh")
def refresh(
    corpus: _Corpus,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the diff it would write; write nothing.")
    ] = False,
) -> None:
    """Re-render the front matter of unchanged received versions. Never commits."""
    _refusing(lambda: refresh_impl(RefreshRequest(corpus, dry_run)))
