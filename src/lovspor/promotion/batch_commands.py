"""``lovspor promote batch`` — classifier candidates of one authority, under one gate (S8).

The operator writes a batch spec (JSON; :class:`~lovspor.promotion.batch.BatchSpec`)::

    {"batch_id": "0301-2026-10-06", "authority_id": "0301", "klass_version": "131-2024",
     "classifier_output": "/abs/path/predictions_r11.jsonl",
     "classifier_version": "r1.1-2026-10-03", "sample_rate": "1"}

``sample_rate`` is required and has no default: ``"1"`` reviews every item,
as ADR-0016 4g recommends for the first authority. Optional ``artifacts``
lists SHA-256s to restrict the batch to.

Without ``--write`` the command assesses the batch and writes the holds
report (``batch-<id>.md`` and ``.json``) into ``--report-dir``; it records
nothing and writes nothing to the corpus. The owner then reviews the sampled
items with ``promote preview`` and records each decision with ``promote
approve``. With ``--write``, once the gate passes, it writes the **next**
approved item through the same code as ``promote local`` and prints the
commit to make: one commit per version (ADR-0016 C2), so the operator
commits and reruns the command for the next item. It never commits.

Exit status: 0 when the gate passes (or there is nothing to promote),
:data:`BLOCKED_EXIT_CODE` when it is blocked — a rejected or unreviewed
sampled item — and 1 when the request is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import ObservatoryRoot, engine_root
from lovspor.promotion.archive import authority_fetches
from lovspor.promotion.batch import BatchAssessment, BatchInputs, BatchSpec, assess_batch
from lovspor.promotion.batch_report import summary, write_report
from lovspor.promotion.classifier import read_classifier_output
from lovspor.promotion.commands import (
    PromotionContext,
    _authority,
    _Corpus,
    _refusing,
    promote_app,
    promote_one,
)
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import DecisionLog

#: Exit status of a batch whose gate is blocked: a rejected or unreviewed sampled item.
BLOCKED_EXIT_CODE = 4

__all__ = ["BLOCKED_EXIT_CODE", "BatchRequest", "batch_impl", "promote_app"]


@dataclass(frozen=True)
class BatchRequest:
    """One batch run: the spec, the corpus, where the report goes, and whether to write."""

    spec: Path
    corpus: Path
    report_dir: Path
    write: bool


def _spec(path: Path) -> BatchSpec:
    try:
        return BatchSpec.model_validate_json(path.read_bytes())
    except OSError as exc:
        msg = f"cannot read the batch spec at {path}: {exc}"
        raise PromotionRefusedError(msg) from exc
    except ValidationError as exc:
        msg = f"the batch spec does not validate: {exc}"
        raise PromotionRefusedError(msg) from exc


def _inputs(spec: BatchSpec, corpus_path: Path) -> tuple[ObservatoryRoot, BatchInputs]:
    root = _root()
    log = ObservationLog(root)
    inputs = BatchInputs(
        log=log,
        fetches=authority_fetches(log, spec.authority_id),
        corpus=CorpusCheckout(corpus_path, [engine_root(), root.path]),
        authority=_authority(root, spec.authority_id, spec.klass_version),
        decisions=DecisionLog(root),
    )
    return root, inputs


def batch_impl(request: BatchRequest, now: datetime) -> None:
    """Assess the batch, write its report, and — when asked and allowed — the next item."""
    spec = _spec(request.spec)
    output = read_classifier_output(spec.classifier_output, spec.classifier_version)
    root, inputs = _inputs(spec, request.corpus)
    assessment = assess_batch(spec, output, inputs)
    paths = write_report(request.report_dir, assessment, inputs.corpus.path, [engine_root()])
    _echo_summary(assessment, paths)
    if assessment.gate.verdict == "blocked":
        raise typer.Exit(BLOCKED_EXIT_CODE)
    if not request.write:
        typer.echo("Dry run: nothing written, nothing recorded.")
        return
    _write_next(root, inputs, assessment, now)


def _write_next(
    root: ObservatoryRoot, inputs: BatchInputs, assessment: BatchAssessment, now: datetime
) -> None:
    pending = assessment.to_write
    if not pending:
        typer.echo("Nothing to write: no approved item of this batch is missing from the corpus.")
        return
    item = pending[0]
    context = PromotionContext(
        root, inputs.log, inputs.fetches, item.key, inputs.corpus, inputs.authority
    )
    promote_one(context, now)
    if len(pending) > 1:
        typer.echo(f"{len(pending) - 1} more approved item(s): commit, then rerun this command.")


def _echo_summary(assessment: BatchAssessment, paths: tuple[Path, Path]) -> None:
    counts = ", ".join(f"{name} {count}" for name, count in summary(assessment).items())
    typer.echo(f"Batch {assessment.spec.batch_id}: {counts}")
    for reason, count in assessment.holds_by_reason.items():
        typer.echo(f"  held {reason}: {count}")
    typer.echo(f"Gate: {assessment.gate.verdict}")
    for key in assessment.gate.rejected:
        typer.echo(f"  rejected in sample, blocks the batch: {key.sha256} {key.source_url}")
    if assessment.gate.awaiting_review:
        typer.echo(f"  {len(assessment.gate.awaiting_review)} sampled item(s) await review")
    typer.echo(f"Report: {paths[0]} (+ {paths[1].name})")


@promote_app.command("batch")
def batch(
    spec: Annotated[Path, typer.Option("--spec", help="JSON batch spec; sample_rate required.")],
    corpus: _Corpus,
    report_dir: Annotated[
        Path, typer.Option("--report-dir", help="Absolute directory for the holds report.")
    ],
    write: Annotated[
        bool, typer.Option("--write", help="Write the next approved item if the gate passes.")
    ] = False,
) -> None:
    """Assess a classifier batch, gate it on the owner's sample, write the next item."""
    request = BatchRequest(spec, corpus, report_dir, write)
    _refusing(lambda: batch_impl(request, datetime.now(UTC)))
