"""Norwegian working days, for the offentleglova § 32 andre ledd mark.

§ 32 andre ledd: a requester who "ikkje har fått svar innan fem arbeidsdagar
etter at organet mottok kravet" may treat the silence as a refusal and
complain. A working day is a weekday that is not a public holiday
(helligdagsloven § 2 and the 1 and 17 May days in lov om 1. og 17. mai som
høgtidsdagar). The holidays that move are fixed relative to Easter Sunday,
computed here rather than taken from a dependency: the list is short, stable
and is the one thing the deadline must not get wrong.

The receipt day is the Oslo calendar day of the sending moment. An e-mail
sent on a weekend is still received that day; only the days after it count.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")

#: offentleglova § 32 andre ledd.
KLAGE_WORKING_DAYS = 5

#: Monday..Friday are weekday() 0..4.
_SATURDAY = 5

_FIXED_HOLIDAYS = ((1, 1), (5, 1), (5, 17), (12, 25), (12, 26))

#: Days from Easter Sunday: skjærtorsdag, langfredag, 1. and 2. påskedag,
#: Kristi himmelfartsdag, 1. and 2. pinsedag.
_EASTER_OFFSETS = (-3, -2, 0, 1, 39, 49, 50)


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_shift = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * weekday_shift) // 433
    month = (h + weekday_shift - 7 * m + 90) // 25
    day = (h + weekday_shift - 7 * m + 33 * month + 19) % 32
    return date(year, month, day)


def public_holidays(year: int) -> frozenset[date]:
    """Every Norwegian public holiday in ``year`` (Sundays aside)."""
    easter = easter_sunday(year)
    moving = {easter + timedelta(days=offset) for offset in _EASTER_OFFSETS}
    return frozenset(moving | {date(year, month, day) for month, day in _FIXED_HOLIDAYS})


def is_working_day(day: date) -> bool:
    return day.weekday() < _SATURDAY and day not in public_holidays(day.year)


def add_working_days(start: date, count: int) -> date:
    """The ``count``-th working day after ``start``; ``start`` itself never counts."""
    day = start
    remaining = count
    while remaining:
        day += timedelta(days=1)
        if is_working_day(day):
            remaining -= 1
    return day


def klage_mark(sent_at: datetime) -> date:
    """The last day of the § 32 andre ledd answer period for a request sent at ``sent_at``.

    A request without an answer by the end of this Oslo day may be appealed
    from the next day on.
    """
    return add_working_days(sent_at.astimezone(OSLO).date(), KLAGE_WORKING_DAYS)
