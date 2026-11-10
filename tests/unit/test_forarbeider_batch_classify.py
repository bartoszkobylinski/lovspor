"""Every staged chapter falls in exactly one class, and only clean can meet a policy (#738).

A2b (contract Amendment 1 point 3): withheld always denies, a detector hit
routes to the per-chapter review, a reason no set names is ``unknown`` and
routes the same way, and only a chapter with a body and nothing but
``review_required`` is ``clean``.
"""

import pytest

from lovspor.forarbeider.batch_classify import (
    DETECTORS,
    NEUTRAL,
    WITHHELD,
    ChapterClass,
    classify,
    classify_result,
    needs_review,
)
from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import WITHHOLDING, HoldReason
from tests.unit.forarbeider_release_support import RETRIEVED, moment, page

EXPECTED = {
    HoldReason.REVIEW_REQUIRED: ChapterClass.CLEAN,
    HoldReason.ANNEX: ChapterClass.WITHHELD,
    HoldReason.IMAGE: ChapterClass.WITHHELD,
    HoldReason.TABLE: ChapterClass.WITHHELD,
    HoldReason.FIGURE: ChapterClass.WITHHELD,
    HoldReason.FOOTNOTES: ChapterClass.WITHHELD,
    HoldReason.AMBIGUOUS_BOUNDARY: ChapterClass.WITHHELD,
    HoldReason.DAMAGED_DOM: ChapterClass.WITHHELD,
    HoldReason.UNSUPPORTED_STRUCTURE: ChapterClass.WITHHELD,
    HoldReason.HIDDEN_CONTENT: ChapterClass.WITHHELD,
    HoldReason.MALFORMED_BODY: ChapterClass.WITHHELD,
    HoldReason.INCOMPLETE_EXTRACTION: ChapterClass.WITHHELD,
    HoldReason.QUOTATION: ChapterClass.FLAGGED,
    HoldReason.THIRD_PARTY_WORK: ChapterClass.FLAGGED,
    HoldReason.HEARING_PARTICIPANTS: ChapterClass.FLAGGED,
    HoldReason.PERSONAL_DATA: ChapterClass.FLAGGED,
    HoldReason.SENSITIVE_DATA: ChapterClass.FLAGGED,
}


def test_every_hold_reason_is_in_exactly_one_set() -> None:
    sets = (NEUTRAL, DETECTORS, WITHHELD)

    assert set(HoldReason) == NEUTRAL | DETECTORS | WITHHELD
    assert sum(len(item) for item in sets) == len(HoldReason)
    assert set(EXPECTED) == set(HoldReason)


def test_the_withheld_set_is_the_staging_withholding_set_itself() -> None:
    assert WITHHELD is WITHHOLDING


@pytest.mark.parametrize("reason", list(HoldReason))
def test_each_member_alone_after_review_required_has_its_class(reason: HoldReason) -> None:
    reasons = (HoldReason.REVIEW_REQUIRED, reason)
    has_body = reason not in WITHHOLDING

    assert classify(reasons, has_body) is EXPECTED[reason]


@pytest.mark.parametrize(
    ("reasons", "has_body", "expected"),
    [
        (("review_required", "table", "quotation"), False, ChapterClass.WITHHELD),
        (("review_required", "table", "future_reason"), False, ChapterClass.WITHHELD),
        (("review_required", "personal_data"), True, ChapterClass.FLAGGED),
        (("review_required", "quotation", "sensitive_data"), True, ChapterClass.FLAGGED),
        (("review_required",), True, ChapterClass.CLEAN),
        ((), True, ChapterClass.CLEAN),
        (("review_required", "quotation", "future_reason"), True, ChapterClass.UNKNOWN),
        (("review_required", "future_reason"), True, ChapterClass.UNKNOWN),
        (("review_required",), False, ChapterClass.WITHHELD),
        (("review_required", "future_reason"), False, ChapterClass.WITHHELD),
    ],
)
def test_mixed_reasons_take_the_strictest_class(
    reasons: tuple[str, ...], has_body: bool, expected: ChapterClass
) -> None:
    assert classify(reasons, has_body) is expected


def test_only_flagged_and_unknown_need_the_per_chapter_review() -> None:
    assert {kind for kind in ChapterClass if needs_review(kind)} == {
        ChapterClass.FLAGGED,
        ChapterClass.UNKNOWN,
    }


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("", ChapterClass.CLEAN),
        ("<blockquote><p>Sitat.</p></blockquote>", ChapterClass.FLAGGED),
        ("<p>Se høring.</p>", ChapterClass.FLAGGED),
        ("<p>ola@example.no</p>", ChapterClass.FLAGGED),
        ("<table><tr><td>x</td></tr></table>", ChapterClass.WITHHELD),
    ],
)
def test_a_built_result_is_classified_from_its_own_reasons_and_body(
    extra: str, expected: ChapterClass
) -> None:
    payload, url = page("l", 3, extra)
    result = build_chapter_result(payload, url, moment(RETRIEVED))

    assert classify_result(result) is expected
