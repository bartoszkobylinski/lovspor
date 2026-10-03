"""Personal-data gate for promoted text (ADR-0016 Decision 6, personopplysningsloven).

``lovverk`` history is never rewritten (ADR-0003), so personal data must be
stopped before the first commit. This gate **finds and holds; it never
redacts** — a silent redaction changes legal text. What it reports is the
kind and the line, never the matched value, so the hold report does not
become a second copy of what it holds back.

What counts as a hit, each deliberately narrow and deterministic:

* ``fodselsnummer`` — eleven digits passing both mod-11 checks, with a
  plausible day and month (D-nummer day + 40, H-nummer and synthetic month
  + 40 / + 80 accepted);
* ``email`` — any e-mail address, a public office's included: a reviewer
  decides, the gate does not;
* ``phone`` — a Norwegian eight-digit number after ``+47``/``0047`` or a
  ``tlf``/``telefon``/``mobil`` label, or grouped as phone numbers are
  (``12 34 56 78``, ``123 45 678``); an amount like ``12 345 678`` is not;
* ``postal_address`` — street, house number and four-digit postcode, or a
  ``Postboks``; a street name alone is a regulation's legal content;
* ``contact_line`` — a ``Kontaktperson``/``Saksbehandler``/``Kontakt:`` line;
* ``signature`` — a line that is only a personal name, next to a line that
  is only an office title (``Kari Nordmann`` / ``ordfører``).

Property identifiers (gnr./bnr.) and Lovdata references are legal content
and are never hits. A personal name elsewhere in running text is not
detected: that needs judgement this gate does not claim, and is what the
human review of every first promotion is for (owner decision, 2026-10-03).
"""

from __future__ import annotations

import re

from lovspor.promotion.models import PersonalDataHit, PersonalDataKind

_MODULUS = 11
_D_NUMMER_DAY_OFFSET = 40
_H_NUMMER_MONTH_OFFSET = 40
_SYNTHETIC_MONTH_OFFSET = 80
_MAX_DAY = 31
_MAX_MONTH = 12
_FIRST_CHECK_WEIGHTS = (3, 7, 6, 1, 8, 9, 4, 5, 2)
_SECOND_CHECK_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_IDENTITY_NUMBER = re.compile(r"(?<!\d)(\d{6})\s?(\d{5})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_EIGHT_DIGITS = r"(?:\d\s?){7}\d(?!\d)"
_PHONE = re.compile(
    r"(?:\+|00)47\s?"
    + _EIGHT_DIGITS
    + r"|(?:tlf|telefon|tel|mobil|mob)\.?\s*:?\s*(?:\+47\s?)?"
    + _EIGHT_DIGITS
    + r"|(?<![\d,.])(?:\d{2} \d{2} \d{2} \d{2}|\d{3} \d{2} \d{3})(?![\d,])",
    re.IGNORECASE,
)
_STREET_SUFFIX = r"(?:veien|vegen|vei|veg|gata|gaten|gate|plassen|plass|stien|bakken|allé|alle)"
_POSTAL_ADDRESS = re.compile(
    r"\b[A-ZÆØÅ][\wæøå.-]*" + _STREET_SUFFIX + r"\s+\d+\s?[A-Za-z]?,?\s+\d{4}\s+[A-ZÆØÅ]"
    r"|\b(?i:postboks)\s+\d+"
)
_CONTACT_LINE = re.compile(r"(?:kontaktperson|saksbehandler)\b\s*:?\s*\S|kontakt\s*:\s*\S", re.I)
_PERSONAL_NAME = re.compile(r"[A-ZÆØÅ][a-zæøå]+(?:[ -][A-ZÆØÅ][a-zæøå]+){1,3}")
_OFFICE_TITLE = re.compile(
    r"(?:vara)?ordfører|rådmann|kommunedirektør|kommunalsjef|fylkesordfører|fylkesdirektør"
    r"|saksbehandler|leder|sekretær",
    re.IGNORECASE,
)


def screen_personal_data(text: str) -> tuple[PersonalDataHit, ...]:
    """Every hit in ``text``, ordered by line then kind; empty when the text is clear."""
    lines = text.splitlines()
    hits = {
        PersonalDataHit(kind=kind, line=number)
        for number, line in enumerate(lines, start=1)
        for kind in _line_kinds(line)
    }
    hits |= {PersonalDataHit(kind=PersonalDataKind.SIGNATURE, line=n) for n in _signatures(lines)}
    return tuple(sorted(hits, key=lambda hit: (hit.line, hit.kind.value)))


def _line_kinds(line: str) -> set[PersonalDataKind]:
    found = {
        PersonalDataKind.EMAIL: _EMAIL.search(line),
        PersonalDataKind.PHONE: _PHONE.search(line),
        PersonalDataKind.POSTAL_ADDRESS: _POSTAL_ADDRESS.search(line),
        PersonalDataKind.CONTACT_LINE: _CONTACT_LINE.match(line),
    }
    kinds = {kind for kind, match in found.items() if match is not None}
    if _has_identity_number(line):
        kinds.add(PersonalDataKind.FODSELSNUMMER)
    return kinds


def _has_identity_number(line: str) -> bool:
    return any(
        _is_identity_number(match.group(1) + match.group(2))
        for match in _IDENTITY_NUMBER.finditer(line)
    )


def _is_identity_number(number: str) -> bool:
    digits = [int(digit) for digit in number]
    first = _check_digit(digits[:9], _FIRST_CHECK_WEIGHTS)
    second = _check_digit(digits[:10], _SECOND_CHECK_WEIGHTS)
    if (first, second) != (digits[9], digits[10]):
        return False
    day, month = int(number[0:2]), int(number[2:4])
    day -= _D_NUMMER_DAY_OFFSET if day > _D_NUMMER_DAY_OFFSET else 0
    if month > _SYNTHETIC_MONTH_OFFSET:
        month -= _SYNTHETIC_MONTH_OFFSET
    elif month > _H_NUMMER_MONTH_OFFSET:
        month -= _H_NUMMER_MONTH_OFFSET
    return 1 <= day <= _MAX_DAY and 1 <= month <= _MAX_MONTH


def _check_digit(digits: list[int], weights: tuple[int, ...]) -> int:
    """The mod-11 control digit; 10 is no valid digit and never equals one."""
    remainder = _MODULUS - sum(d * w for d, w in zip(digits, weights, strict=True)) % _MODULUS
    return 0 if remainder == _MODULUS else remainder


def _signatures(lines: list[str]) -> set[int]:
    """1-based numbers of name-only lines that sit beside an office-title-only line."""
    titled = {i for i, line in enumerate(lines) if _OFFICE_TITLE.fullmatch(line.rstrip(".:,"))}
    return {
        i + 1
        for i, line in enumerate(lines)
        if _PERSONAL_NAME.fullmatch(line.strip()) and ({i - 1, i + 1} & titled)
    }
