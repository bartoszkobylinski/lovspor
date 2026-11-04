"""A private real-document pilot audit, chapter by chapter (#738, ADR-0018 PR4).

The preflight's own evaluation, re-run from the private files and read as one
verdict per chapter. **PASS** is a chapter the preflight finds eligible. **HOLD**
is a chapter waiting for something no one has given yet: a review decision,
an evidence file, a checkpoint, the owner's authorization, or a body the
extractor withholds. **DENY** is a chapter where something given is wrong,
changed, contradicted, rejected, excluded, revoked or expired. A reason not
named here as waiting is DENY, so a new reason code can never read as HOLD by
accident.

Some reasons cover both. ``review_not_approved`` is a pending, held or
rejected review; ``purpose_not_authorized`` and ``purpose_evidence_missing``
are a purpose absent or one that differs from the owner's. They are read
against the same verified inputs the preflight read: a rejection as the
latest decision of the chapter's verified chain, or a purpose the dossier
does declare, is DENY. ``not_authorized`` only arises when the owner's
authorization exists and leaves the chapter out, so it is always DENY: the
owner excluded it. None of this changes the preflight report or its digest.

A missing saved page is DENY too: the preflight finds a page by the digest
its observation recorded, so a page whose bytes drifted since it was read
looks exactly like one never supplied, and drift must never read as waiting.

No new check lives here, and nothing is cleared: a PASS is a technical result,
never release clearance. The report written is the preflight's own, private
in the workspace, and every line printed is codes, digests, counts and source
URLs only.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import NamedTuple

from lovspor.forarbeider.identity import parse_source_url
from lovspor.forarbeider.release_commands import ChapterScope, Run, open_release_workspace
from lovspor.forarbeider.release_commands import preflight_inputs as _preflight_inputs
from lovspor.forarbeider.release_models import (
    ChapterOutcome,
    DenyReason,
    EvidenceKind,
    PreflightReport,
)
from lovspor.forarbeider.release_preflight import Observed, PreflightInputs, evaluate
from lovspor.forarbeider.release_store import write_record
from lovspor.forarbeider.review_models import ReviewStatus


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
        DenyReason.OBSERVATION_MISSING,
        DenyReason.PURPOSE_EVIDENCE_MISSING,
        DenyReason.PURPOSE_NOT_AUTHORIZED,
        DenyReason.REVIEW_MISSING,
        DenyReason.REVIEW_NOT_APPROVED,
    }
)
CLEARANCE = "technical audit only: PASS is not release clearance"
_NOTHING: frozenset[DenyReason] = frozenset()


class Firm(NamedTuple):
    """Waiting reasons the inputs show are about something given: DENY, not HOLD."""

    document: frozenset[DenyReason] = _NOTHING
    chapters: Mapping[int, frozenset[DenyReason]] = MappingProxyType({})

    def of(self, number: int) -> frozenset[DenyReason]:
        return self.document | self.chapters.get(number, _NOTHING)


#: No reason known firm: the classification of the preflight report alone.
NO_FIRM = Firm()


def verdict_of(
    item: ChapterOutcome,
    document_reasons: Iterable[DenyReason],
    firm: frozenset[DenyReason] = _NOTHING,
) -> Verdict:
    """PASS only when eligible; HOLD only when every reason is one still waiting."""
    reasons = {*item.reasons, *document_reasons}
    if item.eligible and not reasons:
        return Verdict.PASS
    if not reasons and not item.missing_evidence:
        return Verdict.DENY
    return Verdict.HOLD if reasons <= WAITING - firm else Verdict.DENY


def firm_of(inputs: PreflightInputs) -> Firm:
    """What the verified inputs say a waiting reason means, chapter by chapter.

    A chapter staged twice is denied as ``chapter_duplicate`` whichever copy is
    read here, so the copies need no order.
    """
    declared = _purposes(inputs)
    document = {DenyReason.PURPOSE_EVIDENCE_MISSING} if declared else set()
    chapters = {
        item.result.chapter_number: frozenset(_firm_chapter(item, declared))
        for item in inputs.observed
    }
    return Firm(frozenset(document), chapters)


def _purposes(inputs: PreflightInputs) -> list[tuple[int, ...]]:
    """The chapters each purpose in the dossier is declared for."""
    if inputs.dossier is None:
        return []
    evidence = inputs.dossier.record.evidence
    return [item.chapters for item in evidence if item.kind is EvidenceKind.PUBLICATION_PURPOSE]


def _firm_chapter(observed: Observed, declared: list[tuple[int, ...]]) -> set[DenyReason]:
    """A rejection on the verified chain, or a purpose declared for this chapter."""
    firm: set[DenyReason] = set()
    history = observed.history
    if history and history[-1].decision.status is ReviewStatus.REJECTED:
        firm.add(DenyReason.REVIEW_NOT_APPROVED)
    if any(observed.result.chapter_number in chapters for chapters in declared):
        firm.add(DenyReason.PURPOSE_NOT_AUTHORIZED)
    return firm


def chapter_url(report: PreflightReport, number: int) -> str:
    locator = parse_source_url(report.document.source_url)
    return locator.model_copy(update={"chapter": number}).page_url


def _chapter_line(report: PreflightReport, item: ChapterOutcome, firm: Firm) -> str:
    verdict = verdict_of(item, report.document_reasons, firm.of(item.chapter_number))
    codes: list[str] = [*report.document_reasons, *item.reasons]
    codes += [f"missing:{gate}" for gate in item.missing_evidence]
    return (
        f"chapter {item.chapter_number} {verdict} source {item.source_sha256 or '-'} "
        f"text {item.text_sha256 or '-'} url {chapter_url(report, item.chapter_number)} "
        f"reasons {','.join(sorted(set(codes))) or '-'}"
    )


def audit_lines(report: PreflightReport, sha: str, firm: Firm = NO_FIRM) -> tuple[str, bool]:
    """The audit as printed, and whether every audited chapter passed."""
    document = report.document
    reasons = report.document_reasons
    verdicts = [verdict_of(item, reasons, firm.of(item.chapter_number)) for item in report.chapters]
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
    lines += [_chapter_line(report, item, firm) for item in report.chapters]
    passed = bool(verdicts) and set(verdicts) == {Verdict.PASS}
    return "\n".join([*lines, CLEARANCE]), passed


def pilot_audit_impl(scope: ChapterScope, run: Run) -> tuple[str, bool]:
    """Evaluate the staged chapters as the preflight does, store its report, print the audit.

    Raises:
        ForarbeiderRefusedError: an input could not be read safely; nothing was written.
    """
    inputs = _preflight_inputs(scope, run)
    report = evaluate(inputs)
    sha = write_record(open_release_workspace(scope.release), "reports", report)
    return audit_lines(report, sha, firm_of(inputs))
