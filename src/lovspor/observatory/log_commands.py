"""Observatory commands that edit the observation log itself.

Every other command reads the log or appends observations to it as a fetch
happens. These are the operator acts that change what the log holds: dropping
a record an interrupted run never finished (`repair`), and correcting the
authority records were filed under (`reattribute`, ADR-0015). Each reports
without writing unless ``--apply`` is given, because each edits evidence.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import CorrectionRefusedError, LogIntegrityError
from lovspor.exclusive_workload import ExclusiveWorkloadHeldError, exclusive_workload
from lovspor.observatory.app import observatory_app
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import RecordTombstone, RefiledObservation, record_to_json_line
from lovspor.observatory.reattribution import (
    Attribution,
    ReattributionPlan,
    ReattributionRequest,
    apply_plan,
    check_registry,
    plan_reattribution,
)
from lovspor.observatory.registry import SourceRegistry, registry_path
from lovspor.observatory.registry_io import _load, _root


def _remove_unfinished_record(log: ObservationLog, raw: bytes) -> None:
    """Back the log up, then drop its unfinished final record.

    The backup is written before anything is truncated, so an interruption
    here leaves the original intact rather than half-repaired. It refuses to
    overwrite an existing backup: a second repair silently clobbering the
    evidence from the first is the failure this command exists to avoid.
    """
    backup = log.log_path.with_name(log.log_path.name + ".bak")
    if backup.exists():
        typer.echo(f"Refused: {backup} already exists — move it aside first.", err=True)
        raise typer.Exit(1)
    backup.write_bytes(raw)
    body, separator, _ = raw.rpartition(b"\n")
    log.log_path.write_bytes(body + separator)
    typer.echo(f"removed. The log as it stood is kept at {backup}")


@observatory_app.command("repair")
def repair(
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually remove it. Without this, nothing is written.")
    ] = False,
) -> None:
    """Drop an unfinished final record left by an interrupted run.

    Only that. The command refuses every other kind of damage, because only
    this one is safe to fix by deleting: an append that never finished
    describes a fetch that was never recorded, so the line carries nothing a
    reader could otherwise recover. A corrupted line anywhere else was written
    in full and then damaged — cutting it would destroy a record nobody has
    been told about, and it means the storage is failing rather than a run
    being interrupted.

    Reports without writing unless ``--apply`` is given. This edits evidence;
    it should take two deliberate steps, and a dry run has to be possible on a
    machine where the answer is "do not touch this".
    """
    log = ObservationLog(_root())
    scan = log.scan_damage()
    if scan.malformed_lines:
        numbers = ", ".join(str(number) for number in scan.malformed_lines)
        typer.echo(
            f"Refused: line(s) {numbers} are corrupted, which an interrupted append cannot "
            "produce. The storage is suspect — restore from backup rather than truncating.",
            err=True,
        )
        raise typer.Exit(1)
    if not scan.incomplete_final_record:
        typer.echo("nothing to repair")
        return
    raw = log.log_path.read_bytes()
    unfinished = raw.rpartition(b"\n")[2]
    typer.echo(f"unfinished final record: {len(unfinished)} bytes, {scan.records_read} intact")
    if not apply:
        typer.echo("dry run — nothing written. Re-run with --apply to remove it.")
        return
    _remove_unfinished_record(log, raw)


#: The name a correction run writes into the host lock. A sweep finding it held
#: defers; this command finding a sweep there refuses (ADR-0015 §7).
REATTRIBUTE_WORKLOAD = "observatory-reattribute"

_CorrectionOption = Annotated[
    Path,
    typer.Option(
        "--correction",
        help="JSON: from_authority, to_authority, host, reason, corrected_by — the decision.",
    ),
]


@observatory_app.command("reattribute")
def reattribute(
    correction: _CorrectionOption,
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually append it. Without this, nothing is written.")
    ] = False,
) -> None:
    """Re-file one host's records under the authority they belong to (ADR-0015).

    Selects every artifact and failure filed under `from_authority` whose URL
    host is `host`, and appends, per record, a re-filed observation under
    `to_authority` and a record tombstone retracting the original. Nothing
    already in the log changes, and no blob is touched.

    Refuses unless the register already supports the move: `to_authority` is
    registered on a domain covering the host, and `from_authority` no longer
    is. `reason` and `corrected_by` are the decision's own words and are never
    filled in by the engine.

    Reports without writing unless `--apply` is given, and `--apply` refuses
    while a sweep holds the host's workload lock. Running it again appends
    only what a previous run did not finish, which after a finished run is
    nothing.
    """
    request = _read_request(correction)
    root = _root()
    _refuse_unsupported(_load(registry_path(root)), request)
    log = ObservationLog(root)
    if not apply:
        _echo_plan(_plan(log, request))
        typer.echo("dry run — nothing written. Re-run with --apply to append it.")
        return
    try:
        with exclusive_workload(REATTRIBUTE_WORKLOAD):
            plan = _plan(log, request)
            _echo_plan(plan)
            typer.echo(f"appended {apply_plan(log, plan)} lines")
    except (ExclusiveWorkloadHeldError, CorrectionRefusedError) as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _read_request(path: Path) -> ReattributionRequest:
    try:
        return ReattributionRequest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        typer.echo(f"Refused: cannot read the correction at {path}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _refuse_unsupported(registry: SourceRegistry, request: ReattributionRequest) -> None:
    try:
        check_registry(registry, request)
    except CorrectionRefusedError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _plan(log: ObservationLog, request: ReattributionRequest) -> ReattributionPlan:
    attribution = Attribution(
        correction_id=uuid.uuid4().hex,
        reason=request.reason,
        corrected_by=request.corrected_by,
        corrected_at=datetime.now(UTC),
    )
    try:
        return plan_reattribution(log, request, attribution)
    except (LogIntegrityError, CorrectionRefusedError) as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _echo_plan(plan: ReattributionPlan) -> None:
    """What the run selected and what it would append, with the first of each kind."""
    typer.echo(
        f"selected: {plan.selected} records ({plan.artifacts} artifact, "
        f"{plan.failures} fetch_failure)"
    )
    if plan.first_observed is not None and plan.last_observed is not None:
        typer.echo(
            f"observed: {plan.first_observed.isoformat()} .. {plan.last_observed.isoformat()}"
        )
    typer.echo(f"already corrected: {plan.already_corrected}")
    typer.echo(f"half-written, to complete: {plan.to_complete}")
    typer.echo(f"to append: {len(plan.appends)} lines (correction {plan.correction_id})")
    for kind in (RefiledObservation, RecordTombstone):
        first = next((record for record in plan.appends if isinstance(record, kind)), None)
        if first is not None:
            typer.echo(f"  first {first.kind}: {record_to_json_line(first)}")
