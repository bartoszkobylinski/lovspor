"""Phase 2 (#744): list items by number or letter, with or without a ledd, and new chapters."""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime import replay
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Address, Operation, classify, interpret
from lovspor.legaltime.replay import Event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
DATED = Timing(status="dated", effective=date(2022, 1, 1))
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))
INTRO = "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:"


def _amending(*articles: str) -> Document:
    body = (
        '<html><body><header><dl><dd class="refid">lov/2021-06-01-7</dd></dl></header>'
        f'<main><section class="section"><h2>I</h2><article class="defaultP">{INTRO}</article>'
        f"{''.join(articles)}</section></main></body></html>"
    )
    return parse_document(body.encode(), "lti/2021/x.xml")


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _t(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _item(label: str, text: str) -> str:
    return (
        f'<ul class="defaultList"><li data-name="{label}"><article class="listArticle">'
        f'<article class="legalP">{text}</article></article></li></ul>'
    )


def _ops(*articles: str) -> tuple[Operation, ...]:
    return interpret(_amending(*articles)).operations


def _state(*articles: str, at: date = date(2023, 1, 1)):
    events = [
        Event(
            sequence=i,
            law="lov/2021-06-01-7",
            member="m",
            operation=op,
            timing=DATED,
            published_at=None,
        )
        for i, op in enumerate(_ops(*articles))
    ]
    baseline = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    return replay.replay(baseline, ENACTED, events, at)


def _ledd(state, key: str) -> list[str]:
    return [ledd.text for p in state.provisions if p.key == key for ledd in p.ledd]


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(
    "sentence, kind, address",
    [
        (
            "§ 16 første ledd nr. 2 skal lyde:",
            "replace_bokstav",
            Address(section="§16", ledd=1, bokstav="2"),
        ),
        ("§ 16 nr. 2 skal lyde:", "replace_bokstav", Address(section="§16", bokstav="2")),
        ("§ 16 nr. 2 lyde:", "replace_bokstav", Address(section="§16", bokstav="2")),
        ("§ 16 bokstav c skal lyde:", "replace_bokstav", Address(section="§16", bokstav="c")),
        (
            "§ 16 første ledd ny nr. 3 skal lyde:",
            "insert_bokstav",
            Address(section="§16", ledd=1, bokstav="3"),
        ),
        (
            "§ 16 første ledd ny bokstav d skal lyde:",
            "insert_bokstav",
            Address(section="§16", ledd=1, bokstav="d"),
        ),
        (
            "§ 3-11 annet ledd ny bokstav l skal lyde:",
            "insert_bokstav",
            Address(section="§3-11", ledd=2, bokstav="l"),
        ),
        ("§ 16 nr. 2 oppheves.", "repeal_bokstav", Address(section="§16", bokstav="2")),
        (
            "§ 16 første ledd bokstav c oppheves.",
            "repeal_bokstav",
            Address(section="§16", ledd=1, bokstav="c"),
        ),
        (
            "I § 6A-7 skal ny bokstav q lyde:",
            "insert_bokstav",
            Address(section="§6A-7", bokstav="q"),
        ),
    ],
)
def test_item_instructions(sentence: str, kind: str, address: Address) -> None:
    got_kind, _groups = classify(sentence)
    assert got_kind == kind
    ops = _ops(_p(sentence), _item("x.", "x"))
    assert ops[0].address == address


def test_item_instructions_with_punktum_or_several_items_stay_unsupported() -> None:
    assert classify("§ 16 nr. 2 første punktum skal lyde:")[0] == "unsupported"
    assert classify("§ 67 første ledd bokstav c og d skal lyde:")[0] == "unsupported"


# ------------------------------------------------ application on § 2 (one ledd, items a and b)


def test_replace_an_item_by_number_or_letter_without_a_ledd_on_a_one_ledd_section() -> None:
    state = _state(_p("§ 2 bokstav b skal lyde:"), _item("b.", "bokstav b ny"))
    assert state.unresolved == ()
    assert "bokstav b ny" in _ledd(state, "§2")[0] and "bokstav a" in _ledd(state, "§2")[0]


def test_an_item_without_a_ledd_on_a_section_with_several_ledd_is_refused() -> None:
    state = _state(_p("§ 1 bokstav a skal lyde:"), _item("a.", "x"))
    assert [problem for _, problem in state.unresolved] == [
        "item without ledd on a provision with several ledd"
    ]


def test_insert_an_item_after_its_predecessor() -> None:
    state = _state(_p("§ 2 første ledd ny bokstav c skal lyde:"), _item("c.", "bokstav c"))
    assert state.unresolved == ()
    text = _ledd(state, "§2")[0]
    assert text.index("bokstav b") < text.index("bokstav c") < text.index("Bokstav b gjelder ikke")
    xml = next(p for p in state.provisions if p.key == "§2").ledd[0].xml
    assert xml.index('data-name="b)"') < xml.index('data-name="c."')


def test_insert_an_item_whose_predecessor_is_missing_is_refused() -> None:
    state = _state(_p("§ 2 første ledd ny bokstav e skal lyde:"), _item("e.", "bokstav e"))
    assert [problem for _, problem in state.unresolved] == [
        "item d, the one before e, is not in the ledd"
    ]


def test_insert_an_item_that_already_exists_is_refused() -> None:
    state = _state(_p("§ 2 første ledd ny bokstav b skal lyde:"), _item("b.", "again"))
    assert [problem for _, problem in state.unresolved] == ["item b is already in the ledd"]


def test_repeal_an_item() -> None:
    state = _state(_p("§ 2 første ledd bokstav a oppheves."))
    assert state.unresolved == ()
    text = _ledd(state, "§2")[0]
    assert "bokstav a" not in text and "bokstav b" in text
    missing = _state(_p("§ 2 første ledd bokstav x oppheves."))
    assert [problem for _, problem in missing.unresolved] == ["bokstav x matched 0 list items"]


def test_item_payload_must_be_exactly_one_text() -> None:
    state = _state(_p("§ 2 første ledd ny bokstav c skal lyde:"), _t("one"), _t("two"))
    assert [problem for _, problem in state.unresolved] == ["item payload is not one text"]


# ---------------------------------------------------------------- new chapters


def test_a_new_chapter_is_one_insertion_per_provision_it_carries() -> None:
    ops = _ops(
        _p("Nytt kapittel 6A skal lyde:"),
        '<article class="futuretitle">Kapittel 6A. Prøver</article>',
        '<article class="futureLegalArticle" data-name="§6A-1"><article class="legalP">En.</article></article>',  # noqa: E501
        '<article class="futureLegalArticle" data-name="§6A-2"><article class="legalP">To.</article></article>',  # noqa: E501
    )
    assert [(op.kind, op.address.section) for op in ops] == [
        ("insert_section", "§6A-1"),
        ("insert_section", "§6A-2"),
    ]
    assert len({op.id for op in ops}) == 2
    state = _state(
        _p("Nytt kapittel 6A skal lyde:"),
        '<article class="futureLegalArticle" data-name="§6A-1"><article class="legalP">En.</article></article>',  # noqa: E501
        '<article class="futureLegalArticle" data-name="§6A-2"><article class="legalP">To.</article></article>',  # noqa: E501
    )
    assert state.unresolved == () and [
        p.key for p in state.provisions if p.key.startswith("§6A")
    ] == ["§6A-1", "§6A-2"]


def test_a_new_chapter_without_provisions_is_unsupported() -> None:
    ops = _ops(
        _p("Nytt kapittel 6A skal lyde:"), '<article class="futuretitle">Kapittel 6A.</article>'
    )
    assert [op.kind for op in ops] == ["unsupported"]
