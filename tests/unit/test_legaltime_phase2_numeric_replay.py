"""Numbered-item replay promised by phase 2, beyond instruction classification."""

from datetime import date

import pytest
from lxml import etree

from lovspor.legaltime import replay
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import parse_document
from tests.unit.test_legaltime_phase2_items import ENACTED, FIXTURES, _ops, _p, _t


def _baseline():
    xml = (FIXTURES / "enacting.xml").read_bytes()
    # Keep the real fixture structure, changing only the two list labels.
    return parse_document(xml.replace(b"a)", b"1)").replace(b"b)", b"2)"), "numbered.xml")


def _event(instruction: str, payload: str | None, sequence: int) -> replay.Event:
    articles = [_p(instruction)]
    if payload is not None:
        articles.append(_t(payload))
    (operation,) = _ops(*articles)
    return replay.Event(
        sequence=sequence,
        law="lov/2021-06-01-7",
        member=f"amendment-{sequence}.xml",
        operation=operation,
        timing=Timing(status="dated", effective=date(2022, sequence + 1, 1)),
        published_at=None,
    )


def _items(state):
    provision = next(p for p in state.provisions if p.key == "§2")
    root = etree.fromstring(f"<article>{provision.ledd[0].xml}</article>")
    return [(item.get("data-name"), "".join(item.itertext())) for item in root.iter("li")]


def test_numbered_item_lifecycle_obeys_dates_and_replays_without_mutating_baseline() -> None:
    baseline = _baseline()
    before = baseline.model_dump_json()
    events = [
        _event("§ 2 ny nr. 3 skal lyde:", "Tredje.", 0),
        _event("§ 2 nr. 2 skal lyde:", "Endret.", 1),
        _event("§ 2 nr. 1 oppheves.", None, 2),
    ]
    expected = [
        [("1)", "bokstav a"), ("2)", "bokstav b")],
        [("1)", "bokstav a"), ("2)", "bokstav b"), ("3.", "Tredje.")],
        [("1)", "bokstav a"), ("2)", "Endret."), ("3.", "Tredje.")],
        [("2)", "Endret."), ("3.", "Tredje.")],
    ]
    for count, at in enumerate(
        [date(2021, 12, 31), date(2022, 1, 1), date(2022, 2, 1), date(2022, 3, 1)]
    ):
        state = replay.replay(baseline, ENACTED, events, at)
        assert state.unresolved == ()
        assert state.applied == tuple(e.operation.id for e in events[:count])
        assert state.future == tuple(e.operation.id for e in events[count:])
        assert _items(state) == expected[count]
        assert (
            replay.replay(baseline, ENACTED, events, at).model_dump_json()
            == state.model_dump_json()
        )
        assert baseline.model_dump_json() == before


@pytest.mark.parametrize(
    "instruction,payload,reason",
    [
        ("§ 2 ny nr. 2 skal lyde:", "Duplikat.", "item 2 is already in the ledd"),
        ("§ 2 ny nr. 4 skal lyde:", "Hull.", "item 3, the one before 4, is not in the ledd"),
        ("§ 2 nr. 3 oppheves.", None, "bokstav 3 matched 0 list items"),
    ],
)
def test_refused_numbered_item_edit_keeps_provision_content(instruction, payload, reason) -> None:
    baseline = _baseline()
    original = next(p for p in baseline.provisions if p.key == "§2")
    event = _event(instruction, payload, 0)
    state = replay.replay(baseline, ENACTED, [event], date(2022, 1, 1))
    assert state.applied == ()
    assert state.unresolved == ((event.operation.id, reason),)
    provision = next(p for p in state.provisions if p.key == "§2")
    assert provision.ledd == original.ledd
    assert provision.status == "unresolved"


@pytest.mark.parametrize(
    "instruction,payload,reason",
    [
        ("§ 2 ny nr. 3 skal lyde:", "Ny.", "item 2, the one before 3, is in 2 places"),
        ("§ 2 nr. 2 oppheves.", None, "bokstav 2 matched 2 list items"),
        ("§ 2 nr. 2 skal lyde:", "Endret.", "bokstav 2 matched 2 list items"),
    ],
)
def test_ambiguous_numbered_item_edit_is_unresolved_and_preserves_all_text(
    instruction, payload, reason
) -> None:
    # Duplicate labels are ambiguous: replay must never guess which item is addressed.
    xml = (FIXTURES / "enacting.xml").read_bytes()
    baseline = parse_document(xml.replace(b"a)", b"2)").replace(b"b)", b"2)"), "duplicate.xml")
    original = next(p for p in baseline.provisions if p.key == "§2")
    before = baseline.model_dump_json()
    event = _event(instruction, payload, 0)

    state = replay.replay(baseline, ENACTED, [event], date(2022, 1, 1))

    assert state.applied == ()
    assert state.unresolved == ((event.operation.id, reason),)
    provision = next(p for p in state.provisions if p.key == "§2")
    assert provision.ledd == original.ledd
    assert provision.status == "unresolved"
    assert provision.problems == (f"{event.operation.id}: {reason}",)
    assert baseline.model_dump_json() == before
