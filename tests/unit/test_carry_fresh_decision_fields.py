"""A fresh approval supplies the published audit's decision fields."""

from lovspor.promotion.carry import _decided
from lovspor.promotion.decisions import utc_text
from lovspor.promotion.extract import EXTRACTOR_VERSION
from tests.unit.test_carry_mutation_remediation import decision


def test_fresh_decision_fields_preserve_the_published_decision_key() -> None:
    approval = decision()

    fields = _decided(approval)

    assert fields == {
        "decision": approval.decision,
        "reviewed_by_role": approval.reviewer_role,
        "decided_at": utc_text(approval.decided_at),
        "reason": approval.reason,
        "classifier": approval.classifier,
        "extractor_version": EXTRACTOR_VERSION,
        "approval_carried": None,
    }
