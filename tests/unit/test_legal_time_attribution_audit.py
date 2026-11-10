"""The Phase 4B audit tool never certifies an attribution its evidence does not prove (#861)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.text import split_sentences

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/legal_time_attribution_audit.py"
TVISTELOVEN = "lov/2005-06-17-90"
STRAFFELOVEN = "lov/2005-05-20-28"


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


def test_an_operation_that_only_exists_after_the_change_is_never_classified() -> None:
    """``before is None``: a brand-new op id is ``check``, never read as a rebinding."""
    sentence = "I tvisteloven 17. juni 2005 nr. 90 gjøres følgende endringer:"
    after = ["x#0:I:2.0", TVISTELOVEN, "replace_ledd", "§ 2 skal lyde:"]
    row = _audit()._row(None, after, sentence, [TVISTELOVEN])
    assert row["class"] == "check"


FRISKOLELOVA = "lov/2003-07-04-84"


@pytest.mark.parametrize(
    ("old_act", "changes", "expected"),
    [
        (None, [FRISKOLELOVA], "intro_not_an_operation"),  # unattributed before, now not an op
        (FRISKOLELOVA, [FRISKOLELOVA], "check"),  # the old act was real: a lost op, not an intro
        (None, [], "check"),  # the sentence's act is not proven: never certified as an intro
    ],
    ids=["unattributed-before-proven", "attributed-before", "unproven"],
)
def test_an_operation_that_disappears_is_an_intro_only_when_unattributed_and_proven(
    old_act: str | None, changes: list[str], expected: str
) -> None:
    """``after is None``: the ``intro_not_an_operation`` class per the module docstring."""
    sentence = "I lov 4. juli 2003 nr. 84 om private skolar gjøres følgende endringer:"
    before = ["x#0:I:1.0", old_act, "unsupported", sentence]
    row = _audit()._row(before, None, sentence, changes)
    assert row["class"] == expected


def test_changed_lists_only_operations_whose_recorded_fields_differ() -> None:
    unchanged = ["x#0:I:2.0", "a", "replace_ledd", "§ 2 skal lyde:"]
    changed_op = ["x#0:I:1.0", None, "k", "s"]
    before = {"x": {"member": "m", "changes": [], "ops": [changed_op, unchanged]}}
    after_op = ["x#0:I:1.0", "b", "k", "s"]
    after = {"x": {"member": "m", "changes": [], "ops": [after_op, unchanged]}}
    assert _audit()._changed(before, after) == {"x": ["x#0:I:1.0"]}


def test_changed_reports_nothing_when_every_operation_is_identical() -> None:
    same = {"x": {"member": "m", "changes": [], "ops": [["x#0:I:1.0", "a", "k", "s"]]}}
    assert _audit()._changed(same, same) == {}


def _doc(body: str, *changes: str) -> Document:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    html = (
        '<html><body><header><dl><dd class="refid">lov/2015-06-19-65</dd>'
        '<dd class="dateInForce">2015-06-19</dd>'
        '<dd class="dateOfPublication">2015-06-19 15:00</dd>'
        '<dd class="title">Lov om ikraftsetting av straffeloven 2005</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()
    return parse_document(html, "lti/2015/nl-20150619-065.xml")


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _article(key: str, *children: str) -> str:
    return f'<article class="legalArticle" data-name="{key}">{"".join(children)}</article>'


def test_governing_returns_the_canonical_intro_that_opens_the_block() -> None:
    """``is_intro`` match: the full sentence is returned, not ``split_sentences``'s last one."""
    intro = "I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"
    body = _article("§3", _ledd(intro), _p("§ 40 oppheves."))
    document = _doc(body, STRAFFELOVEN)
    op = interpret(document).operations[0]
    assert _audit()._governing(document, op.id) == intro


def test_governing_returns_only_the_last_sentence_of_a_fused_intro_ledd() -> None:
    """lov/2014-05-09-16: the governing sentence is the intro, never the payload sentence ahead
    of it in the same ledd."""
    fused = (
        "(1) Utdeling til aksjeeierne kan ikke finne sted før selskapets forpliktelser er dekket. "
        "17. I lov 13. juni 1997 nr. 45 om allmennaksjeselskaper gjøres følgende endringer:"
    )
    body = _article(
        "§1",
        _ledd("I lov 13. juni 1997 nr. 44 om aksjeselskaper gjøres følgende endringer:"),
        _p("§ 8-1 skal lyde:"),
        _ledd(fused),
        _p("§ 3-8 første ledd nr. 6 skal lyde:"),
        _ledd("Ny."),
    )
    document = _doc(body, "lov/1997-06-13-44", "lov/1997-06-13-45")
    operations = interpret(document).operations
    assert operations[1].act == "lov/1997-06-13-45"  # the op the fused intro governs
    governing = _audit()._governing(document, operations[1].id)
    assert governing == split_sentences(fused)[-1]
    assert governing != fused  # only the intro sentence, not the payload prose ahead of it


def test_governing_is_empty_once_the_search_crosses_an_article_boundary() -> None:
    """No intro opens ``§6``: the walk back hits ``§3`` and stops, proving nothing."""
    intro = "I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"
    body = _article("§3", _ledd(intro), _p("§ 40 oppheves.")) + _article("§6", _p("§ 42 oppheves."))
    document = _doc(body, STRAFFELOVEN)
    operations = interpret(document).operations
    assert [op.act for op in operations] == [STRAFFELOVEN, None]
    assert _audit()._governing(document, operations[1].id) == ""


def test_losing_an_act_the_sentence_cites_second_is_never_called_false() -> None:
    """Independent review round 3: the whole citation set decides, not its first member."""
    sentence = (
        "I tvisteloven 17. juni 2005 nr. 90, slik den lyder etter lov 1. januar 2020 nr. 1, "
        "gjøres følgende endringer:"
    )
    row = _row(sentence, "lov/2020-01-01-1", None, [TVISTELOVEN, "lov/2020-01-01-1"])
    assert row["class"] == "check"
