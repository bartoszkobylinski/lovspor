"""A private real-document pilot audit, chapter by chapter (#738, ADR-0018 PR4).

The preflight's own evaluation, re-run from the private files and read as one
verdict per chapter. **PASS** is a chapter the preflight finds eligible. **HOLD**
is a chapter waiting for something no one has given yet: a review decision,
an evidence file, a checkpoint, the owner's authorization, or a body the
extractor withholds. **DENY** is a chapter where something given is wrong,
changed, contradicted, revoked or expired. A reason not named here as waiting
is DENY, so a new reason code can never read as HOLD by accident.

A missing saved page is DENY too: the preflight finds a page by the digest
its observation recorded, so a page whose bytes drifted since it was read
looks exactly like one never supplied, and drift must never read as waiting.

No new check lives here, and nothing is cleared: a PASS is a technical result,
never release clearance. The report written is the preflight's own, private
in the workspace, and every line printed is codes, digests, counts and source
URLs only.
"""

from collections.abc import Iterable
from enum import StrEnum

from lovspor.forarbeider.identity import parse_source_url
from lovspor.forarbeider.release_commands import ChapterScope, Run, open_release_workspace
from lovspor.forarbeider.release_commands import preflight_inputs as _preflight_inputs
from lovspor.forarbeider.release_models import ChapterOutcome, DenyReason, PreflightReport
from lovspor.forarbeider.release_preflight import evaluate
from lovspor.forarbeider.release_store import write_record


class Verdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - a verdict label, not a credential
    HOLD = "HOLD"
    DENY = "DENY"


#: What a person has not given yet. Every other reason is something given that is wrong.
WAITING = frozenset(
    {
        DenyReason.AUTHORIZATION_MISSING,
        DenyReason.AUTHORIZATION_NOT_YET_VALID,
        DenyReason.BODY_WITHHELD,
        DenyReason.CHECKPOINT_MISSING,
        DenyReason.DOSSIER_CHAPTER_MISSING,
        DenyReason.DOSSIER_MISSING,
        DenyReason.EVIDENCE_INCOMPLETE,
        DenyReason.NOT_AUTHORIZED,
        DenyReason.OBSERVATION_MISSING,
        DenyReason.PURPOSE_EVIDENCE_MISSING,
        DenyReason.PURPOSE_NOT_AUTHORIZED,
        DenyReason.REVIEW_MISSING,
        DenyReason.REVIEW_NOT_APPROVED,
    }
)
CLEARANCE = "technical audit only: PASS is not release clearance"


def verdict_of(item: ChapterOutcome, document_reasons: Iterable[DenyReason]) -> Verdict:
    """PASS only when eligible; HOLD only when every reason is a waiting one."""
    reasons = {*item.reasons, *document_reasons}
    if item.eligible and not reasons:
        return Verdict.PASS
    if not reasons and not item.missing_evidence:
        return Verdict.DENY
    return Verdict.HOLD if reasons <= WAITING else Verdict.DENY


def chapter_url(report: PreflightReport, number: int) -> str:
    locator = parse_source_url(report.document.source_url)
    return locator.model_copy(update={"chapter": number}).page_url


def _chapter_line(report: PreflightReport, item: ChapterOutcome) -> str:
    verdict = verdict_of(item, report.document_reasons)
    codes: list[str] = [*report.document_reasons, *item.reasons]
    codes += [f"missing:{gate}" for gate in item.missing_evidence]
    return (
        f"chapter {item.chapter_number} {verdict} source {item.source_sha256 or '-'} "
        f"text {item.text_sha256 or '-'} url {chapter_url(report, item.chapter_number)} "
        f"reasons {','.join(sorted(set(codes))) or '-'}"
    )


def audit_lines(report: PreflightReport, sha: str) -> tuple[str, bool]:
    """The audit as printed, and whether every audited chapter passed."""
    document = report.document
    verdicts = [verdict_of(item, report.document_reasons) for item in report.chapters]
    head = (
        f"PILOT_AUDIT {document.id} {document.document_type} {document.session} report {sha} "
        "publication_authorized=false release_clearance=false"
    )
    counts = (
        f"counts listed {len(report.listed_chapters)} audited {len(verdicts)} "
        + " ".join(f"{verdict.lower()} {verdicts.count(verdict)}" for verdict in Verdict)
        + f" coverage {report.coverage}"
    )
    lines = [head, counts]
    lines += [_chapter_line(report, item) for item in report.chapters]
    passed = bool(verdicts) and set(verdicts) == {Verdict.PASS}
    return "\n".join([*lines, CLEARANCE]), passed


def pilot_audit_impl(scope: ChapterScope, run: Run) -> tuple[str, bool]:
    """Evaluate the staged chapters as the preflight does, store its report, print the audit.

    Raises:
        ForarbeiderRefusedError: an input could not be read safely; nothing was written.
    """
    report = evaluate(_preflight_inputs(scope, run))
    sha = write_record(open_release_workspace(scope.release), "reports", report)
    return audit_lines(report, sha)
