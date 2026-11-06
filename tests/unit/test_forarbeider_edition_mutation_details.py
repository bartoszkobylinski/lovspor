"""Refusals for incomplete landing pages and malformed edition evidence."""

import pytest
from lxml import html

from lovspor.forarbeider.edition import read_landing
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.test_forarbeider_edition import CORRECTED, LS_URL


@pytest.mark.parametrize(
    ("remove", "reason", "detail"),
    [
        ("//footer", RefusalReason.INCOMPLETE_PAGE, "page footer"),
        ("//ul/li/a", RefusalReason.EDITION_UNRECOGNIZED, "edition list item"),
        ("//ul/li", RefusalReason.EDITION_CONFLICTING, "edition links"),
        ("//ul", RefusalReason.EDITION_UNRECOGNIZED, "edition block"),
    ],
    ids=["missing-footer", "empty-list-item", "empty-list", "heading-without-list"],
)
def test_landing_refusal_identifies_missing_or_malformed_evidence(
    remove: str, reason: RefusalReason, detail: str
) -> None:
    root = html.document_fromstring(CORRECTED.read_bytes())
    nodes = root.xpath(remove)
    assert nodes
    for node in nodes:
        node.getparent().remove(node)
    payload = html.tostring(root, encoding="utf-8")

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(payload, LS_URL)

    assert caught.value.reason is reason
    assert caught.value.detail == detail
