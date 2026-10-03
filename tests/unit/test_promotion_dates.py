"""Dates as a regulation's text states them (ADR-0016 Decision 2)."""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion.dates import has_placeholder_date, parse_stated_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12. desember 2019", date(2019, 12, 12)),
        ("1.2.2020", date(2020, 2, 1)),
        ("2020-02-01", date(2020, 2, 1)),
        ("12. desembre 2019", None),
        ("20200201", None),
        ("12.12.19", None),
        ("2020-02-30", None),
    ],
)
def test_parse_stated_date(text: str, expected: date | None) -> None:
    assert parse_stated_date(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("trer i kraft X.X.2016", True),
        ("vedtatt xx.xx.2016", True),
        ("dato dd.mm.åååå", True),
        ("vedtatt __.__.2016", True),
        ("vedtatt 12.12.2016", False),
        ("Box.xx.2016", False),
    ],
)
def test_placeholder_dates(text: str, expected: bool) -> None:
    assert has_placeholder_date(text) is expected
