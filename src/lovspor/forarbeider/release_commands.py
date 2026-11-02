"""The bodies of ``lovspor ops forarbeider-release`` (#738, ADR-0018 PR3B-1).

Offline and read-only to every corpus: nothing here fetches, stages, renders or
writes a ``lovverk`` file, and no option can make it. Records go to an
owner-only workspace outside every Git checkout, named by their digest and
never replaced. The typer commands live in :mod:`lovspor.forarbeider.release_cli`
and only call these, so the bodies stay inside the mutation gate (#292).

Printed: ids, digests, codes and counts. Never chapter text, evidence content,
evidence paths or handles.
"""

import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.corpus_fetch import default_corpus_path
from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.commands import MAX_HTML_BYTES, aware_moment
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.release_models import (
    ALL_CONDITIONS,
    SHA256,
    AuthorizedChapter,
    ChapterIdentity,
    EvidenceArtifact,
    EvidenceKind,
    PreflightReport,
    ReleaseAuthorization,
    ReleaseDossier,
    ReleaseRevocation,
    chapter_identity,
    destination_of,
    digest,
    document_of,
)
from lovspor.forarbeider.release_preflight import (
    Observed,
    PreflightInputs,
    Stored,
    current_pin,
    evaluate,
)
from lovspor.forarbeider.release_store import (
    MAX_EVIDENCE_BYTES,
    Folder,
    open_workspace,
    read_file,
    read_model,
    read_named,
    read_record,
    write_record,
)
from lovspor.forarbeider.review_ledger import StoredDecision, load_history
from lovspor.forarbeider.review_models import ReviewStatus, subject_of
from lovspor.forarbeider.staging import checked_directory

_ENGINE_ROOT = Path(__file__).resolve().parents[3]
_DIGEST = re.compile(SHA256)
_CHECKPOINT = re.compile(r"(?P<key>[0-9a-f]{64})@(?P<revision>[1-9]\d{0,5}):(?P<head>[0-9a-f]{64})")
_EVIDENCE = re.compile(
    r"(?P<kind>[a-z_]+)@(?P<chapters>[1-9]\d{0,4}(,[1-9]\d{0,4})*)=(?P<path>/.+)"
)
_NOT_PERMISSION = "a private record, not permission to publish"


class ReleaseScope(BaseModel):
    """The workspace records go to and the PR3A ledger decisions are read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: Path
    ledger: Path


class ChapterScope(BaseModel):
    """One proposition's PR1 metadata, its PR2 chapter results and their saved pages."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    release: ReleaseScope
    metadata: Path
    observations: tuple[Path, ...]
    pages: tuple[Path, ...]


