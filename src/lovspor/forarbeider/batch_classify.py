"""Which clearance path a staged chapter may take (#738, contract Amendment 1 point 3).

Four classes, decided from the chapter's own hold reasons and body alone:

- ``withheld``: no body, or any :data:`WITHHOLDING` reason. Never released.
- ``unknown``: a reason no set below names. It routes like ``flagged`` and is
  never cleared by a standing policy, so a member added to the hold reasons
  later fails closed until someone categorizes it.
- ``flagged``: a detector fired. Only the per-chapter review can clear it.
- ``clean``: a body and nothing but ``review_required``. Only this class can
  meet a standing policy, and a detector staying silent is not a finding that
  the chapter is free of personal data or third-party work.
"""

from collections.abc import Iterable
from enum import StrEnum

from lovspor.forarbeider.chapter_models import WITHHOLDING, ChapterResult, HoldReason


class ChapterClass(StrEnum):
    CLEAN = "clean"
    FLAGGED = "flagged"
    WITHHELD = "withheld"
    UNKNOWN = "unknown"


#: Present on every staged result; it says nothing about the chapter by itself.
NEUTRAL: frozenset[str] = frozenset({HoldReason.REVIEW_REQUIRED})
#: A body exists, and a detector asks a person to read it.
DETECTORS: frozenset[str] = frozenset(
    {
        HoldReason.QUOTATION,
        HoldReason.THIRD_PARTY_WORK,
        HoldReason.HEARING_PARTICIPANTS,
        HoldReason.PERSONAL_DATA,
        HoldReason.SENSITIVE_DATA,
    }
)
#: The staging set itself, never a copy: what withholds a body withholds a release.
WITHHELD: frozenset[str] = WITHHOLDING


def classify(reasons: Iterable[str], has_body: bool) -> ChapterClass:
    """Withheld first, then unknown, then flagged; clean only when nothing else applies."""
    given = {str(reason) for reason in reasons}
    if not has_body or given & WITHHELD:
        return ChapterClass.WITHHELD
    if given - NEUTRAL - DETECTORS:
        return ChapterClass.UNKNOWN
    if given & DETECTORS:
        return ChapterClass.FLAGGED
    return ChapterClass.CLEAN


def classify_result(result: ChapterResult) -> ChapterClass:
    return classify(result.hold_reasons, result.blocks is not None)


def needs_review(kind: ChapterClass) -> bool:
    """Flagged and unknown chapters clear only through the per-chapter review."""
    return kind in {ChapterClass.FLAGGED, ChapterClass.UNKNOWN}
