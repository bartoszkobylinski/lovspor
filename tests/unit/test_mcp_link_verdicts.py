"""Verdicts for sections resolved through an act link (#1034)."""

from pathlib import Path

import pytest

from lovspor.act_links import known_acts
from lovspor.mcp import CorpusReader, SectionIndex, _extract_cross_references
from tests.unit.test_mcp import _record, _seed_corpus


@pytest.mark.parametrize("complete", [True, False])
def test_missing_linked_section_uses_target_index_completeness(complete: bool) -> None:
    acts = known_acts({"source": ("nl-20180615-040", None), "target": ("nl-19690613-026", None)})
    indexes = {
        "source": SectionIndex(ids={"5-2"}, complete=True),
        "target": SectionIndex(ids=set(), complete=complete),
    }

    refs = _extract_cross_references(
        "[§ 5-2](lov/1969-06-13-26/§5-2)", "source", acts, indexes.__getitem__
    )

    assert len(refs) == 1
    assert refs[0]["target_slug"] == "target"
    assert refs[0]["target_section_id"] == "5-2"
    assert refs[0]["valid"] is (False if complete else None)
    assert "target" in refs[0]["reason"]
    assert ("incomplete" in refs[0]["reason"]) is (not complete)


def test_regulation_link_resolves_through_reader(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-20180615-040": _record(slug="source", title="Source"),
            "sf-20040601-0930": _record(slug="regulation", title="Regulation"),
        },
        body_for={
            "source": "### § 1. Source\n\n[§ 2](forskrift/2004-06-01-930/§2)\n",
            "regulation": "### § 2. Target\n\nText.\n",
        },
    )

    refs = CorpusReader(tmp_path).get_section("source", "1")["cross_references"]

    assert refs == [
        {
            "text": "§ 2",
            "target_slug": "regulation",
            "target_section_id": "2",
            "valid": True,
            "reason": None,
        }
    ]