class Staged(BaseModel):
    """The staged inputs, read and checked to be one proposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: ForarbeidRecord
    results: tuple[ChapterResult, ...]


def _forbidden() -> tuple[Path, ...]:
    return (_ENGINE_ROOT, default_corpus_path())


def _interactive() -> bool:
    return sys.stdin.isatty()


def _workspace(scope: ReleaseScope) -> Path:
    return open_workspace(scope.workspace, _forbidden())


def _read_staged(scope: ChapterScope) -> Staged:
    """The metadata and results as given, in chapter order; a preflight judges the rest.

    Raises:
        ForarbeiderRefusedError: an unsafe or unreadable file, or not its schema.
    """
    metadata = read_model(scope.metadata, _forbidden(), ForarbeidRecord)
    results = [read_model(path, _forbidden(), ChapterResult) for path in scope.observations]
    ordered = tuple(sorted(results, key=lambda result: result.chapter_number))
    return Staged(metadata=metadata, results=ordered)


def _staged(scope: ChapterScope) -> Staged:
    """The staged inputs, refused unless they are one proposition, once each.

    Raises:
        ForarbeiderRefusedError: an unsafe file, another document or a duplicate.
    """
    staged = _read_staged(scope)
    for result in staged.results:
        if document_of(result) != document_of(staged.metadata):
            raise ForarbeiderRefusedError(RefusalReason.CHAPTER_FOREIGN, result.id)
    numbers = [result.chapter_number for result in staged.results]
    if len(set(numbers)) != len(numbers):
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_DUPLICATE, "observation")
    return staged


def _identities(staged: Staged) -> tuple[ChapterIdentity, ...]:
    identities = []
    for result in staged.results:
        identity = chapter_identity(result)
        if identity is None:
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, result.id)
        identities.append(identity)
    return tuple(identities)


def actor(text: str) -> tuple[str, datetime]:
    """``HANDLE@ISO-8601``: who records a decision and when, as one option.

    Raises:
        ForarbeiderRefusedError: ``release_invalid`` or ``invalid_timestamp``.
    """
    handle, separator, moment = text.partition("@")
    if not separator or not handle:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "actor")
    return handle, aware_moment(moment)


def interval(text: str) -> tuple[datetime, datetime]:
    """``FROM/UNTIL``, the ISO 8601 interval an authorization holds for.

    Raises:
        ForarbeiderRefusedError: ``release_invalid`` or ``invalid_timestamp``.
    """
    start, separator, end = text.partition("/")
    if not separator:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "valid")
    return aware_moment(start), aware_moment(end)


def _validated[T: BaseModel](model: type[T], fields: dict[str, object]) -> T:
    """Field names only on a refusal: the input may be the very name the grammar refused."""
    try:
        return model.model_validate(fields)
    except ValidationError as error:
        names = sorted({str(entry["loc"][0]) for entry in error.errors() if entry["loc"]})
        detail = ",".join(names) or model.__name__
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, detail) from error


def _digest_option(text: str, name: str) -> str:
    if _DIGEST.fullmatch(text) is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, name)
    return text


def _artifact(text: str) -> EvidenceArtifact:
    """``KIND@N[,N...]=PATH``; the digest and size are read from the file itself.

    Raises:
        ForarbeiderRefusedError: a malformed option, or a file missing, empty or unsafe.
    """
    match = _EVIDENCE.fullmatch(text)
    if match is None or match["kind"] not in set(EvidenceKind):
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "evidence")
    payload = read_file(Path(match["path"]), _forbidden(), MAX_EVIDENCE_BYTES)
    if not payload:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, "evidence")
    chapters = sorted({int(number) for number in match["chapters"].split(",")})
    return _validated(
        EvidenceArtifact,
        {
            "kind": match["kind"],
            "path": match["path"],
            "sha256": digest(payload),
            "size_bytes": len(payload),
            "chapters": tuple(chapters),
        },
    )


def dossier_impl(scope: ChapterScope, evidence: Sequence[str], reviewed: str) -> str:
    """Record the release dossier of the staged chapters and return the line that reports it.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    staged = _staged(scope)
    artifacts = sorted((_artifact(item) for item in evidence), key=_artifact_key)
    reviewer, moment = actor(reviewed)
    dossier = _validated(
        ReleaseDossier,
        {
            "schema_version": 1,
            "record_type": "forarbeider_release_dossier",
            "document": document_of(staged.metadata),
            "chapters": _identities(staged),
            "evidence": tuple(artifacts),
            "reviewer": reviewer,
            "reviewed_at": moment,
            "legal_sufficiency_decided_by_code": False,
        },
    )
    sha = write_record(_workspace(scope.release), "dossiers", dossier)
    kinds = ",".join(sorted({item.kind.value for item in artifacts}))
    return f"dossier {sha} {dossier.document.id}: evidence {kinds} ({_NOT_PERMISSION})"


def _artifact_key(artifact: EvidenceArtifact) -> tuple[str, str, str]:
    return (artifact.kind.value, artifact.sha256, artifact.path)


def _evidence_digest(artifact: EvidenceArtifact) -> str | None:
    payload = read_file(Path(artifact.path), _forbidden(), MAX_EVIDENCE_BYTES)
    return None if payload is None else digest(payload)


