"""``lovspor promote refresh``: re-render the front matter of unchanged versions (#595).

An extractor bump that changes what a published document's front matter
says — a resolved date, a dropped conflicting statement — but not its text
leaves no supported path to bring ``lovverk`` up to date: ``local`` stops at
``Unchanged`` (same ``content_hash``), ``backfill`` writes new versions only,
and ``migrate`` refuses a rendering that would change (#588). ``promote
received refresh`` (#645) closes that gap for received documents; this
command closes it for every current document of the checkout, whichever
channel it was promoted through.

An observed document (``basis: "observed"``) is re-read from the archived
capture its front matter names (``source_url``, ``source_sha256``) and
rendered with the provenance it was published with; a received one goes
through :func:`~.received_refresh.plan_refresh`, so it needs the innsyn
workspace and is refused, not skipped, without it. The terms are
``received refresh``'s: the running extractor must read the same id and
text, the owner's standing approval of exactly that text at the running
extractor must exist, the body and every version or provenance field must
be byte-identical, and only the Markdown is written. It never commits; it
prints the ``migration(lokal-forskrift): re-render …`` commit, which the
history derivation reads as no event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import PromotionError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR, InnsynWorkspace
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import engine_root
from lovspor.promotion.archive import read_artifact
from lovspor.promotion.commands import _authority, _Corpus, _refusing, promote_app
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import DecisionLog
from lovspor.promotion.extract import extract_regulation
from lovspor.promotion.migrate import (
    MigrationInputs,
    Published,
    _published_source,
    _source_key,
    planned_diff,
    published,
    published_klass_version,
    render_published,
    same_text,
)
from lovspor.promotion.migrate_commands import _optional_workspace
from lovspor.promotion.plan import require_approval
from lovspor.promotion.received_refresh import (
    Refresh,
    RefreshInputs,
    RefreshOutcome,
    is_received,
    plan_refresh,
    refresh_files,
    refuse_beyond_front_matter,
    write_refreshes,
)
from lovspor.promotion.withdraw import refuse_withdrawn


def plan_observed_refresh(inputs: MigrationInputs, document: Published) -> Refresh | None:
    """The observed document's re-rendering, ``None`` when byte-identical, or a refusal."""
    key = _source_key(document)
    refuse_withdrawn(inputs.decisions, key, document.doc_id)
    artifact = read_artifact(inputs.log, inputs.fetches(key.authority_id), key, None)
    klass_version = published_klass_version(document)
    authority = _authority(inputs.root, document.record.authority_id, klass_version)
    extracted = extract_regulation(artifact.payload, artifact.content_type)
    prepared = same_text(inputs.corpus, document, extracted, authority)
    require_approval(inputs.decisions.latest_decision(key), prepared, inputs.decisions.carried())
    markdown = render_published(prepared, _published_source(document))
    if markdown == document.markdown:
        return None
    refuse_beyond_front_matter(document, markdown)
    return Refresh(
        doc_id=document.doc_id,
        authority_id=document.record.authority_id,
        slug=document.record.slug,
        version=document.record.version,
        markdown_path=document.record.markdown_path,
        markdown=markdown,
    )


@dataclass(frozen=True)
class _Run:
    inputs: MigrationInputs
    workspace: InnsynWorkspace | None

    def plan(self, document: Published) -> Refresh | None:
        if not is_received(document):
            return plan_observed_refresh(self.inputs, document)
        if self.workspace is None:
            msg = f"a received document is re-read from the innsyn workspace; set {ENV_INNSYN_DIR}"
            raise PromotionError(msg)
        received = RefreshInputs(self.workspace, self.inputs.decisions, self.inputs.corpus)
        return plan_refresh(received, document)


def _plan_all(run: _Run) -> RefreshOutcome:
    outcome = RefreshOutcome(refreshes=[], refusals={})
    for doc_id, record in sorted(run.inputs.corpus.local_manifest().documents.items()):
        if record.status != "current":
            continue
        try:
            refresh = run.plan(published(run.inputs.corpus, doc_id, record))
        except PromotionError as exc:
            outcome.refusals[doc_id] = str(exc)
            continue
        if refresh is None:
            typer.echo(f"{doc_id}: unchanged; its rendering is current")
        else:
            outcome.refreshes.append(refresh)
    return outcome


@dataclass(frozen=True)
class RefreshAllRequest:
    """Which checkout to refresh, and whether to write."""

    corpus: Path
    dry_run: bool


def refresh_all_impl(request: RefreshAllRequest, now: datetime) -> None:
    """Re-render every current document whose front matter is stale. Never commits."""
    root = _root()
    workspace = _optional_workspace()
    forbidden = [engine_root(), root.path, *([workspace.path] if workspace else [])]
    corpus = CorpusCheckout(request.corpus, forbidden)
    inputs = MigrationInputs(root, DecisionLog(root), corpus, now)
    outcome = _plan_all(_Run(inputs, workspace))
    for doc_id, reason in outcome.refusals.items():
        typer.echo(f"Refused: {doc_id}: {reason}", err=True)
    if not outcome.refreshes:
        if not outcome.refusals:
            typer.echo("Nothing to refresh; nothing written, nothing to commit.")
    elif request.dry_run:
        diff = planned_diff(corpus, refresh_files(outcome.refreshes))
        typer.echo(diff + "Dry run: nothing written.")
    else:
        write_refreshes(corpus, outcome.refreshes)
    if outcome.refusals:
        raise typer.Exit(1)


@promote_app.command("refresh")
def refresh(
    corpus: _Corpus,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the diff it would write; write nothing.")
    ] = False,
) -> None:
    """Re-render the front matter of unchanged versions, observed or received. Never commits."""
    request = RefreshAllRequest(corpus, dry_run)
    _refusing(lambda: refresh_all_impl(request, datetime.now(UTC)))
