"""The Phase 4B audit tool never certifies an attribution its evidence does not prove (#861)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/legal_time_attribution_audit.py"
TVISTELOVEN = "lov/2005-06-17-90"


def _audit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("legal_time_attribution_audit", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(sentence: str, before: str | None, after: str | None, changes: list[str]) -> dict:
    old = ["x#0:I:1.0", before, "replace_ledd", "§ 1 skal lyde:"]
    new = ["x#0:I:1.0", after, "replace_ledd", "§ 1 skal lyde:"]
    return _audit()._row(old, new, sentence, changes)


def test_two_citations_are_never_proven_even_when_the_first_matches() -> None:
    """Independent review round 1: the first of two citations is not a unique identity."""
    sentence = (
        "I tvisteloven 17. juni 2005 nr. 90, slik den lyder etter lov 1. januar 2020 nr. 1, "
        "gjøres følgende endringer:"
    )
    row = _row(sentence, None, TVISTELOVEN, [TVISTELOVEN, "lov/2020-01-01-1"])
    assert (row["identity_proven"], row["class"]) == (False, "check")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [([TVISTELOVEN], (True, "new_proven")), ([], (False, "check"))],
)
def test_one_citation_is_proven_only_when_the_header_lists_it(
    changes: list[str], expected: tuple[bool, str]
) -> None:
    sentence = "I tvisteloven 17. juni 2005 nr. 90 gjøres følgende endringer:"
    row = _row(sentence, None, TVISTELOVEN, changes)
    assert (row["identity_proven"], row["class"]) == expected


def test_a_bound_act_the_sentence_does_not_cite_is_never_proven() -> None:
    sentence = "I tvisteloven 17. juni 2005 nr. 90 gjøres følgende endringer:"
    row = _row(sentence, None, "lov/2005-05-20-28", [TVISTELOVEN, "lov/2005-05-20-28"])
    assert row["class"] == "check"


def test_an_attribution_removed_from_an_act_the_sentence_does_not_cite_was_false() -> None:
    sentence = "2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves."
    row = _row(sentence, "lov/2005-05-20-28", None, ["lov/1946-12-13-14"])
    assert row["class"] == "unbound_was_false"
    kept = _row(sentence, "lov/1946-12-13-14", None, ["lov/1946-12-13-14"])
    assert kept["class"] == "check"  # it lost an act its own sentence names: never silent