def _approved_pins(
    scope: ChapterScope, staged: Staged, decided: datetime
) -> tuple[AuthorizedChapter, ...]:
    """Each chapter with the head of its verified PR3A chain, an approval made by ``decided``.

    Raises:
        ForarbeiderRefusedError: ``review_not_eligible``, ``release_out_of_order``
            for an approval after the owner's decision, or a broken chain.
    """
    ledger = checked_directory(scope.release.ledger, _forbidden())
    pinned = []
    for result, identity in zip(staged.results, _identities(staged), strict=True):
        history = load_history(ledger, subject_of(result))
        if not history or history[-1].decision.status is not ReviewStatus.APPROVED:
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_NOT_ELIGIBLE, result.id)
        if history[-1].decision.decided_at > decided:
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_OUT_OF_ORDER, result.id)
        pinned.append(AuthorizedChapter(chapter=identity, review=current_pin(result, history)))
    return tuple(pinned)


def _current_dossier(scope: ChapterScope, staged: Staged, sha: str) -> ReleaseDossier:
    dossier = read_record(_workspace(scope.release), "dossiers", sha, ReleaseDossier)
    if dossier is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, sha)
    if dossier.chapters != _identities(staged) or dossier.document != document_of(staged.metadata):
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_STALE, sha)
    return dossier


def _purpose(dossier: ReleaseDossier) -> str:
    """The one purpose statement bound to every chapter of the dossier, its bytes intact.

    Version 1 authorizes a single purpose, so two candidates are as refused as none:
    the owner is never handed whichever came first.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: no such purpose, or two.
    """
    numbers = {item.chapter_number for item in dossier.chapters}
    candidates = {
        artifact.sha256
        for artifact in dossier.evidence
        if artifact.kind is EvidenceKind.PUBLICATION_PURPOSE
        and numbers <= set(artifact.chapters)
        and _evidence_digest(artifact) == artifact.sha256
    }
    if len(candidates) != 1:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, "publication_purpose")
    return candidates.pop()


class Grant(BaseModel):
    """The owner and the window an authorization is declared for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dossier: str
    decided: str
    valid: str


_FIXED_AUTHORIZATION: dict[str, object] = {
    "schema_version": 1,
    "record_type": "forarbeider_release_authorization_candidate",
    "revocation_conditions": ALL_CONDITIONS,
    "grants_publication": False,
    "identity_authenticated": False,
}


def authorize_impl(scope: ChapterScope, grant: Grant) -> str:
    """Record the owner's release authorization candidate, at a terminal.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    staged = _staged(scope)
    sha = _digest_option(grant.dossier, "dossier")
    dossier = _current_dossier(scope, staged, sha)
    _, decided = actor(grant.decided)
    if decided < dossier.reviewed_at:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_OUT_OF_ORDER, "dossier")
    chapters = _approved_pins(scope, staged, decided)
    declared = _declared(dossier, sha, chapters, grant)
    authorization = _validated(ReleaseAuthorization, {**_FIXED_AUTHORIZATION, **declared})
    document = dossier.document
    _require_a_person(f"authorize {document.id} {sha[:12]}")
    stored = write_record(_workspace(scope.release), "authorizations", authorization)
    return (
        f"authorization {stored} {document.id}: chapters {_numbers(chapters)} ({_NOT_PERMISSION})"
    )


def _declared(
    dossier: ReleaseDossier, sha: str, chapters: tuple[AuthorizedChapter, ...], grant: Grant
) -> dict[str, object]:
    owner, moment = actor(grant.decided)
    start, end = interval(grant.valid)
    return {
        "document": dossier.document,
        "chapters": chapters,
        "dossier_sha256": sha,
        "purpose_sha256": _purpose(dossier),
        "destination": destination_of(dossier.document),
        "owner": owner,
        "decided_at": moment,
        "valid_from": start,
        "valid_until": end,
    }


