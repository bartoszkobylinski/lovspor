"""``lovspor promote migrate`` — the migration commit after an extractor bump (ADR-0016 4e).

One document (``--authority`` and ``--slug``) or every current document of the
checkout is re-read by the running extractor (:mod:`.migrate`). Where the text
is byte-identical and the owner's standing approval names it at the running
extractor, only the version metadata moves (and a missing evidence sidecar is
written); the Markdown never does. Each migrated document is recorded in the
decision log (``migrated``), and the command prints the commit to make,
``migration(lokal-forskrift): …``. A ``migration:`` commit touches no
document's Markdown, so the history derivation sees no event in it.

``--dry-run`` prints the diff the run would write and writes and records
nothing. A refused document is reported on its own line and the exit status
is 1; the others are still migrated. Like the other ``promote`` commands, the
decorated command only calls :func:`migrate_impl` (#292).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import PromotionError, PromotionRefusedError
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import engine_root
from lovspor.promotion.commands import _authority, _Corpus, _echo_commit, _refusing, promote_app
from lovspor.promotion.corpus import LOCAL_DIR, MANIFEST_NAME, CorpusCheckout, LocalRecord
from lovspor.promotion.decisions import DecisionLog, MigratedRecord
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.migrate import (
    Migration,
    MigrationInputs,
    migration_files,
    plan_migration,
    planned_diff,
    published,
    published_klass_version,
    subject,
)
from lovspor.promotion.withdraw import locate_document
from lovspor.promotion.writer import apply_files

MANIFEST = f"{LOCAL_DIR}/{MANIFEST_NAME}"


@dataclass(frozen=True)
class MigrateRequest:
    """Which documents to migrate, in which checkout, and whether to write."""

    corpus: Path
    authority_id: str | None
    slug: str | None
    dry_run: bool


@dataclass(frozen=True)
class Outcome:
    """What the run decided per document: migrations, and refusals by id."""

    migrations: list[Migration]
    refusals: dict[str, str]


def migrate_impl(request: MigrateRequest, now: datetime) -> None:
    """Migrate the selected documents to the running extractor. Never commits."""
    root = _root()
    corpus = CorpusCheckout(request.corpus, [engine_root(), root.path])
    inputs = MigrationInputs(root, DecisionLog(root), corpus)
    outcome = _plan_all(inputs, _selected(corpus, request))
    for doc_id, reason in outcome.refusals.items():
        typer.echo(f"Refused: {doc_id}: {reason}", err=True)
    if not outcome.migrations:
        if not outcome.refusals:
            typer.echo("Nothing to migrate; nothing written, nothing to commit.")
    elif request.dry_run:
        diff = planned_diff(corpus, migration_files(corpus, outcome.migrations))
        typer.echo(diff + "Dry run: nothing written, nothing recorded.")
    else:
        _write(inputs, outcome.migrations, now)
    if outcome.refusals:
        raise typer.Exit(1)


def _selected(corpus: CorpusCheckout, request: MigrateRequest) -> list[tuple[str, LocalRecord]]:
    manifest = corpus.local_manifest()
    match (request.authority_id, request.slug):
        case (None, None):
            return [(i, r) for i, r in sorted(manifest.documents.items()) if r.status == "current"]
        case (str() as authority_id, str() as slug):
            return [locate_document(manifest, authority_id, slug)]
    msg = "name one document with both --authority and --slug, or neither for every document"
    raise PromotionRefusedError(msg)


def _plan_all(inputs: MigrationInputs, selected: list[tuple[str, LocalRecord]]) -> Outcome:
    outcome = Outcome(migrations=[], refusals={})
    for doc_id, record in selected:
        try:
            document = published(inputs.corpus, doc_id, record)
            if document.is_current():
                typer.echo(f"{doc_id}: already at extractor v{EXTRACTOR_VERSION}")
                continue
            klass = published_klass_version(document)
            authority = _authority(inputs.root, record.authority_id, klass)
            outcome.migrations.append(plan_migration(inputs, document, authority))
        except PromotionError as exc:
            outcome.refusals[doc_id] = str(exc)
    return outcome


def _write(inputs: MigrationInputs, migrations: list[Migration], now: datetime) -> None:
    commit_subject = subject(migrations)
    files = migration_files(inputs.corpus, migrations)
    written = apply_files(inputs.corpus, files)
    for migration in migrations:
        inputs.decisions.record_outcome(_record(migration, written, commit_subject, now))
        typer.echo(
            f"{migration.doc_id}: migrated extractor v{migration.from_version} "
            f"-> v{EXTRACTOR_VERSION}; the Markdown is unchanged"
        )
    for path in written:
        typer.echo(f"wrote {path}")
    _echo_commit(inputs.corpus, commit_subject)


def _record(
    migration: Migration, written: tuple[str, ...], commit_subject: str, now: datetime
) -> MigratedRecord:
    return MigratedRecord(
        artifact=migration.key,
        recorded_at=now,
        doc_id=migration.doc_id,
        version=migration.record.version,
        content_hash=migration.record.content_hash,
        from_extractor_version=migration.from_version,
        to_extractor_version=EXTRACTOR_VERSION,
        approved_at=migration.approval.decided_at,
        written=tuple(p for p in written if p in migration.files or p == MANIFEST),
        commit_subject=commit_subject,
    )


@promote_app.command("migrate")
def migrate(
    corpus: _Corpus,
    authority: Annotated[
        str | None, typer.Option("--authority", help="KLASS code of the one document's authority.")
    ] = None,
    slug: Annotated[
        str | None, typer.Option("--slug", help="The one document's slug under the authority.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the diff it would write; write nothing.")
    ] = False,
) -> None:
    """Move promoted documents to the running extractor, Markdown untouched. Never commits."""
    request = MigrateRequest(corpus, authority, slug, dry_run)
    _refusing(lambda: migrate_impl(request, datetime.now(UTC)))
