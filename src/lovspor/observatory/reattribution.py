"""Planning a re-attribution: which records a correction names, and what it appends.

The one correction ADR-0015 permits is a wrong ``authority_id``, and the one
supported way to issue it is ``observatory reattribute``. This module is that
command's logic, kept apart from the CLI so the plan can be read and tested
without a terminal: the command prints a plan and, only with ``--apply``,
appends it.

A plan is computed from the log as it stands, and re-planning a log the plan
was already applied to yields nothing to append. That is the whole of the
idempotency: an original already corrected is skipped; a correction a crash
left with one half is completed with only the other half; nothing is appended
twice.
"""

from collections.abc import Iterable
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lovspor.errors import CorrectionRefusedError, LogIntegrityError
from lovspor.observatory.corrections import CorrectionSet
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
    ObservationRecord,
    RecordTombstone,
    RefiledObservation,
    record_key,
)
from lovspor.observatory.registry import SourceRegistry, host_within_domain, normalised_domain


class ReattributionRequest(BaseModel):
    """What the operator decided: move one host's records from one authority to another.

    Read from the document ``--correction`` names, so the dry run and the
    ``--apply`` read the same decision, and the decision itself is a file the
    operator can keep beside the run's output.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_authority: TrimmedNonBlankStr
    to_authority: TrimmedNonBlankStr
    host: TrimmedNonBlankStr
    reason: TrimmedNonBlankStr
    #: The human who decided. Never synthesised: a correction with no author
    #: is the defect ADR-0015 exists to fix, one level up.
    corrected_by: TrimmedNonBlankStr

    @model_validator(mode="after")
    def _a_move(self) -> "ReattributionRequest":
        if self.from_authority == self.to_authority:
            raise ValueError("from_authority and to_authority are the same authority")
        return self


class ReattributionPlan(BaseModel):
    """What a run would append, and what it found on the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correction_id: str
    artifacts: int = 0
    failures: int = 0
    first_observed: datetime | None = None
    last_observed: datetime | None = None
    already_corrected: int = 0
    to_complete: int = 0
    appends: tuple[RefiledObservation | RecordTombstone, ...] = Field(default=())
    #: Where the log's correction fold stood when the plan was made. A plan is
    #: only valid against the corrections it was checked against.
    corrections_through: int = 0
    #: The log's size when the plan was made. It may grow — captures keep
    #: appending observations — but never shrink under a plan.
    log_size: int = 0

    @property
    def selected(self) -> int:
        return self.artifacts + self.failures


def check_registry(registry: SourceRegistry, request: ReattributionRequest) -> None:
    """Refuse a correction to an attribution the register itself would not make.

    The validator a hand edit would skip (ADR-0015 §7): the target must be
    registered on a domain covering the host, and the source must no longer
    be — "covers" meaning :func:`host_within_domain`, the capture gate's test.

    Raises:
        CorrectionRefusedError: the register does not support the correction.
    """
    target = registry.sources.get(request.to_authority)
    if target is None:
        raise CorrectionRefusedError(f"{request.to_authority} is not registered")
    if not host_within_domain(request.host, target.canonical_domain):
        raise CorrectionRefusedError(
            f"{request.host} is outside {request.to_authority}'s domain {target.canonical_domain}"
        )
    source = registry.sources.get(request.from_authority)
    if source is not None and host_within_domain(request.host, source.canonical_domain):
        raise CorrectionRefusedError(
            f"{request.host} is still inside {request.from_authority}'s domain "
            f"{source.canonical_domain}; repair the register first (replace-source-domain)"
        )