def _numbers(chapters: tuple[AuthorizedChapter, ...]) -> str:
    return ",".join(str(item.chapter.chapter_number) for item in chapters)


def _require_a_person(phrase: str) -> None:
    """Keeps cron, CI and a pipe out. A pseudo-terminal can still get past it."""
    if not _interactive() or typer.prompt(f"Type to confirm: {phrase}") != phrase:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_NOT_INTERACTIVE, "authorization")


def revoke_impl(scope: ReleaseScope, authorization: str, decided: str) -> str:
    """Record that the owner withdrew an authorization. A second revocation is refused.

    Raises:
        ForarbeiderRefusedError: an unknown authorization, or one already revoked.
    """
    workspace = _workspace(scope)
    sha = _digest_option(authorization, "authorization")
    if read_record(workspace, "authorizations", sha, ReleaseAuthorization) is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, sha)
    if read_named(workspace, "revocations", sha, ReleaseRevocation) is not None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_ALREADY_REVOKED, sha)
    owner, moment = actor(decided)
    revocation = _validated(
        ReleaseRevocation,
        {
            "schema_version": 1,
            "record_type": "forarbeider_release_revocation",
            "authorization_sha256": sha,
            "owner": owner,
            "revoked_at": moment,
        },
    )
    write_record(workspace, "revocations", revocation, sha)
    return f"revoked {sha}"


def _revoked(workspace: Path, sha: str) -> bool:
    revocation = read_named(workspace, "revocations", sha, ReleaseRevocation)
    if revocation is not None and revocation.authorization_sha256 != sha:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, sha)
    return revocation is not None


def checkpoints(values: Sequence[str]) -> dict[str, tuple[int, str]]:
    """``SUBJECT@REVISION:HEAD`` per chapter, as ``forarbeider-review verify`` prints them.

    Raises:
        ForarbeiderRefusedError: ``release_invalid``: malformed, or one subject twice.
    """
    pinned: dict[str, tuple[int, str]] = {}
    for value in values:
        match = _CHECKPOINT.fullmatch(value)
        if match is None or match["key"] in pinned:
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "checkpoint")
        pinned[match["key"]] = (int(match["revision"]), match["head"])
    return pinned


def _pages(scope: ChapterScope) -> dict[str, bytes]:
    pages = {}
    for path in scope.pages:
        payload = read_file(path, _forbidden(), MAX_HTML_BYTES)
        if payload is None:
            raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(path))
        pages[digest(payload)] = payload
    return pages


def _history(ledger: Path, result: ChapterResult) -> tuple[StoredDecision, ...] | None:
    try:
        return load_history(ledger, subject_of(result))
    except ForarbeiderRefusedError as error:
        if error.reason is RefusalReason.REVIEW_LEDGER_CORRUPT:
            return None
        raise


