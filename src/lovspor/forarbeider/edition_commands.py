"""``lovspor ops forarbeider-edition``: a saved landing page's edition evidence (#784).

Offline, like ``forarbeider-metadata``: no HTTP request, no clock, no
publication. The evidence lands beside the v1 record it names, as
``<record>.edition.json``. The landing page is associated with a v1 metadata record only when
both state the same document, and the record is read, never rewritten. The
evidence is an observation: written once, owner-only, never replaced.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.corpus_fetch import default_corpus_path
from lovspor.forarbeider.commands import MAX_HTML_BYTES, _saved_page, aware_moment
from lovspor.forarbeider.edition import EditionFacts, read_landing
from lovspor.forarbeider.edition_models import EditionEvidence, evidence_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import SourceLocator
from lovspor.forarbeider.models import ForarbeidRecord, proved_identity
from lovspor.forarbeider.page import identity_fields
from lovspor.forarbeider.staging import checked_output, read_private, write_observation
from lovspor.ops.commands import ops_app

_ENGINE_ROOT = Path(__file__).resolve().parents[3]
_FIXED_FIELDS: dict[str, object] = {
    "schema_version": 1,
    "record_type": "forarbeid-edition-evidence",
    "landing_provenance": "operator_saved_offline",
    "parser_version": 1,
    "chapter_edition": "unknown",
}


def _record(path: Path) -> ForarbeidRecord:
    """The v1 record at ``path``; one others could read is refused, as every reader here does."""
    payload = read_private(path, MAX_HTML_BYTES)
    try:
        return ForarbeidRecord.model_validate_json(payload)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(path)) from error


def _require_same_document(record: ForarbeidRecord, landing: SourceLocator) -> None:
    stated = (
        record.document_type,
        record.session,
        record.proposition_number,
        record.source_document_id,
        record.source_url,
        record.id,
    )
    if stated != proved_identity(landing):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_IDENTITY_MISMATCH, record.id)


def _identity(landing: SourceLocator) -> dict[str, object]:
    """The v1 record's identity fields, without the chapter page the landing page is not."""
    fields = identity_fields(landing)
    del fields["source_page_url"]
    return fields


def build_evidence(
    payload: bytes, landing_url: str, retrieved_at: datetime, record: ForarbeidRecord
) -> tuple[EditionEvidence, EditionFacts]:
    """The evidence of ``payload``, depending on these inputs alone.

    Raises:
        ForarbeiderRefusedError: the page or the association is refused.
    """
    landing, facts = read_landing(payload, landing_url)
    _require_same_document(record, landing)
    fields = {
        **_FIXED_FIELDS,
        **_identity(landing),
        "landing_page_url": landing.page_url,
        "landing_retrieved_at": retrieved_at,
        "landing_sha256": hashlib.sha256(payload).hexdigest(),
        "edition": facts.model_dump(),
        "chapter_record": record.model_dump(
            include={"source_page_url", "source_sha256", "retrieved_at"}
        ),
    }
    try:
        return EditionEvidence.model_validate(fields), facts
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, landing_url) from error


def evidence_path(metadata: Path) -> Path:
    """Where the evidence of ``metadata`` lives: beside it, ``<record>.edition.json``."""
    return metadata.with_name(f"{metadata.stem}.edition.json")


def edition_impl(landing_html: Path, landing_url: str, retrieved_at: str, metadata: Path) -> str:
    """Write the evidence beside ``metadata`` and return the line that reports it.

    The line names the kind and the stated date, never page text.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    target = checked_output(evidence_path(metadata), (_ENGINE_ROOT, default_corpus_path()))
    moment = aware_moment(retrieved_at)
    record = _record(metadata)
    evidence, facts = build_evidence(_saved_page(landing_html), landing_url, moment, record)
    written = write_observation(target, evidence_text(evidence))
    stated = f" (stated {facts.corrected_on.isoformat()})" if facts.corrected_on else ""
    return (
        f"{'wrote' if written else 'unchanged'} {evidence.id}: edition {facts.kind}{stated}, "
        f"chapter edition unknown, to {target}"
    )


@ops_app.command("forarbeider-edition")
def forarbeider_edition(
    landing_html: Annotated[
        Path,
        typer.Option("--landing-html", help="The saved landing page (no ?ch=).", readable=False),
    ],
    landing_url: Annotated[
        str, typer.Option("--landing-url", help="The exact landing URL that page was saved from.")
    ],
    retrieved_at: Annotated[
        str, typer.Option("--retrieved-at", help="When it was saved, ISO 8601 with offset.")
    ],
    metadata: Annotated[
        Path,
        typer.Option(
            "--metadata",
            help="The v1 record; the evidence is written beside it as <record>.edition.json.",
        ),
    ],
) -> None:
    """Record what a saved landing page states about its edition (issue #784).

    Offline: no HTTP request, no publication, no clearance. The v1 record is
    not changed. Exit 1 is a typed refusal, and nothing is written.
    """
    try:
        typer.echo(edition_impl(landing_html, landing_url, retrieved_at, metadata))
    except ForarbeiderRefusedError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