class Attribution(BaseModel):
    """Who, when and why — identical on both halves of one correction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correction_id: str
    reason: str
    corrected_by: str
    corrected_at: datetime


def plan_reattribution(
    log: ObservationLog, request: ReattributionRequest, attribution: Attribution
) -> ReattributionPlan:
    """What correcting ``request`` would append to ``log`` as it stands now.

    Raises:
        LogIntegrityError: the log does not read to the end. A plan made from
            part of a log would leave the unread records misattributed while
            reporting the correction as done.
        CorrectionRefusedError: a half-written correction of one of these
            records moves it somewhere other than ``request`` asks.
    """
    anchor = {"log_size": _size(log), "corrections_through": log.corrections().last_offset}
    corrections = log.corrections()
    selected = _selected(log, request)
    plan = _summary(selected.values(), attribution.correction_id)
    appends: list[RefiledObservation | RecordTombstone] = []
    already = completed = 0
    for key, original in selected.items():
        if corrections.in_force(key) is not None:
            already += 1
            continue
        completion = _completion(corrections, key, original, request)
        completed += completion is not None
        appends.extend(completion or _both_halves(key, original, request, attribution))
    return plan.model_copy(
        update={"appends": tuple(appends), "already_corrected": already, "to_complete": completed}
        | anchor
    )


def _size(log: ObservationLog) -> int:
    return log.log_path.stat().st_size if log.log_path.exists() else 0


def _selected(
    log: ObservationLog, request: ReattributionRequest
) -> dict[str, ArtifactObservation | FetchFailure]:
    """Every observation filed under the source for the host, by key.

    By key, so two byte-identical lines are one claim and one correction
    names both (ADR-0015 §2). Re-filed observations are not selected: a
    wrong correction is corrected through its own record, not by this run.
    """
    host = normalised_domain(request.host)
    found: dict[str, ArtifactObservation | FetchFailure] = {}

    def collect(record: ObservationRecord, line: bytes) -> None:
        if not isinstance(record, ArtifactObservation | FetchFailure):
            return
        if record.authority_id != request.from_authority:
            return
        if normalised_domain(urlsplit(record.url).hostname or "") == host:
            found.setdefault(record_key(line), record)

    if not log.scan_lines_into(collect).complete:
        raise LogIntegrityError(f"{log.log_path} is damaged; run `observatory verify` first")
    return found


def _summary(
    selected: Iterable[ArtifactObservation | FetchFailure], correction_id: str
) -> ReattributionPlan:
    records = list(selected)
    times = [record.observed_at for record in records]
    artifacts = sum(isinstance(record, ArtifactObservation) for record in records)
    return ReattributionPlan(
        correction_id=correction_id,
        artifacts=artifacts,
        failures=len(records) - artifacts,
        first_observed=min(times, default=None),
        last_observed=max(times, default=None),
    )


def _completion(
    corrections: CorrectionSet,
    key: str,
    original: ArtifactObservation | FetchFailure,
    request: ReattributionRequest,
) -> list[RefiledObservation | RecordTombstone] | None:
    """The tombstone that finishes this decision's own interrupted correction, or None.

    The one half-written state a run may resume is the one the writer itself
    leaves: a single re-filed half that restates ``original`` under
    ``request``'s target with ``request``'s own reason and author, and no
    tombstone. Anything else touching the key — a tombstone without its
    re-filed half (the writer never leaves one), halves that disagree, a
    second correction, a move elsewhere — was not written by this decision,
    and completing it would be a guess about someone else's correction.

    Raises:
        CorrectionRefusedError: the key carries any other half-written state.
    """
    begun = [entry.record for entry in corrections.refiled.get(key, [])]
    for refiled in begun:
        if refiled.observation.authority_id != request.to_authority:
            raise CorrectionRefusedError(
                f"record {key[:12]} has a half-written correction to "
                f"{refiled.observation.authority_id}, not {request.to_authority}"
            )
    if corrections.tombstones.get(key):
        raise CorrectionRefusedError(
            f"record {key[:12]} has a record tombstone with no matching re-filed half; "
            "the writer never leaves one — run `observatory verify`"
        )
    if not begun:
        return None
    if len(begun) > 1 or not _resumable(begun[0], original, request):
        raise CorrectionRefusedError(
            f"record {key[:12]} has a half-written correction this decision did not write"
        )
    return [_tombstone(key, _attribution_of(begun[0].correction))]


def _resumable(
    refiled: RefiledObservation,
    original: ArtifactObservation | FetchFailure,
    request: ReattributionRequest,
) -> bool:
    """True when ``refiled`` is exactly what this decision writes for ``original``."""
    correction = refiled.correction
    return (
        refiled.observation == original.model_copy(update={"authority_id": request.to_authority})
        and correction.previous_values == {"authority_id": request.from_authority}
        and (correction.reason, correction.corrected_by) == (request.reason, request.corrected_by)
    )


def _attribution_of(half: Correction | RecordTombstone) -> Attribution:
    return Attribution(
        correction_id=half.correction_id,
        reason=half.reason,
        corrected_by=half.corrected_by,
        corrected_at=half.corrected_at,
    )


def _both_halves(
    key: str,
    original: ArtifactObservation | FetchFailure,
    request: ReattributionRequest,
    attribution: Attribution,
) -> list[RefiledObservation | RecordTombstone]:
    """A new correction, in the order that keeps every crash point safe (§6)."""
    return [
        _refiled(key, original, request.to_authority, attribution),
        _tombstone(key, attribution),
    ]


def _refiled(
    key: str, original: ArtifactObservation | FetchFailure, to: str, attribution: Attribution
) -> RefiledObservation:
    return RefiledObservation(
        observation=original.model_copy(update={"authority_id": to}),
        correction=Correction(
            supersedes=key,
            corrected_fields=("authority_id",),
            previous_values={"authority_id": original.authority_id},
            **attribution.model_dump(),
        ),
    )


def _tombstone(key: str, attribution: Attribution) -> RecordTombstone:
    return RecordTombstone(retracts=key, **attribution.model_dump())


def apply_plan(log: ObservationLog, plan: ReattributionPlan) -> int:
    """Append the plan, one record at a time, through the log's only write path.

    Each append is locked and fsynced (:meth:`ObservationLog.append`), and
    the plan orders every correction re-filed half first, so an interruption
    at any point leaves originals that read either as filed or as corrected.

    Raises:
        CorrectionRefusedError: the log changed under the plan — a correction
            was appended or the log got shorter since it was made. Nothing is
            written; plan again. The command plans under the host lock, so
            this guards every other caller of the plan too.
    """
    if log.corrections().last_offset != plan.corrections_through or _size(log) < plan.log_size:
        raise CorrectionRefusedError("the log changed since the plan was made; plan again")
    for record in plan.appends:
        log.append(record)
    return len(plan.appends)