class Run(BaseModel):
    """What one preflight is asked: the authorization, the checkpoints and the moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization: str | None
    checkpoints: tuple[str, ...]
    at: str


def _authorization(workspace: Path, run: Run) -> Stored[ReleaseAuthorization] | None:
    if run.authorization is None:
        return None
    sha = _digest_option(run.authorization, "authorization")
    record = read_record(workspace, "authorizations", sha, ReleaseAuthorization)
    return None if record is None else Stored(record, sha)


def _dossier(
    workspace: Path, authorization: Stored[ReleaseAuthorization] | None
) -> Stored[ReleaseDossier] | None:
    if authorization is None:
        return None
    sha = authorization.record.dossier_sha256
    record = read_record(workspace, "dossiers", sha, ReleaseDossier)
    return None if record is None else Stored(record, sha)


def preflight_inputs(scope: ChapterScope, run: Run) -> PreflightInputs:
    """Every input of a verdict, read from private files.

    Raises:
        ForarbeiderRefusedError: an unsafe or unreadable file, or a malformed option.
    """
    workspace = _workspace(scope.release)
    ledger = checked_directory(scope.release.ledger, _forbidden())
    staged, pages = _read_staged(scope), _pages(scope)
    authorization = _authorization(workspace, run)
    dossier = _dossier(workspace, authorization)
    artifacts = () if dossier is None else dossier.record.evidence
    return PreflightInputs(
        metadata=staged.metadata,
        metadata_page=pages.get(staged.metadata.source_sha256),
        observed=tuple(
            Observed(result, pages.get(result.source_sha256), _history(ledger, result))
            for result in staged.results
        ),
        dossier=dossier,
        evidence={item.path: _evidence_digest(item) for item in artifacts},
        authorization=authorization,
        revoked=authorization is not None and _revoked(workspace, authorization.sha256),
        checkpoints=checkpoints(run.checkpoints),
        at=aware_moment(run.at),
    )


def preflight_impl(scope: ChapterScope, run: Run) -> tuple[str, bool]:
    """Write the report and return the lines that report it, and whether it is a preview.

    Raises:
        ForarbeiderRefusedError: an input could not be read safely; nothing was written.
    """
    report = evaluate(preflight_inputs(scope, run))
    sha = write_record(_workspace(scope.release), "reports", report)
    return report_lines(report, sha), report.outcome == "eligible_preview"


def report_lines(report: PreflightReport, sha: str) -> str:
    """The verdict in codes and digests only."""
    head = (
        f"{report.outcome.upper()} {report.document.id} report {sha} "
        f"coverage {report.coverage} publication_authorized=false"
    )
    lines = [head, f"document_reasons {','.join(report.document_reasons) or 'none'}"]
    for item in report.chapters:
        verdict = "eligible" if item.eligible else "deny"
        reasons = ",".join([*item.reasons, *(f"missing:{gate}" for gate in item.missing_evidence)])
        lines.append(f"chapter {item.chapter_number} {item.chapter_id} {verdict} {reasons or '-'}")
    return "\n".join(lines)


def show_impl(scope: ReleaseScope, kind: str, sha: str) -> str:
    """A stored record's ids, digests and codes: never paths, handles or text.

    Raises:
        ForarbeiderRefusedError: an unknown kind or record, or an edited one.
    """
    workspace = _workspace(scope)
    sha = _digest_option(sha, "digest")
    if kind == "dossier":
        return _show_dossier(_stored(workspace, "dossiers", sha, ReleaseDossier), sha)
    if kind == "authorization":
        record = _stored(workspace, "authorizations", sha, ReleaseAuthorization)
        return _show_authorization(record, sha, _revoked(workspace, sha))
    if kind == "report":
        return report_lines(_stored(workspace, "reports", sha, PreflightReport), sha)
    raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "kind")


def _stored[T: BaseModel](workspace: Path, folder: Folder, sha: str, model: type[T]) -> T:
    record = read_record(workspace, folder, sha, model)
    if record is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, sha)
    return record


def _show_dossier(record: ReleaseDossier, sha: str) -> str:
    lines = [f"dossier {sha} {record.document.id}"]
    lines += [f"chapter {item.chapter_id} text {item.text_sha256}" for item in record.chapters]
    lines += [
        f"evidence {item.kind.value} {item.sha256} chapters {','.join(map(str, item.chapters))}"
        for item in record.evidence
    ]
    return "\n".join(lines)


def _show_authorization(record: ReleaseAuthorization, sha: str, revoked: bool) -> str:
    window = f"{record.valid_from.isoformat()}/{record.valid_until.isoformat()}"
    lines = [
        f"authorization {sha} {record.document.id} destination {record.destination}",
        f"dossier {record.dossier_sha256} purpose {record.purpose_sha256} valid {window}",
    ]
    lines += [
        f"chapter {item.chapter.chapter_id} review {item.review.subject_key}@{item.review.revision}"
        for item in record.chapters
    ]
    state = "revoked" if revoked else "not revoked"
    return "\n".join([*lines, f"{state}; identity_authenticated=false grants_publication=false"])
